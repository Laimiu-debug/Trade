# 回测改造需求与实施清单（全市场滚动 + 每日事件库持久化）

> 文档状态：Implemented v3（M1/M2 已落地，M3 待补齐）  
> 更新时间：2026-02-20  
> 说明：本文件按当前代码状态回填，未勾选项为剩余缺口。

## 1. 背景与问题
- 全市场回测需避免“按 `date_to` 静态截面选池再回放”的前视偏差。
- 威科夫事件需要按交易日持久化与增量更新，避免重复计算波动。
- 前端全市场回测需要任务化，避免同步请求超时。

## 2. 现状快照（基于当前代码）
### 2.1 已具备能力
- [x] 回测任务接口：`POST /api/backtest/tasks`、`GET /api/backtest/tasks/{task_id}`。
- [x] 滚动模式枚举：`daily` / `weekly` / `position`。
- [x] 滚动候选池能力：`trend_pool` 与 `full_market` 都支持按刷新日构池并映射 `allowed_symbols_by_date`。
- [x] 任务进度结构：`current_date`、`processed_dates`、`total_dates`、`percent`、`warning`。

### 2.2 原缺口闭环状态
- [x] `mode=full_market` 已打通 `daily` / `weekly` / `position` 三种滚动模式。
- [x] 全市场路径已移除 `Top1500(score+ai_confidence*20)` 前置截断。
- [x] 前端 `full_market` 已统一走任务接口，不再走同步长超时分支。
- [x] 每日威科夫事件库已落地（SQLite + 索引 + 版本键 + lazy fill + 回填接口）。

## 3. 本轮目标（Definition of Success）
- [x] G1：`full_market` 支持 `daily` / `weekly` / `position` 三种滚动，且逻辑可复现。
- [x] G2：回测统一任务化，前端不再依赖长超时同步请求。
- [x] G3：回测与信号计算优先读取事件库，缺失时增量计算并回写。
- [x] G4：`run_id` 在回测中仅作为参数模板来源，不再作为固定未来股票池。

## 4. 统一需求口径（本轮冻结）
### 4.1 滚动刷新规则
- `daily`：每个交易日刷新候选池。
- `weekly`：每周首个交易日刷新候选池，其余交易日沿用。
- `position`：首日刷新；后续仅在“卖出后产生可补仓空位”时，于下一交易日刷新。

### 4.2 `run_id` 语义
- 回测中 `run_id` 只提供“筛选参数模板”。
- 若 `run_id` 缺失或无效，回退系统参数模板并写入 `notes` 告警。
- 禁止把历史某次 run 的静态股票列表直接用于整个回测区间。

### 4.3 全市场候选池构建口径
- 删除 `Top1500(score+ai_confidence*20)` 预截断。
- 刷新日按市场范围全量标的执行威科夫事件判定。
- `max_symbols` 在刷新日候选池形成后再应用；非刷新日沿用上一池。
- “系统保护上限”仅作为资源熔断兜底，触发时显式告警。

### 4.4 任务化与进度口径
- 回测统一走任务接口（`trend_pool` 与 `full_market` 一致）。
- 进度口径统一为交易日维度：`current_date`、`processed_dates`、`total_dates`、`percent`。
- 长区间默认给出 warning 提示（缩短区间/分批回测）。

## 5. 待办 A：全市场滚动模式落地
### A1. 后端实现（`backend/app/store.py`, `backend/app/core/backtest_engine.py`）
- [x] 将 `mode=full_market` 纳入滚动池构建路径，和 `pool_roll_mode` 打通。
- [x] 抽离“刷新日 -> 当日候选池 -> 日期映射 `allowed_symbols_by_date`”通用逻辑（已复用 `_build_allowed_symbols_by_date`）。
- [x] 删除全市场 Top1500 前置截断逻辑。
- [x] 结果 `notes` 写入滚动模式、刷新次数、空池天数、保护上限触发等信息。
- [x] `position` 模式按“卖出后下一交易日”生成刷新计划并可复现。

