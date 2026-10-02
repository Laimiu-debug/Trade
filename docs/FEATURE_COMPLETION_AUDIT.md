# F01–F78 功能承接与验收核对

核对日期：2026-09-26。范围：`backend/trade_app`、`frontend/src/rebuild`、新启动/备份脚本，以及两套原项目的功能基线。本文是代码审查记录，不是“全部功能已完成”的声明。

## 1. 判定方式

以 [FEATURE_SPEC.md](FEATURE_SPEC.md) 每一行及 §4.1、§4.2 的子项为验收边界；原接口/字段来源见 [source-inventory.json](source-inventory.json)。旧 `backend/app`、旧页面和旧脚本仍在仓库内，不能作为新应用已接入的证据。测试也必须确认导入 `trade_app`：例如 `test_ai_chat_api.py`、`test_ai_parameter_proposal_apply.py` 仍属于旧应用，不能证明新 AI API 已通过。

状态说明：

- **已接入**：找到了新应用的服务、页面和对应测试；本次审查未发现该行核心承接项的明确缺口。仍须执行发布回归，不能据此推断所有边界均已通过。
- **部分**：已有可用主链，但右栏列出的原能力或验收条件尚未完整承接。
- **待验收**：实现已找到，但尚缺特定平台或完整发布验证；不等于功能未开发。
- **施工中**：本轮其他任务正在实现；交付前必须重新核查代码与测试，不能提前计为完成。
- **未接入**：仅旧代码存在，未找到新应用可操作入口。

表中路径简称：`B/` = `backend/trade_app/`；`U/` = `frontend/src/rebuild/`；`T/` = `backend/tests/`；`S/` = `frontend/scripts/`。`TR` = [test_trade_rebuild.py](../backend/tests/test_trade_rebuild.py)。测试栏表示**已有测试位置/覆盖方向**，不是本次审查重新运行的结果。

## 2. 实施缺口、数据边界与发布验收分开记录

**实施终核**：本轮发现的F23完整候选/门槛/排名、F33旧报告/旧平原转换、F22第二唯科规则验证及原评分分桶、F73交叉CSV/成交CSV与HTML/全点Excel均已补上实际入口与定向测试。F23/F33/F73各自真实浏览器验收已通过；当前未保留明确的F01–F78核心实施缺口。这里的“已接入”不等于其他平台或真实外源已验收，下述发布门禁仍单独记录。

**已经实现但必须解释的数据与模型边界**：固定研究样本的历史市场成员不可追溯、日线不能还原分钟内真实成交顺序、历史可得时间未知、人工候选设计可能看过后段行情、有限近期资讯/分时源不保证任意历史覆盖。这些不是待补造的数据，也不再作为F26/28–32一直标“部分”的理由。缺失值保持未知或明确拒绝，质量标签随结果保存。

**发布验收**：Windows最终源码包已通过真实启动/计算/导出/备份/切换/退出，见 [发行验收](RELEASE_ACCEPTANCE.md)；macOS app、Linux真实代理/TLS/证书及桌面原生目录选择需各自实测。真实用户旧文件尚未导入是本阶段保留原数据的既定范围，不是测试必须触碰真实账本。原始文件应自行保留，脱敏逻辑档案不是原字节备份。

## 3. 逐项验收表

### 系统、行情与选股

| ID / 状态 | 新代码与页面证据 | 测试证据 | 剩余实施或验收动作 |
|---|---|---|---|
| F01 · 已接入 | [启动器](../scripts/run_trade_rebuild.py)、`B/platform/lifecycle.py`/`launcher.py`、`B/managed_server.py`、生命周期API、`U/lifecycle-control.tsx`；单实例/启动浏览器/受控退出/切换目录。 | 生命周期13项、相关30项与独立managed UI；主任务第二版真实Windows EXE启动/切换/退出已通过。 | 边界：只管理自身进程；外部uvicorn显示unsupported。交互切换512MiB/令牌10分钟/排空60秒明确。最新发布包验收见F76。 |
| F02 · 已接入 | `U/settings-workspace.tsx`、`B/api/settings_service.py`/`settings_routes.py`、0050：七组内联编辑与范围说明，业务组 defaults diff+revision+hash 确认；模拟费用保留滑点/现金缓冲；日历清空保留版本。 | `T/test_group_settings.py` 14 项、日历回归15项、设置 UI5项通过；696 build与隔离真实 `S/smoke-group-settings.mjs` 通过，详见 `docs/SETTINGS_ACCEPTANCE.md`。 | 显示仍为本机范围；来源全局默认已用于实际页面/API执行，旧键只明确迁入。存储不提供清空数据式默认操作；旧资料映射不由设置页面代替。 |
| F03 · 已接入 | 日线适配器、`B/market/provider_health.py`/routes、`U/provider-health.tsx`；来源能力/优先级/实际回退/显式探测；分时另见F05。 | `T/test_provider_health.py` 6项与 `S/smoke-review-health.mjs` 受控远程响应、原真实在线同步smoke。 | 边界：GET不联网，POST探测不入库；空数据不称健康。本地探测范围和股票/指数支持列表明确；外部源实时可用性需发布时复测，不能用mock替代。 |
| F04 · 已接入 | `B/market/symbols.py::normalize_market_symbol` 已统一股票/指数/基金/TDX 板块身份，修正 920/后缀；`U/market-symbols.ts` 接样本选择和图表，`stock-search.tsx` 支持代码/名称/拼音。 | `T/test_market_identity.py` 27 项（含TDX全市场920）及 69 项定向后端门禁由负责任务报告通过；`U/market-symbols.test.tsx` 12 项 UI 测试通过，涵盖分享/估值/模拟选择和错误交易所标记排除。 | 保留原证券别名历史持仓、不改写数据集内容 ID；旧无前缀且可能是指数的档案需显式重新导入才能确定交易所，不能静默与同代码股票合并。 |
| F05 · 已接入 | `B/market/intraday.py`、`intraday_online.py`/服务/持久缓存、`U/intraday-workspace.tsx`、`intraday-chart.tsx`及行情页；本地LC1、在线沪深股票/明确10指数、指定日收盘。 | 负责子任务22后端/3UI、TS/架构与build792真实Edge独立 `S/smoke-intraday.mjs` 流程通过，见 `INTRADAY_ACCEPTANCE.md`。 | 边界：在线源仅近期受支持证券；实际公网探测无指定日/连接失败如实保留。当前未完成分钟排除、量手/元/指数点明确；数据不进入严格策略，不承诺任意历史分钟。 |
| F06 · 已接入 | `B/market/sync_jobs.py`、页面进度/取消；`scripts/trade_rebuild_sync.py` 独立增量/全量CLI；`scripts/trade_rebuild_tdx_bundle.py` 与 `deploy/scripts/sync-trade-rebuild-tdx.ps1` 安全打包/校验/新目录解压。 | 原批量同步/实际CLI进程测试；TDX工具21夹具测试含模拟SSH、源变动/损坏/路径/中断边界；`MARKET_SYNC_CLI.md`、`TDX_BUNDLE_GUIDE.md`。 | 边界：旧根只读、逐文件/归档SHA、完成凭据最后发布，切根明确手动；实际服务器SSH传输和大体积真实源未操作，不冒充部署验收。 |
| F07 · 已接入 | `B/market/diagnostics.py`、`B/research/event_store_*`、0049、`U/event-store.tsx`：缓存诊断、PIT 事件仓统计/冻结任务/版本/回填/取消恢复。 | 负责任务报告事件仓18后端/4UI与真实Edge270点取消恢复、PIT缓存smoke通过；详见 `EVENT_STORE_ACCEPTANCE.md`。 | 事件仓未接计算热路径，不能宣称所有研究执行已自动读此缓存；版本、样本和可得时间不匹配时不得复用旧缓存。 |
| F08 · 已接入 | `B/research/{screener_domain,screener_metrics,screener_service,tdx_universe}.py`；`U/screener.tsx` 阶段池、参数、原因、行情/信号衔接。 | TR：四步漏斗旧样本对照、流通股来源、全市场任务恢复；`T/test_screener_point_in_time.py`：未来尾部不改变历史门槛。 | 以同一冻结样本逐项核验全部原过滤参数；确认缺换手率明确排除/提示、不填零。`len(eligible)>250` 与请求日前末根标记要纳入发布回归。 |
| F09 · 已接入 | `B/research/b1_domain.py`、`b1_service.py`；新增 `watch_pool_service.py`/models、0046、独立 API；`U/watch-pool.tsx` 显式保存共享池的人工/自动快照、参数、顺序、修订和历史。 | TR：B1 原指标/时点/任务；`T/test_watch_pool.py` 18 项、`U/watch-pool.test.tsx` 3 项已通过；`S/smoke-watch-pool.mjs` 已通过真实预览/迁入、指数股票隔离、双窗口冲突保稿、刷新、历史、320px。 | 旧本机记录仅预览后明确迁入，原键保留；每个成员须找到匹配的持久漏斗来源，缺失来源拒绝。自动成员需明确更新，不随打开的筛选记录自动替换。 |
| F10 · 已接入 | `B/research/screener_service.py` 等保存运行；`U/screener.tsx` 分开当前表单、历史与显式复制，最近运行可恢复。 | `S/smoke-screener.mjs`、`smoke-b1-prefs.mjs`；TR：重启读取冻结运行。 | 回归查看旧记录不改当前偏好、切页后结果恢复、显式复制才填表；已保存运行与未保存表单的恢复范围分别标注。 |

