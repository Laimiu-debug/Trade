# 策略统一目录与执行审查

核对日期：2026-09-26。范围为两个原项目、`backend/trade_app/research` 的实际执行器、公开 API 与当前策略目录。本轮不修改旧项目、不删除旧策略 ID、不改写已保存信号或报告，也不以历史阶段文档代替当前代码证据。

## 1. 来源与整合结论

- `final-trade/backend/app/core/strategy_registry.py` 注册 **16 个有效策略**；`strategy_plugins.py` 分别定义候选、信号、排名和执行策略。原文件与当前仓库保留的 `backend/app/core/strategy_registry.py` SHA-256 一致：`e0fbf337b191942d3f4a650ab3d053c2dbbb25e2808ac7b11c36d7a2538d2b97`。源码内 `__removed_*` 定义没有进入有效目录，不因搜索到名称而重新启用。
- `LaimiuTrade` 提供实盘账本、人工/AI 复盘和计划。`backend/app/models.py:101` 的 `next_strategy` 是复盘文本字段；原服务和路由中没有第二个量化策略注册器。因此整合不把复盘计划当作可回测算法，也没有凭空产生一组“重复量化策略”。
- 旧 16 项按 **9 个策略族**组织。共享指标实现不等于相同策略：变体仍保留独立 ID、门槛、模式、参数预设和历史记录。新增 **3 个经典参考策略**使用独立来源与 ID，共 19 项目录；`legacy_catalog.json` 仍仅保存原 16 项。
- 当前 **17 项**支持普通单股观察/扫描、单股回测和传统策略组合；**原 16 项**支持完整旧插件候选扫描。B1 与矩阵插件有各自原生扫描入口，不能据旧 `supports_matrix` 标签推断它们可直接进入单股运行器。
- 原始 S1–S9、aligned 维科夫事件矩阵、传统策略组合是不同执行路径。前两者的引擎名不被伪装为注册策略别名。独立混合波段/图形量价以及人工目标拟合等保留在 [策略实验室](STRATEGY_LAB.md)，不混入生产目录后自动改变旧参数意义。

## 2. 全目录对应与重复关系

“单股”在下表包括普通观察/扫描、单股回测与传统组合；“完整候选”指冻结 store/TDX 候选、模板、七项通用门槛与原插件排名。入口能力不代表任何样本都能产生信号。

| 策略族 | 稳定 ID | 实际差异与入口 |
|---|---|---|
| 维科夫事件 | `wyckoff_trend_v1` | 单股 + 完整候选；原默认，事件质量与确认门槛。 |
| 维科夫事件 | `wyckoff_trend_v2` | 单股 + 完整候选；V2 默认健康/事件门槛不同，不与 V1 合并。矩阵版本/权重仅在相应专用引擎解释。 |
| 维科夫事件 | `score_only_rank_v1` | 单股 + 完整候选；使用 `entry_quality_score`，不是漏斗代理分或指标分。通过观察门槛仍不等于正向买点。 |
| 相对强弱 | `relative_strength_breakout_v1` | 单股 + 完整候选；价格/回撤/量能门槛，完整路径保留五个排名权重。 |
| 主力量能 | `ths_main_force_flip_v1`、`ths_main_force_golden_cross_v1` | 单股 + 完整候选；相同量能基础，分别使用紫转黄和金叉触发，不互作别名。 |
| 主散节奏 | `ths_force_rhythm_v1` | 单股 + 完整候选；周期、振幅、分位、散户过滤等实际 21 参数。旧人工拟合/验证脚本谓词与生产规则分开。 |
| 均线聚合 | `wulong_cluster_v1` | 单股 + 完整候选；形态与候选入池独立，完整路径跳过预筛时不能伪称已完成草稿资格校验。 |
| 矩阵候选插件 | `matrix_signal_v1` | 原生冻结池 + 完整候选；原代理公式及同日 Top N，与真实原始 S1–S9/事件矩阵引擎分开。 |
| 多周期共振 | `b1_mtf_v1` | 原生 B1 + 完整候选；原生五参数与完整路径七项通用事件门槛分开，不进入普通单股运行器。 |
| 趋势为王 | `trend_king_v1` | 单股 + 完整候选；综合模式实际为 A 或 B。 |
| 趋势为王 | `trend_king_limitup_v1` | 单股 + 完整候选；锁定模式 A。 |
| 趋势为王 | `trend_king_rally_v1` | 单股 + 完整候选；锁定模式 B。 |
| 趋势为王 | `trend_king_pullback_v1` | 单股 + 完整候选；锁定模式 C，旧公式要求先满足 A，再检查量比、历史弹性与前日涨幅，属于 A 的子集。多个模式不是独立投票。 |
| 涨停与情绪 | `emotion_limit_up_v1`、`limit_up_arb_v1` | 单股 + 完整候选；前者为当日涨停量能共振，后者为前日涨停后放量，不能只因名称有“涨停”而合并。 |
| 经典趋势规则 | `classic_donchian_breakout_v1` | 单股；此前 55 根通道突破/20 根通道退出的收盘参考版，不是原海龟盘中成交、N 仓位及加仓的完整复刻。 |
| 经典趋势规则 | `classic_sma_trend_v1` | 单股；日线 200 根 SMA 趋势开关，可选缓冲，不是月末 10 月 SMA 多资产系统。 |
| 经典均值回归规则 | `classic_bollinger_reentry_v1` | 单股；20/2 下轨重返及中轨退出的明确自定义规则，不声称等同 Bollinger 原完整系统。 |

