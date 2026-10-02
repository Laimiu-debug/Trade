# 旧报告与平原档案转换验收（F33）

## 入口与源格式

新版「历史回测 → 研究报告库 → 旧报告与平原档案」接受：

- Final Trade `InMemoryStore.build_backtest_report_package` 导出的 `.ftbt` / ZIP（`ftbt-1.0`）。逐个验证 manifest 的文件长度与 SHA-256，保留 run_request、run_result、plateau_result 和已关联的参数点详情。
- 完整 `BacktestPlateauResponse` JSON。保留原参数点顺序、失败点、源得分、邻域/区域/推荐与相关性字段；不会重新评分或创建平原任务。
- LaimiuTrade 旧打印脚本写入 `.html` 的实际 JSON（username/day/data）以及独立 HTML，可显式存档原件。缺少研究请求、研究结果、清单时精确显示缺失项，不转换成回测。

新版原有 strict v1/v2 报告导入协议未改变。旧资料另存 `legacy_research_reports`（迁移 0065），不生成 BacktestRun、PlateauExperiment 或交易记录。

## 一致性、安全与边界

上传预览不写数据库；确认时重新读取相同文件并校验预览摘要，用户明确接受限制后保存。按原件 SHA 去重；再次导入保持首次记录 ID、保存方式和冻结快照，界面明确提示。列表删除为软删除，显式重导相同原件恢复同一记录；备份/恢复包含原件及转换快照。

原包没有完整冻结市场行情、已知时间和新版执行/代码版本，来源统计属于旧记录，不能据此恢复、续跑或声称新版可重现。保留原请求、成交、曲线和参数值，不猜测缺少值。缺字段只能 archive-only；原件结构无效、SHA 错误或参数点关系矛盾则拒绝。

预算：上传最大 16 MiB，展开最大 64 MiB，单成员最大 16 MiB，最多 2006 文件、2000 平原点、单数组 5000 行，JSON 深度 32。ZIP/ZIP64 EOCD 与实际 central directory 在创建 ZipInfo 前检查，central 最大 1 MiB；拒绝重复/越界/未知路径、链接、特殊/加密文件、尾随内容和分卷。明细文件名、detail_key、点参数及明细请求参数必须吻合，且每份文件通过清单摘要。

原 HTML/Excel 不加载或执行；原件 GET 返回 application/octet-stream、attachment、nosniff、sandbox CSP。新版安全摘要 HTML 只使用转义后的结构字段；所有下载入口都不会自动打开原 HTML/Excel。浏览器结构表格默认分页，JSON 诊断展开后最多显示 100000 字符，完整数据另行下载。

## API

`/api/v1/research/legacy-reports`：

- `POST /preview`：multipart file，返回只读预览。
- `POST /import`：file + expected_preview_sha256 + mode + acknowledge_limitations；CSRF / 幂等保存。
- `GET` 最近 100 条元信息；`GET /{id}` 校验并读取冻结详情。
- `DELETE /{id}` 软删除；不会删除来源研究。
- `GET /{id}/original.bin[?name=...]` 原件二进制下载；名字只能取预览清单。
- `GET /{id}/export.json` 全量转换记录；`GET /{id}/report.html` 安全摘要下载。

## 验证证据

- `backend/tests/test_legacy_reports.py`：21 项通过。使用实际旧导出器构造 fixture，逐字段对照请求/结果/平原/点详情；覆盖缺字段、关联矛盾、数字/日期无效、恶意 ZIP/JSON、预分配元数据预算、ZIP64、确认/去重/备份/恢复、原件下载安全头和只读无任务副作用。
- `frontend/src/rebuild/legacy-report-library.test.tsx`：5 项通过。预览确认、切换文件废弃预览、不可信文本转义、精确点详情、缺字段仅存档和删除确认。
- TypeScript 与架构检查通过。
- `frontend/scripts/smoke-legacy-reports.mjs`：build 796 真实 Edge / API / SQLite，隔离目录 `trade-legacy-report-ui-IuVZqN`。实际旧导出器 → 预览 → 确认 → 原平原与点详情 → 安全原件及摘要下载 → 375px → 去重/刷新恢复 → HTML仅存档/删除，全部通过；没有外部请求或新增回测。

测试命令（仓库根目录，PowerShell）：

```powershell
$env:PYTHONPATH='backend;../final-trade/backend'
python -m pytest backend/tests/test_legacy_reports.py -q
python scripts/check_rebuild_architecture.py
```

前端目录运行 `vitest run src/rebuild/legacy-report-library.test.tsx` 与 `node scripts/smoke-legacy-reports.mjs`。浏览器脚本只启动自己的临时本地服务，不连接或切换用户数据目录。