### 图表、市场与策略

| ID / 状态 | 新代码与页面证据 | 测试证据 | 剩余实施或验收动作 |
|---|---|---|---|
| F11 · 已接入 | `U/market-chart.tsx`、`chart-indicators.ts`、`chart-research-overlay.tsx`：K 线/量/MA、原 THS 主力/散户独立坐标、分类事件/阶段/小溪冰线/观察标记，保留人工/AI/成交图层。 | 新指标 6 项逐点比对原 `thsVolumeSignal` 及前缀不变/缺失断线；另 12 项证券身份 UI 回归；`S/smoke-chart-layers.mjs` 真实 10 事件、开关、过去视窗隐藏、主题、320px、0 自动研究请求已通过。 | 原图没有 MACD/KDJ/BOLL，不能杜撰为迁移项。事件只读取同数据集的已有研究并明确决策时点；新运行缺少原 A/B/C 类型时显示“观察”，不猜测。精确日期替代旧最近日期匹配是有意修正。 |
| F12 · 已接入 | `B/market/annotations.py` 版本化 CRUD；`U/stock-annotation.tsx` 按规范证券身份保存本机草稿、base revision 冲突比较、人工日期/阶段/形态/决定/备注，与 AI 独立。 | TR：人工标注版本/备份不触碰原数据；`U/stock-annotation.test.tsx` 3 项验证切证券恢复、冲突明确继续、异步结果不串证券；`S/smoke-market-chart.mjs`。 | 发布回归离线/存储额度和跨账户同证券共享语义；草稿留在本机，正式标注进入服务端备份，二者须分别说明。 |
| F13 · 已接入 | `U/market-chart.tsx` 明示首末交易日/包含根数、涨跌、量额、缺额；`stock-ai-overlay.tsx` 选择已校验分析，人工/AI 日期分别画线。 | `S/smoke-market-chart.mjs`；`T/test_ai_generations.py`：未来起爆日拒绝。 | 补 AI 与人工日期重合/在可见范围外/无对应行情的浏览器对照；核对选区收盘到收盘口径与旧版一致，不能把无行情日期移到邻近 K 线。 |
| F14 · 已接入 | `B/research/trend_service.py`、`U/trend-leaders.tsx`；冻结候选、历史、排序与图表打开。 | TR：趋势时间线旧排名与 TDX 任务；`S/smoke-trend-leaders.mjs`。 | 复跑固定样本原参数/排序；`min_hist` 是历史弹性百分比，不是天数，新增说明/预设必须保持该单位。 |
| F15 · 已接入 | `B/research/ladder_service.py`、`U/limit-up-ladder.tsx`；日期、层级、冻结任务、错误和空结果。 | TR：旧涨停高度与全市场样本；`T/test_strategy_symbols.py`；`S/smoke-limit-up-ladder.mjs`。 | 回归沪深北和后缀代码板块涨幅门槛，核查特殊证券/新股规则限制仍明确，不扩展宣称完整交易所规则引擎。 |
| F16 · 已接入 | `B/research/sector_service.py`、`U/sector-capital.tsx`；旧资金代理指标、板块映射/排行/详情、任务记录与来源。 | TR：`test_sector_flow_proxy_...` 旧固定指数对照、TDX 历史；`S/smoke-sector-capital.mjs`。 | 验收页面明确该旧口径是价格/成交额代理，不把它改称真实逐笔主力净流入；检查单位、窗口、映射缺失与缓存/源日期。 |
| F17 · 已接入 | `B/research/abnormal_domain.py`、`abnormal_service.py`；`U/abnormal-scan.tsx` 提供窗口、快照、冷却、停牌/指数回退解释。 | TR：旧 episode/cooling、冻结 benchmark、停牌与回退；`S/smoke-abnormal-scan.mjs`。 | 发布回归固定样本 10/30 日、基准缺失/停牌与规则版本；指数回退必须仍可见。 |
| F18 · 已接入 | `B/research/valuation_domain.py`、`valuation_service.py`；`U/sentiment-valuation.tsx` 五因子、预设、实时带入、盈利覆盖与情景历史。 | TR：五因子公式、PE 来源；`S/smoke-sentiment-valuation.mjs`、`smoke-valuation.mjs`。 | 验收 TTM/动态/静态 PE、亏损和零值；已保存估值情景可由F65一键待采用并冻结；独立即时表单未保存时不会自动伪装来源。 |
| F19 · 已接入 | `legacy_catalog.json`、runtime、0060 `registry_service.py`及设置策略组：16项名称/版本/能力、可修订启用/原生默认入口、预览/hash/新提交门禁。 | 负责任务79后端/16UI、架构、真实Edge默认B1/矩阵入口/停用409/重载375px通过；`STRATEGY_REGISTRY_ACCEPTANCE.md`。 | 边界：停用阻止新提交，不阻断已冻结任务恢复；单股、B1与矩阵各走明确能力路径，禁用项不会静默替换策略。 |
| F20 · 已接入 | `U/research.tsx`参数表单、`strategy-presets.tsx`；`B/research/preset_service.py`及B1纯五参数normalizer贯通固定/TDX/预设；分享版本/差异/明确应用。 | 原预设/AI提案测试；注册表批次79后端/16UI及真实预设分享/差异/应用/B1扫描浏览器通过。 | 边界：旧事件评分只读字段拒绝提交，未知参数不静默过滤；运行实际参数与默认/当前表单分离，参见 `STRATEGY_REGISTRY_ACCEPTANCE.md`。 |
| F21 · 已接入 | `B/research/event_profiles.py`、`event_profile_catalog.json`、`event_profile_service.py`；`U/event-profiles.tsx` 原系统模板只读复制、自定义 CRUD/应用/审计。 | `T/test_event_profiles.py`、`test_wyckoff_research_api.py`；`S/smoke-wyckoff-workflow.mjs`。 | 发布验收 82 条规则/10 维度、冲突保留草稿、历史运行冻结实际模板；严格拒绝未知/越界是相对旧版静默修正的有意变化。 |
| F22 · 已接入 | `scripts/trade_rebuild_lab.py` 与lab模块：6研究策略/四原晨报预设、独立目标拟合、Step1/持仓池回放；另补 `verify-rhythm` 第二固定规则profile、生产节奏/紫转黄逐日诊断、`validate-chart` 原5分桶/CBA/PF及单调检查、完整周期目标/fast28%。 | 负责任务39项lab测试与第二profile8项通过；原常量AST/独立custom与verify谓词、真实预设子进程入场/退出对照。 | 有意修正旧未来去重、共享现金与日线因果退出，冻结 `exact_legacy_equivalence=false`；旧pickle缓存和打印排版不是安全兼容目标。16个原脚本逐项去向见 `STRATEGY_LAB.md`。 |