目录分组实现：[catalog_presentation.py](../backend/trade_app/research/catalog_presentation.py)。原 16 个名字和版本与保留的原注册器逐项比较，模式 `all/a/b/c` 的归一化值另有断言。

## 3. 本轮已修复的问题

### 3.1 通用扫描 API 静默改变计算口径

**复现：** `POST /api/v1/research/scan-jobs` 接收 `StrategyScanCreate`。修改前该 DTO 没有 `signal_context` 且默认忽略多余字段；即使调用者提交完整候选口径，服务接收到的也是普通单股扫描请求。信号工作区专用路由使用另一请求入口，不受此问题影响。

**修复：** [schemas.py](../backend/trade_app/api/schemas.py:521) 新增 `StrategySignalContext`，保留 `candidate_path`、`window_days`、`universe_mode`。请求、策略项、上下文三层拒绝多余字段；窗口严格整数，`strict` 严格布尔，修订严格整数。参数值接受已有归一化器实际支持的字符串、数值和布尔值，由策略自身校验范围。没有上下文时继续走原普通单股路径。

**验证：** 真实 API 创建完整上下文任务，经 worker 计算后读取冻结扫描和研究记录，确认口径、40 日窗口、跳过预筛模式与布尔参数保留。拼错字段、布尔窗口、字符串 `strict` 明确返回 422。OpenAPI 与 TypeScript 契约已通过生成脚本更新。

### 3.2 新目录项被误认为支持旧完整候选适配

**复现：** `signal_context.context_catalog()` 原来遍历全部 `strategy_catalog()`；完整计算器的 formatter、候选及排名仅实现原 16 个插件，追加经典条目会显示支持但不能按该路径执行。

**修复：** [signal_context.py](../backend/trade_app/research/signal_context.py:48) 将完整候选入口限制为冻结旧目录及未明确禁用该能力的条目。[scan_service.py](../backend/trade_app/research/scan_service.py:48) 和参数归一化边界对不适配策略返回 `SIGNAL_CONTEXT_UNSUPPORTED` / 409，提示使用普通观察/扫描。经典条目的普通单股运行资格保留，拒绝不会被替换为假信号或零分。

**验证：** 三个经典 ID 均可通过普通扫描请求校验、均被完整候选目录排除；直接服务和真实 API 明确拒绝错误适配。原 16 项真实指标计算、模板/排名/TDX/store 对照与未来尾部隔离测试继续通过。

### 3.3 目录能力与文档滞后

**复现：** 原目录运行时转换层仍返回“排名/回测尚待迁移”等早期描述；注册设置把 B1/矩阵以外的任何新条目直接标为单股入口。旧能力标签是原插件能力，不能作为当前执行器的全部能力证明。

**修复：** 新展示层基于实际运行器名单构建 `execution_paths` 和 `current_capabilities`，给出 `family_id/family_name/variant_name/origin`、`availability` 与入场/出场/排名语义。当前 `limitations` 反映真实边界；`legacy_status/legacy_limitations` 保留早期转换层说明，原 `status` 保持客户端兼容。来源目录对象深复制，页面字段不能污染缓存计算参数。没有登记的执行路径返回 `unavailable`，不自动宣称可运行。

旧 [STRATEGY_PORTING_NOTES.md](STRATEGY_PORTING_NOTES.md) 与 [STRATEGY_AUDIT.md](STRATEGY_AUDIT.md) 已明确标为历史阶段记录并指向本文。README 与设置验收文档已更新入口和默认行为。

### 3.4 新增默认与预览确认不完整

**修复：** [registry_service.py](../backend/trade_app/research/registry_service.py:33) 初始默认使用 `enabled_by_default`，旧策略回退 `enabled_in_legacy`。新目录首次启用 19 项且保留 Wyckoff V1 为默认；已保存的启用/默认集合原样保留，新增经典策略只有显式差异预览并保存才开启。恢复默认也是有修订的明确写入，不覆盖旧历史。

