# 新应用发行与验证

原 `build-exe.ps1` 和 `build-mac.sh` 仍是旧应用入口。新应用使用 `scripts/build_trade_rebuild.py` 与 `packaging/TradeRebuild.spec`，输出到每次独立的 `.release-dist/<UTC时间>/`，不会覆盖已有安装或数据目录。

## 构建

使用安装了 `backend/requirements.txt`、`PyInstaller==6.19.0` 与前端依赖的构建环境，在仓库根目录执行（CI 固定同一打包器版本）：

```powershell
python scripts/build_trade_rebuild.py
```

生成可单独分发的 Windows EXE：

```powershell
python scripts/build_trade_rebuild.py --onefile
```

单文件产物为 `.release-dist/<UTC时间>/TradeRebuild.exe`，无需携带 `_internal` 或前端资源目录。运行时临时展开资源，正常退出后清理；账本、行情和复盘仍写入独立的数据目录，因此换 EXE 不会更换这些数据。默认文件夹模式继续可用；macOS 使用原 app bundle。

已有经过验证的前端构建时可用 `--skip-frontend`。发布包带前端静态资源、离线股票库、中文字体、数据库迁移、策略目录以及用于运行指纹的源码副本。构建后生成逐文件长度/SHA-256 清单；构建成功与所有功能验收是两项不同的门禁。

## 运行模式

- 开发：`python scripts/run_trade_rebuild.py --data-dir <独立目录>`。
- Windows 包：运行输出文件夹内的 `TradeRebuild.exe`，支持同样的 `--data-dir`、`--port`、`--no-browser`。
- Windows 单文件：只复制输出根目录的 `TradeRebuild.exe` 即可，参数和数据目录规则与文件夹版相同。启动时需要展开内置资源，首次打开需稍等。
- 包内命令：`TradeRebuild.exe export ...`、`TradeRebuild.exe lab ...`、`TradeRebuild.exe sync ...`，分别提供独立 PDF 导出、离线研究实验室和本机行情同步客户端。`lab presets` 查看四套预设；各命令的 `--help` 给出参数。CLI 和计算子进程统一使用 UTF-8，支持中文路径；同步客户端连接已运行的本机服务。离线 TDX 压缩包及 SSH 传输仍使用 [独立工具](TDX_BUNDLE_GUIDE.md)。
- `--state-file <独立文件>` 允许测试或另一份安装使用独立的活动目录指针，验收时必须指定，避免改变常用指针。
- 受控服务和计算子进程从当前打包的可执行文件启动，只接受固定的应用 worker 名单。计算进程继续受内存/时限/输出限制；不依赖目标机器另装 Python，也不执行用户传入的任意模块。

首次运行先核对系统设置中的实际目录。备份恢复到新空目录后，通过预检和明确确认切换；切换前保留恢复 ZIP，目标启动失败会尝试回滚。关闭页面本身不会停止服务，Windows 可从右下角 Trade 托盘菜单退出，也可用系统设置中的退出按钮。

### 2026-09-27 通达信目录选择与自动识别

- 受控启动器在启动时扫描本机固定磁盘顶层目录与 Program Files；唯一有效安装自动使用，多个候选留待用户选择。扫描不递归，最多检查 512 个目录，并设 3 秒协作时间预算；未发现时可手动选择任意完整安装目录或 `vipdoc`。
- “系统设置 → 行情来源”提供原生目录选择、扫描候选列表和“使用此目录”。路径只放在应用运行内存，不写设置文件、数据库配置或 Windows 环境变量；下次启动重新识别。显式传入的 `TRADE_TDX_ROOT` 仍兼容开发/批处理场景，应用本身不会创建该变量。
- 选择立即作用于能力检查、日线/分时导入和全市场研究。写入或全市场处理正在执行时拒绝切换；未完成任务保存源目录的 SHA-256 身份指纹，重启后若来源不同便等待重新选择原目录，不混用两个安装的行情。任务中心提供中文提示。
- 检查本地样本不导入、不修改通达信源文件；未下载指定证券 `.day` 时提示先下载盘后日线。安装识别只确认日线目录结构，不能替代每个证券的数据质量检查。

### 2026-09-27 Windows 托盘与可重复启动

- 启动器创建原生 Windows 通知图标，使用发行包内 `trade-icon.ico`；点击打开页面，右键菜单提供“打开 Trade”和“退出 Trade（关闭全部后台）”。服务切换期间禁用打开，退出期间保留“正在退出”状态，完成回收后再移除图标。
- 仅隐藏打包程序自己的控制台，绝不隐藏用户的共享终端。CLI `export/lab/sync` 和 worker 入口不初始化托盘，标准输入输出协议保持不变。Windows 自动化加 `--no-tray`，不依赖桌面会话。
- 原生退出先写入实例校验的退出指令，给服务最多 15 秒完成收尾；超时、指令写入失败或计算卡住，由启动器回收自己的进程树。先处理服务与子进程、锁及发现记录，再移除托盘图标。正在退出的实例不被重复双击复用；等待锁释放后新启动。
- Explorer 重建任务栏时处理 `TaskbarCreated` 并恢复通知图标；托盘初始化失败会回收本次启动并报告错误，不留下只有后台的程序。Windows 可能将新图标放在通知区域折叠区，用户可自行展开或固定。
- 旧包没有 `.trade-running.json` 时，仅在数据锁被占用后检查指定端口起的 20 个本机端口，核对产品、实例 ID、管理能力和数据目录后重开已有页面；未响应的占用会明确报错，不按端口或进程名杀服务。
- 普通双击启动失败显示 Windows 错误框，并写入 `startup-error.log`。`--no-browser` 自动化仅记录错误，避免阻塞测试。
- 页面在启动器首次健康轮询之前就请求退出时，核对退出意图的实例 ID、随机凭证与数据目录，再将服务的正常退出码视为完成；不会误报启动失败或重新弹出浏览器。缺失/过期意图、异常退出码仍按启动失败处理。