### 信号、回测与模拟交易

| ID / 状态 | 新代码与页面证据 | 测试证据 | 剩余实施或验收动作 |
|---|---|---|---|
| F23 · 已接入 | `signal_context.py`/formatters、后台scan job/worker和signal workspace：16策略完整store/TDX口径、七通用门槛/模板、相对强弱五权重、矩阵同日池、B1完整事件/排名；1–10策略组合、年龄/确认/时效/延迟/过滤/草稿衔接。 | 新21后端全部通过（原候选/原插件/格式化/PIT/不足不造分/940根B1/API/真实子进程），既有55扫描/工作区/五龙回归通过；主任务新版UI5+全104/build797及真实旧/完整双路径、权重/模板/CSV通过。 | 边界：旧观察记录不补造新上下文；full_market只跳候选预筛，实际生成门槛保留；五龙跳预筛只供观察。TDX超过预算明确选子集；历史成员未经核验，报告过滤不重算矩阵池。见 `SIGNAL_WORKSPACE_ACCEPTANCE.md`。 |
| F24 · 已接入 | `B/research/drafts.py`、`sim_equity_service.py`、`U/research.tsx`/simulation：手数、金额、现金比例和总资产比例；后者冻结完整估值分母、费用、现金上限、wallet revision与quote hash。 | 原研究工作流测试；负责任务报告 `T/test_sim_equity.py` 等19后端/4UI及真实Edge全链路通过，见 `SIM_EQUITY_ACCEPTANCE.md`。 | 总资产比例需要完整且可得的估值，缺价不填零、不使用旧价/未知可得时间做分母；现金比例仍保持独立准确名称。 |
| F25 · 已接入 | `B/research/scan_service.py`、signal workspace、`U/strategy-scans.tsx`/`signal-workspace.tsx`：区间各策略出现日期/次数/分数、union/intersection、过滤、区间价格收益/回撤及历史。 | `T/test_strategy_scans.py`；负责任务报告signal workspace17后端/4UI和真实浏览器通过。 | 区间价格收益/回撤只覆盖选定冻结样本，明示口径；不等于含费用、仓位和组合资金时序的可执行组合收益。篮子T1/T2另外展示。 |
| F26 · 已接入 | `scan_job_{models,service,worker}.py`、0042/共享调度与异步扫描UI；组合 `portfolio_*`/0048及实验/样本外所有权隔离。 | 扫描19新+16原测试、组合原27及扩展真实浏览器、刷新/取消/恢复/历史删除。 | 边界：HTTP同步POST scans明确409引导异步；固定样本与可得时间标签不等同历史全市场证明。任务取消不发布部分成功信号。 |
| F27 · 已接入 | `B/research/basket_domain.py`、`basket_service.py`；`U/signal-baskets.tsx`：手动/扫描创建、版本编辑、删除、T1/T2、显式前瞻数据、概览/明细导出。 | `T/test_signal_baskets.py`：旧毛收益、缺失 pending、T1 当日不能净卖出、后续样本前缀校验；`S/smoke-wyckoff-workflow.mjs`。 | 发布复核旧字段筛选/CSV/Excel 文件；原信号锚点与完整过夜两种持有期分开，固定期毛收益、扣费收益、当前盯市不得混用。 |
| F28 · 已接入 | 单股 `backtest_*`/`walk_forward_*`，组合raw S1–S9/传统14/aligned事件矩阵；0057组合WF、0062组合风险/稳定性/月度/市况/MonteCarlo与指定日持仓/条件计划。 | 单股WF15/相关59、组合原27与多路径smoke；组合WF6新增+2折6阶段20日真实smoke；高级分析新增17后端、旧报告回归36与真实两证券分析/计划/reportv2/删除通过。 | 边界：移动区块Bootstrap不是预测；市况是入场前本证券20根±3%代理，不是全市场。WF训练前缀及选择冻结，测试资金仓位重置；未知次开价格/数量保持null。详见 `PORTFOLIO_EXECUTION.md`。 |
| F29 · 已接入 | `portfolio_domain.py`/service/worker、0048、`U/portfolio-workspace.tsx`；static/daily/weekly/position刷新，卖出与持仓触发、窗口及构池轨迹；aligned矩阵独立路径。 | 组合纯域/API/进程与两证券多路径浏览器，旧NumPy完整日历S1–S9逐日对照、稳定并列、未来冲击前缀不变。 | 边界：输入是冻结研究样本，`selection_membership_unverified`始终保留；不把已实现的样本内池扩展为没有证据的历史全市场池。 |
| F30 · 已接入 | 共享交易费用/单位/T+1；单股/组合gap优先、开盘资金阶段、双触达规则、最大持有、滑点、日内回撤观察后次开部分减仓与日K连续确认清仓。 | 单股/滑点/组合纯域及真实进程；同日盘中卖款不得倒用于此前开盘、T+1/gap顺序及上海日终截止反例。 | 边界：日线预设成交模型，不能重建真实分钟顺序或盘口。两个退出层可独立设置；日终版本 `fixed-sample-causal-portfolio-known-band-exits-v3`（包含旧晨报激活/时间退出/保本参数）需进入最终包。 |
| F31 · 已接入 | 单股 `plateau_*`/0043及组合 `portfolio_experiment_*`/0056：grid/LHS/种子、热图、连通区域/邻域/敏感性/相关性、候选与峰值、执行维度和逐点报告。 | 单股26测试/真实smoke；组合11新增/相关62与4候选16分块、暂停恢复/热图/完整周期指标/报告/导出删除真实通过。 | 边界：完整grid超预算拒绝不截断；原邻居评分顺序错误已修正，不宣称修正后数值仍旧版逐位相等。旧WF为扩展窗，不杜撰rolling固定窗迁移缺口。 |
| F32 · 已接入 | `task_routes.py`/共享调度/`U/task-center.tsx`聚合扫描、同步、回测、平原、组合实验/WF、AI；各任务按capabilities展示操作及深链接。 | task center/compute/backtest/plateau/portfolio experiment/WF测试与真实暂停恢复、成功检查点不重跑、删除保护浏览器。 | 边界：普通单次回测pause/resume=false是明确能力差异；可暂停任务在公布检查点生效，重启后需显式恢复，不能把运行中删除当取消。 |
| F33 · 已接入 | 单股/组合report与0051冻结输入、成交/权益/持仓/决策、ZIP/HTML/Excel；另新增0065 `legacy_report_*` / `U/legacy-report-library.tsx`：原ftbt-1.0及完整旧平原显式预览/校验/导入、原件下载、历史、去重/软删/备份。 | 原报告与三组合路径测试/浏览器；旧格式21后端/5UI、TS/架构和build796真实Edge `smoke-legacy-reports.mjs`通过（`trade-legacy-report-ui-IuVZqN`）。 | 边界：原HTML仅受限原件下载，不执行；字段不足的L打印JSON/HTML只读保留并说明缺项，不猜成交、不创建新回测。新格式16MiB上限明确。见 `LEGACY_REPORT_ACCEPTANCE.md`。 |
| F34 · 已接入 | `U/strategy-scans.tsx`、`research.tsx` 与 `B/research/scan_service.py` 支持单股区间、多策略证据/日期、冻结 K 线及观察/可入草稿区分。 | `T/test_strategy_scans.py`；`S/smoke-wyckoff-workflow.mjs`。 | 回归选定单股的每个命中可打开对应源运行/图表；禁用的策略路径不能被替代为其他策略。旧完整延迟执行状态由 F23 继续承接。 |
| F35 · 已接入 | 参数预设版本/重复检查/分享导入，B1归一化，历史显式回填，平原候选另存预设；服务端观察池/快捷项及本机偏好导出入口。 | 预设/观察池/注册表测试与smoke；主任务本机偏好6UI及真实双标签冲突/主题密度恢复/320px通过。 | 边界：参数收藏在服务端备份；本机偏好包有明确13组allowlist，不包含草稿/凭据或任意旧软件localStorage。 |
| F36 · 已接入 | `B/trading/simulation.py`、`U/simulation.tsx` 委托/撤单/成交、FIFO 批次、幂等提交、事务更新。 | TR：`test_simulation_order_reservation_t_plus_one_and_fifo`；`S/smoke-simulation.mjs`。 | 发布复跑重复请求、撤单/成交竞争、部分卖出后的批次/费用守恒；表格显示账户类型。 |
| F37 · 已接入 | `B/trading/simulation.py` 结算、重置及只读恢复账户；`U/simulation.tsx` 确认影响范围并能恢复。 | `S/smoke-sim-reset.mjs`；TR 模拟账户测试。 | 验收结算重试、重置前恢复点、恢复组仅一个可操作账户、真实账户零变化。 |
| F38 · 已接入 | `U/simulation.tsx` 持仓/可卖/现金/成交/冻结行情估值/快捷卖出；`U/main.tsx` 的真实快照与 `asset-estimate.tsx` 分开。 | `S/smoke-simulation.mjs`、`smoke-snapshot-positions.mjs`、`smoke-asset-estimate.mjs`。 | 回归缺价时总资产为空、未实现与已实现分离；快捷卖出只预填，不自动执行；真实页只记录成交。 |
| F39 · 已接入 | `B/trading/simulation.py::normalized_config`、`sim_models.py`；`U/simulation.tsx` 费用、滑点、现金缓冲；订单冻结配置。 | `T/test_slippage_execution.py`；TR：模拟费用/资金预留；`S/smoke-simulation.mjs`。 | 回归改费率/滑点后旧单和旧成交不变，限价经滑点后仍严格检查；配置更新冲突要可恢复。 |
| F40 · 已接入 | `B/reviews/sim_performance.py`、`B/research/sim_equity_*`、0055、`U/sim-performance.tsx`/`sim-equity.tsx`：FIFO买入/卖出归属、已实现统计；显式冻结行情估值的总资产/指数/回撤与月收益分母。 | 主任务报告买入月份在 `S/smoke-review-health.mjs` 通过；负责任务报告19后端/4UI与 `SIM_EQUITY_ACCEPTANCE.md` 的完整真实Edge流程通过。 | 累计已实现盈亏与总资产曲线保持独立名称；缺初始审计拒绝猜起点，缺价null、未知可得价不做分母，不能将固定估值样本称为每日真实NAV。 |