### A2. 前端实现（`frontend/src/pages/backtest/BacktestPage.tsx`, `frontend/src/shared/api/endpoints.ts`）
- [x] `full_market` 改为提交 `/api/backtest/tasks`，移除同步长超时依赖。
- [x] 统一任务进度展示：百分比、当前交易日、处理进度、warning。
- [x] UI 已补充 `full_market` 下 `daily` / `weekly` / `position` 三种滚动语义与耗时差异提示。

### A3. 测试补齐
- [x] 后端已覆盖 `full_market + daily/weekly/position` 组合用例。
- [x] 后端已补“系统保护上限触发”语义测试（触发时 `notes` 可见告警）。
- [x] 前端“全市场任务化提交 + 轮询成功/失败 + 进度展示”测试已补齐。

## 6. 待办 B：每日威科夫事件库持久化
### B1. 存储模型（SQLite）
- [x] 库文件落地（默认 `~/.tdx-trend/wyckoff_events.sqlite`，支持环境变量覆盖）。
- [x] 新建表 `wyckoff_daily_events`（含核心事件字段、评分字段、版本字段、时间戳字段）。
- [x] 复合唯一键：`(symbol, trade_date, window_days, algo_version, data_source, data_version, params_hash)`。
- [x] 索引：`(trade_date, symbol)`、`(symbol, trade_date)`。

### B2. 计算与写入策略
- [x] 增量构建：默认仅补齐缺失交易日（命中记录跳过）。
- [x] 支持按日期区间批量回填（可指定市场、窗口、最大标的）。
- [x] 算法版本/数据源版本/参数口径变更可通过版本键 miss + 重建实现。
- [x] 写入失败重试机制已实现（SQLite 操作失败自动重试）。

### B3. 读取策略（回测/信号）
- [x] 回测与信号优先读库，缺失时动态计算并 lazy fill 回写。
- [x] 提供“只读库 / 允许回写”开关（环境变量控制）。
- [x] 版本键不匹配按 miss 处理，不复用旧记录。

### B4. 数据质量与可观测性
- [x] 已增加空事件、异常分值、日期错位质量检查（lazy fill + backfill）。
- [x] 已暴露基础统计：命中数、未命中数、lazy-fill 写入、回填写入、耗时与扫描规模。
- [ ] 命中率/缺失率已暴露；诊断视图仍待补齐。

## 7. 验收标准
### 7.1 功能正确性
- [x] `daily`/`weekly`/`position` 回测用例已覆盖，结果可复现性有自动化验证。
- [x] 全市场回测不再依赖 `date_to` 单点截面选池。
- [x] `run_id` 缺失/无效时，行为符合“参数模板回退 + 明确告警”。

### 7.2 性能目标
- [ ] 首次全量构建与后续增量耗时对比基线待补。
- [ ] 同参数二次回测耗时下降 50% 的量化验收待补。

### 7.3 稳定性
- [ ] 任务中断后“断点续跑”机制待补（当前为可重跑增量）。
- [x] 事件库缺失场景可自动补齐，不影响回测完成。
- [x] 同键重复读取不会批量刷新历史 `updated_at`（已有测试覆盖）。

## 8. 里程碑建议
- [x] M1（先可用）：全市场三种滚动 + 全任务化 + 进度展示稳定。
- [x] M2（提性能）：事件库落地 + 回测读库 + 增量回填。
- [ ] M3（提可运维）：版本化重建、诊断页、统计报表（命中率/覆盖率/缺失率）。

## 9. 默认决策（如无异议按此执行）
- [x] D1：`position` 触发刷新条件采用“任一卖出导致空位”，包含止损/止盈/超时/事件离场。
- [x] D2：事件库支持多 `window_days` 并存（由版本键区分）。
- [x] D3：事件库版本键纳入数据源维度（TDX/AkShare）。
- [x] D4：回测结果写入“参数快照摘要（hash + 关键字段）”用于审计追溯。

## 10. 非目标（本轮不做）
- 不新增策略因子与模型重训。
- 不修改交易撮合规则（沿用现有回测执行规则）。
- 不做多账户/分布式任务调度。