实现依据：[Microsoft Shell_NotifyIconW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw)、[通知区域](https://learn.microsoft.com/en-us/windows/win32/shell/notification-area)、[TrackPopupMenu](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-trackpopupmenu)。使用 Python 标准库 ctypes，无新增 GUI 依赖。

验收：`backend/tests/test_windows_tray.py` 在独立目录验证原生图标注册/移除、点击与退出命令、连续三次退出重开、账户持久化、旧包发现及卡死子孙进程回收。提供 `TRADE_TRAY_TEST_EXE=<完整 EXE 路径>` 后，同一端到端用例只复制真实单文件包运行。完整功能/异常终止 smoke 仍使用独立目录和随机端口。

### 2026-09-27 后台生命周期修复

- Windows 启动器为自己的服务创建独立 Job Object，并在服务获得数据锁、启动工作线程前完成归属绑定；绑定失败就停止本次启动。启动器被强制结束时，系统会回收这个服务及其子进程，不按进程名扫描或清理其他软件。
- 正常退出继续沿用等待写入、后台任务收尾和受控关闭；不会因为关掉浏览器标签就结束回测。强制结束程序不保证未保存表单或正在进行的计算完成，已提交数据由 SQLite 事务及已有任务恢复机制保护。
- 同一数据目录的重复双击先核对运行记录、回环地址健康状态和实例 ID，然后打开已运行页面。并发启动使用短期启动锁串行化；旧记录本身不能阻止重启，也不能指向外部网址。托盘版补充了无运行记录旧包的目录身份校验，更新程序仍应正常退出旧版。
- `stdin` 启动握手覆盖进程归属绑定之前的窗口，持续管道作为父进程存活信号。Windows 的强制回收由 OS Job 提供；非 Windows 原生包仍须单独验收。

机制依据：[Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) 与 [Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)。生命周期 Job 不增加计算额度；计算 worker 的内存和进程限制由独立 Job 实施。

Windows 源码虚拟环境的 `python.exe` 会转发启动基础解释器。计算进程以挂起状态创建，先绑定 Job 再恢复执行，防止解释器在绑定前脱离监管。检测到 `pyvenv.cfg` 时允许转发器和解释器共两个进程，共享同一份 Job 总内存预算；基础解释器和冻结包仍限制一个进程。取消、超时或关闭会回收整个计算进程树。`test_compute_process.py` 在真实 Windows venv 中验证计算、内存限制、实际解释器清理和绑定失败拒绝执行；CI 增加了同样的 venv 回归。

回归：`backend/tests/test_launcher_cleanup.py` 包含真实 Windows 子孙进程清理、无关进程继续运行、并发双启动、只结束启动器后释放端口与数据锁、重新启动保留数据及归属绑定失败拒绝启动。`frontend/scripts/smoke-launcher-cleanup.mjs` 针对显式提供的单文件 EXE，在独立目录重复这些关键流程；不会使用默认 8011 或常用数据目录。

## 每份发行包的必须验收项

1. 临时空目录启动，首页/离线股票库/字体能访问，健康检查对应当前启动器的实例。
2. 导入冻结样本，实际计算一个单股回测和一个分块组合任务；结果指纹可读取、子进程能够退出。
3. 保存中文复盘、导出包含中文字体的 PDF，以及完整备份；恢复到独立目录并校验。
4. 从页面切换目录，重新建会话，验证新目录身份；退出后启动器及所拥有的服务均终止。
5. 端口冲突、重复数据目录、任务取消、无网络、数据损坏/迁移失败与回滚。

源代码受控生命周期已有隔离浏览器和真实子进程测试，Windows 中间冻结包也已实际通过启动、计算、中文导出、目录切换和退出；每次源码变更后的最终包仍须单独验收，不能沿用旧包结论。当前包路径与证据见 [发行验收](RELEASE_ACCEPTANCE.md)。macOS/Linux 产物、签名、公证和原生目录选择须在对应环境验证。

`.github/workflows/rebuild-release.yml` 提供 Windows/macOS/Linux 手动构建矩阵，执行契约/架构/令牌检查、构建及冻结包浏览器验收。工作流定义存在不表示远端执行成功；本轮没有触发 GitHub 发布。

Windows 矩阵另外包含单文件模式。`smoke-packaged-rebuild.mjs` 在 `TRADE_BUNDLE_SINGLE_FILE=1` 时，把 EXE 单独复制到中文且带空格的空目录，再验证研究 worker、CLI、资源、导出、目录切换和退出；重新解包启动后核对已保存资料和报告，最后检查 `_MEI` 临时目录已清理。计算子进程保留打包器设置的必要启动字段，同时继续过滤业务密钥，并维持单 worker 的资源限制。机制依据 [PyInstaller 6.19 启动器环境约定](https://pyinstaller.org/en/v6.19.0/advanced-topics.html#environment-variables-used-by-frozen-applications)。

远程模式需要显式配置公共 HTTPS 域名、代理令牌和 Caddy 认证，后端仍只监听 loopback。真实 Caddy 本机 TLS 链路已通过隔离测试，公开域名证书和 Linux 服务部署另行实测，见 [远程部署](REMOTE_DEPLOYMENT.md)。