目录预览摘要除 ID/版本/schema，还绑定实际 `signal_params/default_params/scanner_params/pool_params`、来源摘要、执行入口与默认状态，修复实际默认值变化而旧预览仍可保存的问题。布尔值不能伪装成修订号；非法预览对象返回领域错误。

**验证：** 保存旧 16 项的版本 7 设置，读取不自动增加三项；预览不写库；确认后才增为版本 8。单独改变可执行默认值而不改 schema/设置修订，旧预览被拒绝。

## 4. 计算与执行核对

| 核对项 | 当前证据及结论 |
|---|---|
| 历史弹性单位 | `trend_king_domain.py:183` 在可见前缀内计算过去最多 250 根中的 20 日涨幅；`min_hist/min_hist_c` 是百分比，非天数。代码里的 `future_idx` 仅访问传入前缀以内，不可据变量名称判为未来函数。当前目录显示单位与 C/A 重叠关系。 |
| 候选同名异义 | `signal_context.py::candidate` 保留 store/TDX 两路；`test_signal_context.py` 与 `test_screener_point_in_time.py` 核对量比、斜率、均线天数及 251 根当时可得历史门槛，未来文件长度不能改变过去入池。 |
| 事件与零分 | `wyckoff_strategy.py` 保留显式零健康分、主要事件自身确认、真实事件日期；风险或非正向观察不能直接创建买入草稿。ScoreOnlyRank 没有被替换为局部代理评分。 |
| 可得时间与入场 | `service.create_run`、扫描 worker、`backtest_domain` 与 `portfolio_domain` 都通过已知前缀计算；当前日/晚到历史不能进入之前开盘决策。已有单股/组合回归含事件确认时间、下一开盘、未来冲击不改前段选择、旧价缺失及上海日终截止。 |
| 出场范围 | 普通旧单股回测使用冻结持有期及风险退出；传统组合另有风险事件退出；原始矩阵和事件矩阵使用各自出场条件。不能把旧 playbook 列出的所有卖出提示当成普通单股已执行的成交。新增经典策略显式 `exit_signal`，由执行器在后续开盘处理，不能混为买入信号。 |
| 身份/涨跌幅 | 股票别名、市场前缀、后缀与北交所 920 已由纯函数统一；20%/30% 板块不再用主板阈值。冻结数据 ID、旧报告和账本记录不做追溯改写。 |
| 排名与相关性 | 策略族分组不是新的统一评分器。相同日线和共享指标的变体相关，不能把四个趋势为王命中解释为四份独立证据；不同算法分数不能直接平均成胜率。 |
| 入出场默认兼容 | 本轮分类与 API 修复不改变旧纯策略公式。原 16 ID、参数预设、报告继续读取；新增执行代码/摘要变化时，待续算任务仍遵守代码版本校验，不宣称旧二进制与新代码可无条件混跑。 |

本轮未发现需要删除旧 ID 或回写历史报告才能解决的问题。对普通单股、完整候选、矩阵引擎的不同范围采用明确入口，避免同名策略被悄悄换成另一组公式。

## 5. 验证记录与边界

本轮实际执行：

```powershell
cd backend
python -m pytest tests/test_strategy_consolidation.py tests/test_strategy_registry.py tests/test_signal_context.py tests/test_strategy_scan_jobs.py -q
```

结果：**63 passed，1 项第三方 dateutil 弃用警告**。其中新增整合回归 11 项，包括原 16 身份/模式、真实入口一致性、缓存不被展示层改写、新旧启用设置、默认参数预览过期、错误完整适配拒绝、通用 API 冻结上下文和参数类型。经典入出场计算由并行实现任务另行验证，不能将本目录回归代替交易行为验收；最终整合门禁由主任务汇总。

`python scripts/generate_rebuild_contracts.py` 已成功生成 OpenAPI/TypeScript 契约。此前功能审计与全量验收为历史基线，本轮变更后的最终全套测试、浏览器布局由本轮主任务记录；本子任务不打包、不提交。

仍需正确表述的数据与研究边界：

- 缺同期板块排名/历史成员时保存缺源标记，不补造全市场背景。明确固定研究样本不能证明消除了历史成分选择偏差。
- 日线不能还原真实分钟触达顺序、排队成交与涨跌停流动性。费用、滑点、下一开盘、T+1 与可见信息的假设需随报告一起解释。
- 原策略默认、经典参考参数和样本内排名没有本轮实证收益保证。新增三种规则是可复算的研究基线，不是经过独立样本验证的盈利结论。收益评估需使用冻结数据、成本敏感性与训练/测试隔离，且不能将看过测试后的再次选参说成独立验证。
