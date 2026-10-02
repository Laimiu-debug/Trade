# 新行情同步 CLI

入口 `scripts/trade_rebuild_sync.py` 调用运行中的新应用本机 API。它与行情页面、任务中心共用同一持久任务，不启动另一份数据库写入器。服务需要先启动；目标默认 `http://127.0.0.1:8011`。

## 提交与恢复

请求文件 `request.json` 示例：

```json
{
  "symbols": ["sh600000", "sz000001"],
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "mode": "incremental",
  "provider": "auto",
  "provider_order": ["baostock", "akshare"]
}
```

```powershell
python scripts/trade_rebuild_sync.py sync --request request.json --job-file job.json
python scripts/trade_rebuild_sync.py status --job-file job.json
python scripts/trade_rebuild_sync.py wait --job-file job.json --timeout 60
python scripts/trade_rebuild_sync.py cancel --job-file job.json
python scripts/trade_rebuild_sync.py retry-failed --job-file job.json --new-job-file retry.json
```

提交前先落盘请求编号。若请求已经送达但回包丢失，执行原提交命令并加 `--resume`，会复用同一幂等请求。任务文件保留原输入摘要、实际数据目录和任务编号；不保存 Cookie、会话令牌或 API key。更换数据目录、URL、请求内容时拒绝沿用原任务文件。

重试只选择原任务中的失败证券，并复用服务端冻结的提供方顺序。重试回包丢失时再次执行同一命令与 `--new-job-file`，也不会重复创建任务。已结束任务不会再次抓取成功证券。取消明确保留已经导入的不可变行情版本。

返回 JSON 包括实际进度、每只证券结果和来源。退出码0表示该命令成功，异步任务可能仍在队列；最终状态以 JSON 为准。失败/部分失败/已取消任务返回2；输入、连接或权限错误返回1。`wait`最长3600秒，到时返回当前状态，服务端任务继续运行。

## 范围

每次请求最多50个证券，交易日期和提供方支持范围沿用页面验证。当前 AKShare 适配器只支持股票日线；明确的指数代码需要 BaoStock 或本地通达信，不能用同代码股票替代。

CLI仅接受显式端口的本机HTTP地址，不发送Cookie到重定向地址。服务器文件上传、远程认证、TLS和历史通达信数据传送脚本仍属于部署/传输验收，本文不声称它们已迁入。当前无自动服务器部署，也未改动用户原行情文件。

`backend/tests/test_sync_cli.py` 使用真实独立本机服务验证会话/CSRF、提交回包中断恢复、只重试失败证券、作用域冲突和实际 CLI 进程提交/查询/取消。测试服务停用后台抓取，不访问外部行情商。