### 真实账本、净值与目标

| ID / 状态 | 新代码与页面证据 | 测试证据 | 剩余实施或验收动作 |
|---|---|---|---|
| F41 · 已接入 | `B/trading/service.py`、`B/analytics/service.py`、`U/main.tsx` 真实交易 CRUD/作废、序号、审计、重算。 | TR：account ledger revision、superseded projection、round IDs；`S/smoke-rebuild.mjs`。 | 发布复跑同日顺序、更正历史触发全链重算、旧结果不覆盖新修订；无券商下单行为。 |
| F42 · 已接入 | `B/ai/generation_service.py`、`generation_schemas.py`、`B/reviews/attachments.py`；`U/ai-generations.tsx` 上传/选择图片、明确发送、原输出、校正后仅待确认。 | `T/test_ai_generations.py`、`test_ai_generation_api.py`；`S/smoke-ai-generations.mjs` 已通过本地 mock provider 流程。 | 真实视觉模型的识别质量仍需独立样本验收；不能以 mock 正确 JSON 当识别准确率。验收错账户附件、缺费率/金额保持空、用户确认前正式账本不变。 |
| F43 · 已接入 | `B/trading/pending.py`、`U/pending-trades.tsx`：逐条修改/确认、批量逐行结果、重复提示与删除。 | TR：pending batch/duplicate/history；`T/test_ai_generations.py`：OCR 到 pending；`S/smoke-pending-trades.mjs`。 | 发布验证重复确认幂等、失败行不消失、同笔 OCR 重复核对、确认后不可通过 AI 撤回正式交易。 |
| F44 · 已接入 | `B/trading/real_fees.py`、`domain.py` 共用费用；`U/real-fees.tsx`、`main.tsx` 自动计算/实付覆盖及来源。 | TR：real fees auto/manual/pending snapshot；`T/test_slippage_execution.py`；`S/smoke-real-fees.mjs`。 | 同样例跨真实/模拟/回测复跑四种费用与舍入；旧成交保留原配置和实付数，界面不得硬编码新费率。 |
| F45 · 已接入 | `B/trading/domain.py`、`B/analytics/service.py`；`U/trade-rounds.tsx` 进行中/闭合/异常、稳定 ID、胜负统计与来源。 | TR：round IDs/streaks/anomalies；`S/smoke-round-note.mjs`。 | 回归同股同日两回合、超卖隔离、费用归属及交易修订导致拆合的关联警告。 |
| F46 · 已接入 | `B/trading/service.py`、`nav.py`、`U/main.tsx` 初始/入金/出金/作废/备注与份额重算。 | TR：account ledger NAV revision、security invalid NAV、零份额分段。 | 验收超额出金、历史更正、同日现金流在估值前的顺序说明和零净值阻止除零。 |
| F47 · 已接入 | `B/trading/service.py`、`B/ai/generation_service.py`；`U/main.tsx`、`ai-generations.tsx` 人工/OCR 快照、持仓行、版本及核对差异。 | TR：snapshot positions/reconciliation；`T/test_ai_generations.py`：空数不补零、资产接受/还原；`S/smoke-ai-generations.mjs`。 | 发布回归账户/日期版本冲突、OCR 先核对再存；新建资产事实不能经 AI 删除，需到资产页修订。 |
| F48 · 已接入 | `B/research/real_valuation.py`、`U/asset-estimate.tsx` 基于流水与明确样本估算，给价格日期/质量。 | TR：real asset estimate readonly quotequality；`S/smoke-asset-estimate.mjs`。 | 核对缺一只价格时不伪造总资产、不覆盖确认快照；估值缺失与现金已知要分开显示。 |
| F49 · 已接入 | `B/trading/nav.py`、`B/analytics/service.py`、`U/nav-chart.tsx`：份额法、分段、缺估值质量、净值/回撤。 | TR：资金流中性、零净值/零份额、重算升级；`S/smoke-chart.mjs`。 | 发布固定样本逐日对账，新增出入金不改变净值；断估值期间不画成真实零收益。 |
| F50 · 已接入 | `B/trading/targets.py`、`nav.py`；`U/target-nodes.tsx` 默认 50×30%、版本配置、当前点亮/回撤熄灭与首次事件。 | TR：versioned target nodes/projection upgrade；`S/smoke-target-nodes.mjs`。 | 对照资金流后目标金额、倍率修改前后版本与首次耗时；不把新阈值伪装成旧历史。 |
| F51 · 已接入 | `B/analytics/performance.py`、`B/reviews/periods.py`、`U/performance.tsx`、`nav-chart.tsx` 和周期页：日周月/回合/节点。 | TR：confirmed snapshot period performance/missing baseline、period ISO boundary；`S/smoke-performance.mjs`。 | 逐层验证汇总可追溯交易/快照，首周期基线、分母、缺失估值、零交易与净值段边界；日详情所列数据不得使用后续快照回填为当日事实。 |

