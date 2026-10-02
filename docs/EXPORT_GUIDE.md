# 复盘、统计与独立 PDF 导出

## 页面入口

日、周、月复盘可导出 Markdown、PDF；实盘统计和模拟统计可导出 PDF、Excel、CSV。统计导出使用页面所选周期，模拟统计使用买入日或卖出日归属。Excel 包含来源、口径、汇总和完整明细，CSV 按表导出；金额保留十进制文本，电子表格危险公式前缀会转为文本。

模拟已实现统计支持开始/结束日期，点击“应用统计区间”后同时应用于表格和三种导出。卖出轴过滤成交日；买入轴按持久化FIFO分摊精确筛选买入批次，一笔卖出可能只选中部分数量、成本和盈亏，不按股数猜分摊。没有精确分摊的跨买入日旧记录单列排除。累计已实现图保持卖出日期轴，不能用于推断买入当时已知收益。

“模拟资产与回撤曲线”打开一个已保存报告后，可单独“导出权益曲线 PDF”：包含冻结的权益、回撤、月份收益三张矢量图及完整逐日/月度明细、质量、行情来源和输入哈希。读取旧报告不会按当前账户重新计算；缺失价格断线，旧收盘和不完整月份明确标注。成交统计PDF显示已实现盈亏/月度金额图；实盘周期PDF显示对应收益率图。

设置中的“打印与署名”保存署名及本机导出目录，带版本冲突检测、默认值预览和审计。署名只影响后续导出，不改已保存复盘或旧 PDF。浏览器下载仍使用浏览器目录；本机导出目录供下述明确执行的命令使用，保存设置本身不创建目录。

## 独立导出与系统预览

先启动新 Trade，使用启动器实际显示的端口。命令只连接本机 HTTP 服务，不启动第二套数据库、不读写旧软件目录、不调用外部数据源。

```powershell
python scripts/trade_rebuild_export.py --url http://127.0.0.1:8011 accounts
python scripts/trade_rebuild_export.py --url http://127.0.0.1:8011 review --account <账户ID> --kind daily --key 2025-01-06 --output C:\Exports\review.pdf --open
python scripts/trade_rebuild_export.py --url http://127.0.0.1:8011 review --account <账户ID> --kind weekly --key 2025-W02
python scripts/trade_rebuild_export.py --url http://127.0.0.1:8011 statistics --account <账户ID> --kind monthly --date-basis sell --limit 24 --output C:\Exports\statistics.pdf
python scripts/trade_rebuild_export.py --url http://127.0.0.1:8011 statistics --account <模拟账户ID> --date-basis buy --date-from 2025-01-01 --date-to 2025-01-31 --output C:\Exports\january-purchases.pdf
```

打包后使用 `TradeRebuild.exe export` 代替 `python scripts/trade_rebuild_export.py`，参数相同；源码启动器也支持 `python scripts/run_trade_rebuild.py export ...`。账户 ID 从 `accounts` 查询，不能使用旧账户编号。省略 `--output` 时使用打印设置的绝对目录及自动文件名；目录未配置时明确报错。

`--open` 在成功保存后用系统 PDF 查看器打开。已有文件始终拒绝覆盖；再次导出需换文件名。成功返回文件绝对路径、字节数及 SHA-256。服务中断、错误账户、无效日期、非 PDF 响应或超过 32 MiB 时不写输出。PDF 已保存但系统查看器无法启动时，保留文件并提示手工打开。

## 已验证与限制

- `test_export_cli.py` 3 项：真实 TCP 服务和独立命令进程、中文字体、署名、设置版本/恢复默认隔离、已有文件保护、错误账户/日期不写文件。
- `test_performance_pdf.py` 5 项：完整长表、空/过量数据、实盘精确收益、模拟 FIFO 日期归属、Excel/CSV来源与公式处理。
- 浏览器 `smoke-preferences-statistics.mjs` 已验证实际 PDF/Excel/CSV 下载和模拟买卖跨月归属。长表 PDF 用 Poppler 渲染检查中文、分页、重复表头和来源块。
- `test_statistics_ranges_charts.py` 3 项验证跨买入日的精确部分分摊、日期过滤后的CSV/PDF一致、缺分摊排除、图表缺失断线和冻结权益PDF；浏览器还验证按买入/卖出切换同一区间的成交数量，以及所选冻结权益报告的真实PDF下载。新增权益/周期图表共11页已渲染检查，未裁掉表头、图表或长来源文本。
- PDF 超过 5000 个明细行会明确拒绝；Excel/CSV总明细超过 100000 行会拒绝。缩小周期，或使用完整备份保存原始资料；不会静默截断。
- 系统查看器由操作系统提供。真实默认查看器窗口、macOS/Linux发行包仍须在对应机器验收。
