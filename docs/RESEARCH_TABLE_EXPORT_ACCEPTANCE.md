# 研究表格导出（F73）

## 原范围对照

| 原入口 | 新入口 | 保存的范围和字段 |
|---|---|---|
| `CrossValidatePage.handleExport` 的 filteredResults CSV | 信号工作区 → 保存/读取报告 → 导出交叉验证 CSV | 仅该不可变报告中通过过滤的 per_symbol，所有行而非屏幕分页；保留旧 8 基础列和每策略 7 列，另附报告/扫描/过滤/日期/实际价格端点/版本说明 |
| `BacktestPage.buildTradesOnlyCsv/Html` 历史成交 | 历史回测 → 已完成单股/组合 → 导出逐笔成交 CSV / HTML | 导出全部原始成交顺序与证券、数量、价格、费用、信号/已知/决策/执行日期和原因。新版成交粒度是买卖执行腿，不伪装成旧已完成交易回合 |
| 同上，旧导入历史成交与点详情 | 旧报告与平原档案 → 结构详情 / 参数点详情 → 导出原成交 CSV / HTML | 保留旧 21 列顺序、原回合日期和数量/价格/盈亏；收益率×100为百分比。缺少质量分等字段留空，未沿用旧界面补零行为 |
| `BacktestPage.buildPlateauHistoryWorkbookBuffer` | 收益平原 → 已结束实验 → 导出平原全点 Excel；旧档案 → 导出原平原全点 Excel | 全部参数点，包括成功、失败、取消、无效点。旧格式保留 PlateauSummary / BasePayload / PlateauPoints / PlateauRegions / PlateauCorr / PlateauNotes、旧全点列与源 JSON；新版另含实际采样ordinal、状态、参数、配置、轴值、输入/结果摘要和版本 |

旧交叉结果代表点采用每策略最高 entry_quality_score，平分时保留最早观察日；共振强度为重叠策略数×去重出现日期数。原式最高/平均质量分仅保留为兼容列，明确不代表跨策略统一排名。价格变化采用新版已修正的区间首末可得收盘口径，不把区间外未来价格带回报告。缺价格和缺分留空。

已保存信号报告的 CSV 不受表单尚未保存的日期/过滤修改影响。原研究健康/事件分须通过报告冻结的 run 摘要校验；不重跑策略。导出页面不自动创建报告、回测或委托。

## 下载及完整性

所有端点 GET，只读取已有快照，返回 attachment、nosniff、private/no-store 及 sandbox CSP。HTML 的标题、元信息、全部单元格均转义，无脚本/外链。CSV带 UTF-8 BOM并做公式前缀防护；Excel字符串明确设为文本单元格，超过32000字符分片保存在LongText，避免Excel静默截断或拆分处重新解释公式。

单股结果核对 result_sha256；组合复用已验证的输入/分块链/最终摘要；平原核对冻结输入、每点输入/结果，以及已完成排名快照与点指标的一致性；旧原件与转换报告复用 F33 的摘要校验。GET 不重新计算策略、排名或收益。

原生未完成任务无法导出成交表。平原仅结束状态（成功/失败/取消）可导出全部点；状态和失败项保留，不把不完整实验当成功。旧 archive-only 记录不具备结构导出能力。

## 路由

- `/api/v1/backtests/{id}/trades.csv`、`trades.html`
- `/api/v1/research/portfolios/{id}/trades.csv`、`trades.html`
- `/api/v1/research/plateaus/{id}/export.xlsx`
- `/api/v1/research/legacy-reports/{id}/trades.csv`、`trades.html`（可选 detail_key，仅允许原包已校验明细）
- `/api/v1/research/legacy-reports/{id}/plateau.xlsx`
- `/api/v1/research/signal-workspace/reports/{id}/cross-validation.csv`

## 自动验证

- `backend/tests/test_research_table_exports.py`：5 项通过，覆盖真实旧导出器字段/全点、过滤集合/区间日期、真实单股/组合/平原工作进程、成功失败混合点、摘要损坏拒绝、CSV公式/HTML/Excel长文本分片。
- F33旧格式解析连测共 26 项通过。
- `frontend/src/rebuild/research-table-downloads.test.tsx`：2 项通过；与旧档案UI连测7项通过。
- TypeScript与架构边界检查通过。
- 浏览器脚本 `frontend/scripts/smoke-research-table-downloads.mjs` 在 build797 的真实 Edge、独立临时数据目录中通过：实际 worker 生成单股/组合和平原记录；下载并解析单股/组合 CSV/HTML、原生和旧平原全点 Excel、旧 21 列成交和参数点详情；已保存交叉报告的 CSV 在表单修改交易所但未保存后逐字节不变。没有新增研究 POST，也没有页面脚本错误。隔离目录为 `trade-table-exports-ui-eMerSg`，未使用共享服务或用户账本。