### 复盘、知识与 AI

| ID / 状态 | 新代码与页面证据 | 测试证据 | 剩余实施或验收动作 |
|---|---|---|---|
| F52 · 已接入 | `B/reviews/service.py`、`attachments.py`、`U/daily-review.tsx`；基线 §4.2 日字段分别保存，图片上传/粘贴/删除、历史、AI 独立草稿。 | TR：daily full manual sections/history、attachment scope/backup；`S/smoke-daily-sections.mjs`、`smoke-review-attachment.mjs`。 | 发布逐字段对照旧日记录，尤其 `reflection` 与 `mistakes` 分开；检查图片失败/删除和离线编辑。旧数据迁入能力仍属于 F70，不由新表单字段完整自动证明。 |
| F53 · 已接入 | `B/reviews/scores.py`、`ai_scores.py`、`B/ai/generation_service.py`；`U/review-scores.tsx`、`ai-generations.tsx` 日六维/逐笔三维/批量/T 组、AI 建议、明确复制到人工最终分。 | `T/test_ai_review_scores.py`、`test_ai_generation_api.py`；`S/smoke-ai-generations.mjs` 已验证接受 AI8 后人工3和评论不变。 | 继续验收复制所选维度（包括 0 分）、重新评分/撤回、批量任一修订冲突整批不写；人工分不能在模型结束时自动修改。 |
| F54 · 已接入 | `B/reviews/plans.py`/`planning.py`、`B/market/calendar_service.py`/`planning_quotes.py`、0047、`U/plan-rehearsal.tsx`；冻结持仓复制、含费用现金预演、缺价未知、本地日历建议及实际快照对照。 | `T/test_review_planning.py` 15 项、AI generation API 3 项、6 项 UI 测试通过；负责子任务报告 `S/smoke-review-planning.mjs` 独立目录真实 Edge/375px 通过。 | 日历只有完整本地覆盖才推荐，不改已有目标日；现金未知不置零，快照/流水不混合、迟到和未来价排除。预演不写入真实事实；边界见 `docs/REVIEW_PLANNING_ACCEPTANCE.md`。 |
| F55 · 已接入 | `B/reviews/periods.py::FIELDS/bounds`、`U/period-editor.tsx`，周目标/成果/资源/节奏/做对做错/策略/认知/标签/统计/交易/回合/历史。 | TR：period ISO week boundary；`S/smoke-period-reviews.mjs`；AI 周草稿测试。 | 复跑跨年 ISO 周、全部原 F/L 独立字段、删除冲突与保存状态；旧记录原自定义起止范围仍需在导入时保留来源。 |
| F56 · 已接入 | `B/reviews/periods.py`、`U/period-editor.tsx` 月边界、体系迭代/目标、节点/交易/回合；AI 月草稿独立接受。 | TR 周期统计；`S/smoke-period-reviews.mjs`；`T/test_ai_generations.py`。 | 回归大小月/跨年、人工正文与刷新统计隔离、缺期初估值和关联回合日期口径。 |
| F57 · 已接入 | `B/reviews/rounds.py`、`U/round-note.tsx`、`B/ai/generation_service.py`：稳定回合摘要、关联日周月、变更警告和 AI 版本。 | TR：round note survives trade revision；`T/test_ai_generations.py`：round draft；`S/smoke-round-note.mjs`。 | 验收摘要在拆合/同日双回合后保留且不错误转绑；AI 接受仅按冻结目标版本修改。 |
| F58 · 已接入 | `reviews/service.py::review_gaps`/reminders；`U/review-drafts.ts`、日/周/月/回合与评分编辑：同步本机缓冲、自动日保存/手动提交、离线读取、base revision对比、迟到请求隔离、跨标签明确选择。 | 提醒2后端/真实日期跳转；新草稿12UI+周期3通过；build792 `S/smoke-review-drafts.mjs` 日快速切页/接口断线/三标签冲突/账户隔离/周月回合/评分0分及评论/320px通过，目录 `trade-review-drafts-ui-QbRytr`。 | 边界：已加载应用中的接口失败可续写；未缓存网页不能在整个服务关闭后首次加载。存储额度失败退到本次应用内存并提示复制/正式保存，不承诺关浏览器后保留内存。 |
| F59 · 已接入 | `B/reviews/tags.py`、`U/sim-review-tags.tsx` 标签 CRUD/成交分配/统计，按模拟账户隔离。 | TR：sim tags scope/history/realized stats；`S/smoke-sim-tags.mjs`。 | 复跑删标签不删成交、多标签重复归属解释、跨账户与未平仓样本不纳入已实现胜率。 |
| F60 · 已接入 | `U/share-card.tsx`、`B/research/stock_history.py`/API汇总同证券跨dataset版本的Screener+B1+Research历史，保留不同交易所身份；文字/长PNG/无行情说明。 | `T/test_stock_history.py` 2项、证券身份UI；主任务报告 `S/smoke-share-card.mjs` 长附言PNG、不透明底部及真实页面通过。 | 保留来源类型/时间/原运行链接；新截图为分享呈现，不将跨版本不同样本历史自动合并成同一计算口径。 |
| F61 · 已接入 | `B/market/news.py`、`B/api/news_routes.py`、0044、`U/market-news.tsx`；沿用旧东方财富→Google RSS，24/48/72 小时、截至时间/复盘日期交集、显式刷新、持久缓存与来源回退。 | `T/test_market_news.py` 18 项通过；真实东方财富返回 150 条且有发布时间；`S/smoke-market-news.mjs` 通过显式联网/缓存失败/空结果/日期参数/文本安全/320px（浏览器用受控响应）。 | 有限近期源不保证整段历史覆盖；未知/未来/窗口外条目排除，抓取时间不等于历史可得时间。GET 仅缓存，POST 才联网。Google RSS 回退已测试，当前外网连通性未单独证明。 |
| F62 · 已接入 | `B/insights/service.py`、`U/insights.tsx` 卡片/标签/删除与按日稳定选择。 | TR：inspiration cards/tags/stable daily；`S/smoke-insights.mjs`。 | 回归空库、同日重复选择稳定、删除后刷新及标签过滤；旧卡片迁入依赖 F70。 |
| F63 · 已接入 | `B/ai/config.py`、`provider.py`、`U/ai-workspace.tsx` 分离 text/vision、环境 key 引用、显式连接测试与缺 key 状态。 | `T/test_ai_workspace.py`：配置/URL/密钥/显式测试；`T/test_ai_preset_api.py`；`S/smoke-ai-workspace.mjs` 已通过。 | 真实远端提供方连通性需用户已配置环境后单独验证；测试仅本地 mock，不产生外部模型费用。检查备份仅含引用、日志不含 key。 |
| F64 · 已接入 | `B/ai/service.py`/provider、`U/ai-workspace.tsx`、`ai-launcher.tsx`、main：单份常驻工作台、全站入口、流输出/停止/恢复，切页继续、账户切换门禁。 | `T/test_ai_workspace.py` 14项；新快捷上下文16后端/7UI、build708；`S/smoke-ai-shortcuts.mjs` 真实main流中切页/返回/停止/重载/账户隔离全部通过。 | 本机草稿按账户+会话隔离且跨标签冲突需明确选择，不随服务端备份。网络发送仍需冻结预览/明确确认；远端模型连通性未以本地mock代替。详见 `AI_SHORTCUTS_ACCEPTANCE.md`。 |
| F65 · 已接入 | `B/ai/contexts.py`/`context_sources.py`/`quick_prompts.py`、0059、`U/ai-quick-prompts.tsx`：页面待采用来源、估值/单股/组合产物、账户/行情/研究、原playbook、本地模板、快捷项版本CRUD/排序/置顶/审计。 | `T/test_ai_shortcuts.py` 16项含SHA冲突/日终截止/损坏/备份；7UI含系统复制/过期revision保稿；真实 `S/smoke-ai-shortcuts.mjs` 通过保存/追加/冻结/详情一键，始终1次明确模型请求。 | 单股/组合只携带摘要及有界成交/权益片段，省略量和120KB限制明确。估值以假设创建时间判定，回测标事后计算；无已保存ID的即时表单不伪装成冻结来源。 |
| F66 · 已接入 | `B/ai/generation_service.py`、`generation_schemas.py`；`U/ai-generations.tsx`、`stock-ai-overlay.tsx`：原结论/置信度/起爆/题材字段、校正、保留/拒绝/撤回/删除与图表选择。 | `T/test_ai_generations.py`；`S/smoke-ai-generations.mjs` 已通过股票流程；主任务确认 AI 图表标记浏览器回归通过。 | 说明分析基于冻结历史，保留记录不自动改变人工启动日或产生实时行情承诺；模型结论质量需真实样本另验。 |
| F67 · 已接入 | `B/research/preset_service.py`、`B/api/preset_routes.py`、`U/strategy-presets.tsx` 读取已完成 AI JSON→不可变差异/哈希→显式应用。 | `T/test_strategy_presets.py`、`test_ai_preset_api.py`；`S/smoke-strategy-presets.mjs`。 | 回归未知/只读参数拒绝、预设修订冲突、重复应用幂等、模型完成时不修改参数；只对已迁入 normalizer 的策略开放。 |
| F68 · 已接入 | `B/ai/service.py` 调用任务/错误码/token；`U/ai-workspace.tsx` 用量及含连接测试的历史；`U/task-center.tsx` 聚合。 | `T/test_ai_workspace.py`：错误脱敏、unknown usage、中断；`T/test_task_center.py`；AI smoke。 | 验收已知 token 部分汇总标 partial、未知不补零；失败/取消/连接测试都可追踪，过滤账户不泄露他账户内容。 |
| F69 · 已接入 | `B/ai/generation_service.py`、`U/ai-generations.tsx` 日/周/月/回合/预研独立草稿，选字段接受、审计、有限撤回。 | `T/test_ai_generations.py`、`test_ai_generation_api.py`；`S/smoke-ai-generations.mjs` 已验证保留其他人工字段。 | 发布核对所有周期目标修订和源账本修订；网络失败、invalid JSON、拒绝草稿不得写入人工正文；不配置 AI 仍可完成手动流程。 |

