# F06 通达信离线上行工具

本工具替代旧 `deploy/scripts/sync-tdx-data.ps1` 的硬编码主机/目录和覆盖解压流程。旧脚本只保留作来源对照；不要用它更新新版数据目录。本轮只运行了本机小夹具与模拟 SSH 包装测试，没有连接用户服务器或读取用户通达信数据。

## 文件与保证

- 独立 Python 3.10+ 标准库 CLI：`scripts/trade_rebuild_tdx_bundle.py`，无应用、worker、第三方库依赖。
- 参数化 PowerShell 包装：`deploy/scripts/sync-trade-rebuild-tdx.ps1`。
- 源目录可为 TDX 安装目录、`vipdoc` 或已有的扁平数据根。只读 `sh/sz/bj/lday/*.day`，显式 `--include-minute` 才读 `minline/*.lc1`。证券文件名限定所属交易所可选前缀加六位数字。
- 辅助文件仅 `T0002/hq_cache/shs.tnf`、`szs.tnf`、`bjs.tnf`、`base.dbf`。源为 `vipdoc` 时从其父目录寻找 T0002。其他文件不打包。
- ZIP64，256 KiB 分块；逐文件 SHA-256/长度、清单 SHA-256 和整个归档 SHA-256。没有把数 GB 分钟数据一次载入内存。
- 默认总原始数据 ≤16 GiB、单文件 ≤100 MiB、文件数 ≤30000，可显式放宽到硬上限 32 GiB / 512 MiB / 100000。已涵盖旧脚本约 6.8 GB 总分钟源的容量目标，但本轮没有实际抓取/传输 6.8 GB。
- 先检查 ZIP/ZIP64 尾部、文件数和中央目录 ≤64 MiB，再加载 ZIP 索引；清单 ≤32 MiB。随后验证所有声明路径、大小、摘要与实际内容。预算也在源枚举阶段执行。
- 拒绝目录穿越、绝对路径、反斜线/隐藏 NUL、重复项、符号链接、硬链接、Windows reparse point、特殊或加密 ZIP 项、未知文件、损坏和源读取时变动。创建结束前再次检查选中文件身份/大小/修改时刻。
- 输出归档必须在源目录外且不存在；验证全部成功后以不覆盖方式发布。目标解压目录也必须不存在。全部文件验证、写入、flush/fsync 完成后，才原子发布 `tdx-bundle-complete.json`。当前文件系统须支持原子硬链接发布；不支持时明确失败，不降级覆盖。
- 解压中途失败可能留下一个**没有完成凭据的新目录**，供管理员检查；不递归删除它，不触碰原数据，不将其当作可用根目录。换一个新目标重试。
- 完成凭据仅证明运输字节完整性，不证明 OHLC/交易日/可得性正确；仍由行情导入和研究规则校验数据质量。

## 本机创建与校验

以下均为示例路径，先建立独立输出目录。打包期间应停止会改写选中文件的通达信下载；发现变动就重新打包。

```powershell
$created = python scripts/trade_rebuild_tdx_bundle.py create `
  --source 'D:\TDX\vipdoc' --output 'D:\tdx-exports\bundle-20260926.zip' `
  --include-minute | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $created.ok) { throw 'TDX 打包失败' }
$archiveHash = $created.result.archive_sha256

python scripts/trade_rebuild_tdx_bundle.py verify `
  --archive 'D:\tdx-exports\bundle-20260926.zip' --sha256 $archiveHash
if ($LASTEXITCODE -ne 0) { throw 'TDX 校验失败' }
```

日线模式不加 `--include-minute`。预算参数各子命令均接受：`--max-total-gib 16 --max-file-mib 100 --max-files 30000`。总数据预算不包括 ZIP 元数据和清单；它们有独立上限。

`verify` / `extract-new` 强制传入由本机创建结果取得的可信归档 SHA-256。不要把远端重新计算出来的散列当作传输前的可信值，否则无法证明上传的就是本机这个归档。

## 解压到新目录

远端或本机均使用同一个独立 CLI：

```bash
python3 /srv/trade-project/scripts/trade_rebuild_tdx_bundle.py extract-new \
  --archive /path/to/bundle.zip --sha256 '<本机记录的64位SHA-256>' \
  --target /srv/tdx-data-20260926
```

目标父目录须已存在，目标 `/srv/tdx-data-20260926` 必须不存在。结果目录内直接包含 `sh/lday` 等证券路径和可选 `T0002/hq_cache`，另有清单和完成凭据。建议使用 `tdx-data-日期` 命名，而非 `vipdoc`：应用把名为 vipdoc 的目录视为原通达信安装结构，会在其父目录寻找辅助文件。

完成后管理员检查 JSON 凭据（`root_switch=manual_only`、归档摘要、清单摘要、文件数、总长度）、抽样行情以及旧数据的恢复方式，再显式设置 `TRADE_TDX_ROOT` 指向新根，并通过正常启动器重启。工具不改环境变量、应用配置、旧目录、服务或进程。

## 可选 SSH 上传包装

先在服务器项目中部署这个独立 CLI，确认 SSH Host alias 与主机密钥已正确配置。包装脚本不会自动接受未知/变化的主机密钥，也不会部署代码。

```powershell
.\deploy\scripts\sync-trade-rebuild-tdx.ps1 `
  -Source 'D:\TDX\vipdoc' -HostAlias 'tdx-remote' `
  -RemoteProject '/srv/trade-project' -RemoteTarget '/srv/tdx-data-20260926' `
  -IncludeMinute
```

支持 `-LocalPython` / `-RemotePython` 与上述三个预算参数。Host 必须是配置过的简短 alias；项目/目标须为绝对 POSIX 路径，可含空格，但不接受 shell 字符或 dot segments。

包装器先本机打包和自检；SSH/SCP 均带 `StrictHostKeyChecking=yes`、`BatchMode=yes`、`ConnectTimeout=10`。远端 `mktemp` 建立唯一私有临时目录，仅上传其中的归档，再调用 `extract-new` 进行完整远端校验与新目录解压。各阶段退出码和完成凭据均校验；失败返回非零，不输出完成。清理只针对本次随机临时文件/空临时目录，不递归删除远端任何内容。失败时保留本机归档路径以便检查。

## 验收

`python -m pytest backend/tests/test_tdx_bundle.py -q`

小夹具覆盖日线/分钟/四辅助文件、源只读、真实 ZIP64 尾部（测试降低阈值以避免分配数 GB）、逐文件与归档散列、预算、已有文件/目录保护、源并发写入、路径与重复/链接/损坏拒绝、元数据先验限额、磁盘写入及完成凭据发布失败、CLI JSON/非零退出、模拟 SSH/SCP 的成功/上传失败/恶意 alias 与严格主机密钥选项。本轮实际 21 项通过。未执行公网 SSH/SCP、未实际传输大型源，也不把模拟远端测试称为真实服务器验收。