### 导出、存储与发布

| ID / 状态 | 新代码与页面证据 | 测试证据 | 剩余实施或验收动作 |
|---|---|---|---|
| F70 · 已接入 | `legacy_import`、0052/0058/0061/0064：JSON/SQLite只读识别与脱敏档案；L核心新账户/归档后转换；卡片/费用/目标/来源/AI引用/打印/回合逐项映射；完整final模拟严格新账户迁入及用户选择图片对应。 | 核心27、补充10、新模拟16/附件9；连FIFO回归65后端通过；导入相关UI9；build796真实 `S/smoke-legacy-final-migration.mjs` 完整账本续卖、坏现金拒绝、一次转换、图片字节/正文/账户隔离/刷新/320px通过。 | 数据边界：原文件不改，只存脱敏逻辑/原SHA，不存SQLite原页/密钥。非精确分、坏引用、未知批次/未到可用日/旧pending明确拒绝；初始日期与历史委托费率未知不猜。无图片文件就保持缺失；研究旧档案只读。见 `LEGACY_IMPORT_ACCEPTANCE.md`。 |
| F71 · 已接入 | `B/reviews/markdown_export.py`；日/周/月页链接导出已保存正文、评分、计划、交易/回合；图片明确“未包含，回应用查看”。 | TR：review Markdown saved content/account scope；`S/smoke-daily-sections.mjs`、`smoke-period-reviews.mjs`。 | 验收每个旧字段、空值、中文与图片缺失提示；如增加离线图片包，必须相对路径并带附件校验，不能把当前文本清单称作图片已打包。 |
| F72 · 已接入 | `reviews/pdf_export.py`/离线字体/打印预览；`statistics_export.py` 与冻结权益PDF三图；`scripts/trade_rebuild_export.py`/EXE export子命令，本机独立导出及系统预览；打印署名/目录设置。 | export CLI3、原统计5、新日期/图表3及14项定向门禁；18页长表和11页曲线PDF逐页渲染检查；真实日期轴/冻结权益/两类账户下载及 `smoke-preferences-statistics.mjs` 通过。 | 边界：累计已实现收益和权益分开；缺价断线、旧分摊不足明确排除；超量拒绝不截断。保存打印目录不创建文件夹，CLI不覆盖已有文件；系统默认查看器实际窗口及其他OS需发布验收。 |
| F73 · 已接入 | 账户、篮子、报告、统计及四阶段漏斗/B1选择导出；补 `research_table_exports`：保存信号报告的原交叉CSV、单股/组合成交CSV与HTML、原生/旧平原全点Excel，各详情真实下载按钮。 | 筛选导出11项及真实六次下载、50证券中文PDF11/23页；最后补充导出5后端/2UI，build797真实Edge全部CSV/HTML/Excel下载及交叉CSV未保存过滤改动后逐字节不变通过。 | 边界：冻结阶段/选择顺序/参数/来源可核对，未知保持未知、精度不丢失、危险公式转文本；原21列成交/点详情明确，交叉CSV原质量分与新策略排名不混用。见 `SCREENING_EXPORTS.md`、`RESEARCH_TABLE_EXPORT_ACCEPTANCE.md`。 |
| F74 · 已接入 | 存储预览/校验/复制、受控启动器排空→恢复点→目录切换→新会话/失败回滚；`platform/directory_picker.py`managed原生目录入口。 | 生命周期13项、managed真实UI/EXE切换；picker4测试和页面集成。 | 发布验收：原生桌面文件夹窗口仍需人工选取。边界：外部uvicorn只显示手动说明；交互大小/超时明确，不跨目录覆盖原账本。 |
| F75 · 已接入 | 统一主题/密度/表单表格；业务观察池/来源/快捷项服务端版本化；`U/browser-preferences.ts`/panel集中13组本机偏好格式、SHA、差异预览、按组恢复及跨标签冲突。 | 观察池18后端/3UI、设置14后端/5UI/真实320px；主任务偏好6UI及真实双标签冲突、主题密度恢复与320px通过。 | 边界：偏好包不包含草稿/凭据/任意旧软件键；业务配置归服务端备份，本机草稿恢复单列F58/F64，不混称所有浏览器存储均已备份。 |
| F76 · 待验收 | `scripts/build_trade_rebuild.py`、`scripts/run_trade_rebuild.py`、新PyInstaller/spec与静态资源/字体；不依赖旧app入口打包。 | 主任务Windows中间包已真实通过启动、回测/组合/资源/PDF/备份、目录切换及退出；源码build797通过。 | 最后F23/原导出收尾仍需重捕获源码并最终重打包。macOS app/Linux代理远程部署分别未验收，不能发布为三端完成；未向服务器部署。 |
| F77 · 已接入 | `U/style.css`、分组导航与设置、横向表格容器、模块空/忙/失败/质量状态、键盘焦点；各表单异步隔离与草稿保护。 | build796全UI101项通过；主任务 `smoke-all-pages.mjs` 全导航/设置分组/明暗主题/320px/键盘/详情86检查通过（`trade-all-pages-ui-fAcPX1`）；各功能含离线/缺源/失败/长表定向验收。 | 验证范围为本机Edge和明确脚本用例，不能据此声称所有浏览器/辅助技术/操作系统均已认证；macOS/Linux及正式包在F76/F78单列。 |
| F78 · 已接入（远程部署待验收） | `main.py`会话/CSRF/Host/Origin、上传限制/审计/key引用；新增 `platform/remote_access.py`明确可选HTTPS认证代理信任边界及Caddy/systemd模板。 | 本机安全/上传/脱敏/报告校验；主任务remote12+sync10+task3通过；session有效cookie复用原CSRF/过期拒绝新增两标签回归。 | 发布验收：未在真实Linux/TLS/证书/代理部署验证，默认仍本机，不宣称跨平台已交付。边界：只信明确loopback代理，不能把任意远端直接访问称已获认证。 |

## 4. 解释边界与兼容性检查

以下项目不共用“未完成”勾选框；分别说明其性质，避免把不可得历史当成需要造出的数据：

| 项目 | 性质 | 当前结论 |
|---|---|---|
| 策略目录与实际路径 | 已接功能的能力边界 | 16个descriptor/playbook各自标能力，单股/B1/raw/aligned/传统路径不暗中互换。 |
| 固定研究样本与历史全市场 | 数据边界 | 无历史成员证据时始终标未经核验，不能靠新增计算伪造。 |
| 日线退出与分钟成交 | 模型边界 | 双层退出已实现日线因果近似；盘口和真实触达顺序不在数据中。 |
| 平原和Walk-forward | 已接功能+统计边界 | 已有grid/LHS/区域/邻域/失败点/恢复/候选报告/训练选择冻结；样本内排名不是收益承诺。 |
| 新报告与旧原报告 | 已接功能+兼容边界 | 新单股/组合v1/v2与原ftbt-1.0/完整旧平原有独立校验/预览导入；只有展示HTML或字段不全的旧打印资料保留为只读原件，不执行旧HTML、不猜缺失交易。 |
| 旧资料档案与原件恢复 | 已接功能+数据边界 | L核心/严格补充项、完整旧模拟续用和图片显式对应已接入；无初始日期或图片二进制不猜。脱敏逻辑不是原字节。 |
| OCR流程与识别准确率 | 外部模型验证 | mock证明授权/校验/人工接受；真实模型质量需实际配置验证，不自动确认交易。 |
| 最新源码与发行包 | 发布验收 | Windows需最终源码快照重打包；macOS/Linux与原生窗口单独验收，当前未部署用户服务器。 |

## 5. 已知验证记录与发布闸门

文档核对阶段只读实现，没有重新执行全套测试。随后单独承接资讯实现并执行其测试；以下是同轮实际验证，不扩大其证明范围：

- `npm run build:rebuild` 已通过；证明 TypeScript/打包成功，不证明旧功能全集完成。
- `smoke-ai-workspace.mjs` 已通过本地 mock provider：配置、显式测试、会话、冻结预览、中文流、取消/重载、只读模板复制、自定义删除、320px。无真实外部模型调用。
- `smoke-ai-generations.mjs` 已通过本地 mock provider：股票、日复盘接受/撤回、实际图片附件到 OCR、待确认交易隔离、资产空值校正、AI 评分不覆盖人工、320px。
- `test_market_news.py` 18 项、架构检查和 `smoke-market-news.mjs` 已通过；资讯浏览器测试使用受控响应，另外只读真实东方财富接口确认 150 条均有发布时间。未向正式数据写入测试新闻。
- 负责任务报告 `smoke-report-library.mjs` 已通过；主任务报告 `smoke-wyckoff-workflow.mjs` 已通过模板/扫描/篮子/回测主链。最终发布需保留对应运行输出，不把这里的转述当作重新运行证据。

发布闸门（已验证项与待执行分开）：

- [x] 新扫描/单股与组合平原/Walk-forward/高级分析/资讯/分时/旧资料入口逐项核对；外部与模型数据边界单列。
- [x] 固定样本新旧公式、脚本常量、金额字段和筛选导出对照已有定向测试；有意时点/符号/越界修正记录版本。
- [x] 核心市场→研究→模拟、实盘→净值→复盘、AI预览→发送→人工接受均有真实本地浏览器用例；mock模型不等于真实模型质量。
- [x] 各持久模块有独立新目录备份往返，0064包含迁入模拟和图片；原数据目录未导入，原字节/密钥不进入旧逻辑档案。
- [x] build797 TypeScript与前端构建、全部UI104项及最终86项全页面布局/键盘回归已由主任务通过；F23全部参数展开375px复测通过（`trade-signals-ui-LaqLvf`）。
- [x] 主任务最终 `backend/tests` 全量 **1366 passed / 5 warnings / 423.19s**，无失败或跳过；其中含旧app对照，不能把1366全部描述为新应用测试。前端全137通过/1旧skip，新rebuild104通过；lint无error、113已有warning，TS/build797通过。
- [x] F33原报告转换21后端/5UI/build796真实浏览器，F23完整门槛21后端/5UI/build797真实浏览器，F73补充导出5后端/2UI/build797真实下载均通过并回填；无原数据修改。
- [x] 最新源码最终全量后端回归结果已由主任务确认，F23的21项已包含在最终源码测试状态中。
- [x] TR-020增加独立真实HTTP基准：100,000成交/5,000复盘/10,000摘要、真实回测child+慢HTTP行情下100对分页/保存；87对严格重叠、零错误，p95分别42.99ms/23.45ms。完整规模/机器/原始数据及未证明范围见 `INTERACTIVE_LOAD_BENCHMARK.md`，不外推5000证券全市场性能。
- [x] 最终Windows源码快照 `20260926T083554282331Z` 构建与真实EXE验证通过：启动/资料/研究任务/lab CLI子进程/中文路径/图表PDF/备份/切换/退出；证据 `trade-frozen-smoke-5OlyZ7`。382份源码哈希与当前应用一致，清单和包指纹见 [发行验收](RELEASE_ACCEPTANCE.md)。

真实行情商、用户自配AI、真实SSH/远程TLS、macOS/Linux包、原生选择/默认PDF查看器窗口属于外部或平台验收。未在本轮执行的范围明确保留，不伪造“部署成功”。
