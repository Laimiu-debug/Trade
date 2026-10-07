# Trade 重构开发 TODO

版本：1.2 · 2026-09-26 · G0/G1/G2 主流程和大部分 G3 功能已落地，当前处于最后兼容核对与 G4 发布验收。依据 [当前实施总览](IMPLEMENTATION_STATUS.md)、[F01–F78 核对](FEATURE_COMPLETION_AUDIT.md) 及各专项验收更新；不再沿用早期“仅 G2 首段”的阶段描述。

勾选规则：`[x]` 表示该 R 项的实现与下列范围内验收已有证据；不是所有平台发行认证。`[ ]` 可为部分已实现或待最终验证，每项注明确切缺口。原始条目保持不删；真实行情覆盖不足、模型假设、用户尚未迁入旧数据与代码未实现分别记录。

入口：[重构文档](REBUILD_GUIDE.md)；功能 ID 见 [FEATURE_SPEC.md](FEATURE_SPEC.md)。阶段表示依赖顺序，不是工期承诺。每项勾选前记录实现路径、验收证据和残留限制；文档编写完成不等于开发任务完成。

实施约束见 [技术要求](TECHNICAL_REQUIREMENTS.md)。M0–M7 保留为完整能力分组；实际优先按 TR-025 的 G0 基础、G1 账本流程、G2 研究流程组织任务，再进入 G3 全能力扩展和 G4 完整验收。可以先实现后续模块中业务流程必需的最小部分；每组功能的最终验收范围不变。

## M0 · 冻结基线与建立工程

目标：让“包含原来全部功能”可逐项核对。

- [ ] R001 冻结两个原仓库的提交与依赖锁文件；保存 173 接口、28 业务路由、16 策略、独立脚本与前端持久化清单。核对原始清单中的工作区改动标记。
  - 进度 / 未验部分：提交、接口、路由、策略和持久化清单已见 [来源基线](source-inventory.json) / [基线核查](archive/DOCUMENTATION_CHECK.md)。待补两个原仓依赖文件的独立归档摘要，不能把提交号清单当作依赖锁定完成。
- [ ] R002 为 F01–F78 建可追踪验收条目；逐项补原请求 / 响应、UI 行为与导出字段样例。
  - 进度 / 未验部分：[F01–F78 核对](FEATURE_COMPLETION_AUDIT.md) 已有新代码、测试与边界映射；仍需将每项原请求/响应、UI 和导出样例索引收齐，并按最终版本复核，不能仅按“已接入”全勾。
- [x] R003 建最小固定行情、交易、快照、复盘、OCR 假响应与报告包样本；不包含用户真实密钥。
  - 已验：固定行情/账本/快照/复盘、模型假响应及真实旧导出器报告夹具已落地；见 [功能核对的测试列](FEATURE_COMPLETION_AUDIT.md)、[旧报告验收](LEGACY_REPORT_ACCEPTANCE.md)。
- [x] R004 固定精度、费率、T+1、回合、NAV、日期轴和统计版本规则，建立新旧差异表。
  - 已验：规则和原版差异见 [策略审查](archive/STRATEGY_AUDIT.md)、[组合执行](PORTFOLIO_EXECUTION.md)、[模拟估值](SIM_EQUITY_ACCEPTANCE.md)；账本 NAV/零份额/费用边界由 [新应用测试](../backend/tests/test_trade_rebuild.py) 覆盖。
- [ ] R005 创建新架构目录、类型 / 格式工具、前后端锁文件、测试与构建工作流；确认 Node / Python 范围。
  - 进度 / 未验部分：新目录、前端 package-lock、CI、构建及运行时范围已有；见 [打包说明](REBUILD_PACKAGING.md)。后端 requirements 仍含范围/未固定的传递依赖，完整后端锁文件和同版本复建门禁尚未闭合。
- [x] R006 记录 API 契约与数据库初版迁移；建立 OpenAPI → TS 生成流程。
  - 已验：实际路由生成 OpenAPI/TS、摘要和 CI 漂移检查已接入；见 [API 契约](API_CONTRACTS.md)。开放字典仍按 unknown/record 表达，不虚称全部响应字段严格类型化。
- [ ] R007 登记 Windows / macOS / Linux 目标环境与性能基准机，记录待验平台。
  - 进度 / 未验部分：[Windows实测环境与硬件](INTERACTIVE_LOAD_BENCHMARK.md) 和 [发行环境](RELEASE_ACCEPTANCE.md) 已记录；macOS/Linux仍需对应环境及平台基准验收。
- [ ] R008 固定六个业务域及公共设施的所有权 / 公开接口，CI 检查越界与循环依赖；验证 import 无副作用（TR-002–TR-004）。
  - 进度 / 未验部分：[架构检查器](../scripts/check_rebuild_architecture.py) 与 CI 已校验域边界/循环/纯模块依赖；仍缺覆盖全部模块的 import 无文件/网络/线程副作用专项验收，静态 AST 检查不能代替。
- [x] R009 将任务编入 G0–G4 业务流程门槛，准备两个端到端样例与证据目录（TR-025/TR-026）。
  - 已验：[技术要求 G0–G4](TECHNICAL_REQUIREMENTS.md) 已定义流程门槛；账本 [smoke-rebuild](../frontend/scripts/smoke-rebuild.mjs)、研究 [扫描工作流验收](SCAN_JOB_ACCEPTANCE.md) 提供独立样本与失败场景。

出口：可启动空壳；没有原数据写入；来源基线可复核；所有功能都有承接位置。

## M1 · 公共基础、设计系统与数据保护

依赖：M0。优先解决旧审查 T01/T03/T04/T05/T06。

- [x] R101 独立数据根、单实例锁、受控进程启动 / 退出、日志与健康检查（F01/F74/F78）。
  - 已验：已接生命周期/单实例/健康检查；见 [功能核对 F01/F74](FEATURE_COMPLETION_AUDIT.md)、[受控启动与切换](REBUILD_PACKAGING.md)。外部 uvicorn 明确不支持受控重启。
- [x] R102 SQLite 事务、迁移、Decimal / 整数金额、审计、revision 与幂等基础设施（F02/F78）。
  - 已验：事务、迁移、金额规则、审计/修订/幂等已有 [新应用账本测试](../backend/tests/test_trade_rebuild.py)，各新增域沿用同一基础。
- [x] R103 会话、同源 / Host / Origin、CSRF、凭据引用、上传资源边界（F63/F78）。
  - 已验：见 [功能核对 F63/F78](FEATURE_COMPLETION_AUDIT.md)、[远程边界](REMOTE_DEPLOYMENT.md)；默认本机、上传限额、CSRF 和环境密钥引用均接实际 API。
- [x] R104 完整备份包、预检、临时恢复、回退点，包含所有已存在实体与附件（F70）。后续每新增实体同步更新 manifest 和往返验证。
  - 已验：一致性数据库与附件/冻结样本归档、临时恢复预检及切换前恢复点已实现；见 [旧资料/备份验收](LEGACY_IMPORT_ACCEPTANCE.md)、[打包生命周期验收边界](REBUILD_PACKAGING.md)。
- [ ] R105 唯一设计令牌源、类型 / CSS / AntD / ECharts 适配、浅深色、减少动态（F75；D01）。
  - 进度 / 未验部分：JSON→CSS 令牌生成、主题和减少动态已用在新页；见 [设计系统](DESIGN_SYSTEM.md)。原要求的类型/AntD/ECharts 统一适配及所有硬编码视觉值清查尚未逐项验收。
- [x] R106 单一 AppShell、导航组、账户范围、股票搜索占位、任务 / AI 入口、错误页（D02）。
  - 已验：统一 rebuild AppShell、账户范围、证券搜索、任务与常驻 AI 已接入；见 [功能核对 F04/F32/F64/F75](FEATURE_COMPLETION_AUDIT.md)、[AI 快捷入口](AI_SHORTCUTS_ACCEPTANCE.md)。
- [ ] R107 组件样例页：按钮、输入、表格、指标、状态、弹窗、评分、节点、图表（D03）。
  - 进度 / 未验部分：各业务页已有实际组件与交互测试；仍缺独立覆盖所列全部组件/状态的样例页及视觉验收，不将业务页等同组件目录。
- [x] R108 统一配置 schema 与 UI 偏好，明确服务端实体和 localStorage 边界（F02/F75）。
  - 已验：服务端组设置、版本/默认差异和本机偏好 allowlist 分离；见 [设置验收](SETTINGS_ACCEPTANCE.md)、[功能核对 F02/F35/F75](FEATURE_COMPLETION_AUDIT.md)。
- [x] R109 持久化任务基础：提交、执行、事件流、取消、心跳、中断状态；CPU / I/O 分离（F32）。
  - 已验：耐久状态/尝试围栏/取消/中断、受限计算进程与 AI I/O 已落地；见 [扫描任务](SCAN_JOB_ACCEPTANCE.md)、[事件仓](EVENT_STORE_ACCEPTANCE.md)、[AI 快捷入口](AI_SHORTCUTS_ACCEPTANCE.md)。
- [ ] R110 任务 CPU / I/O 并发、内存、临时磁盘、超时和排队预算；未实测机器默认单 CPU worker，进程传文件引用（TR-016/TR-017）。
  - 进度 / 未验部分：共享单 CPU 调度、进程内存/时间/文件输入输出和队列限额已实现，见 [扫描资源预算](SCAN_JOB_ACCEPTANCE.md)。TDX/在线 I/O 仍各有后台工作流，跨所有领域的统一 I/O、临时磁盘和峰值资源验收尚未闭合。
- [x] R111 进度限频、产物校验与发布、任务公平性 / 背压、保存与查询负载基准（TR-018–TR-020）。
  - 已验：检查点、摘要校验发布、轮转/队列背压及 [TR-020真实HTTP基准](INTERACTIVE_LOAD_BENCHMARK.md) 通过；100对查询/保存零错误，87对回测child与在途慢行情严格重叠，查询p95 42.99ms、保存p95 23.45ms。只证明记录中的机器与负载，不外推最大规模SLA。
- [ ] R112 业务库 / 行情 / 产物 / 附件分层、引用回收、实际 SQLite 配置、锁等待与资源指标（TR-021–TR-024）。
  - 进度 / 未验部分：SQLite WAL/timeout、数据库/行情/附件分层和引用保护已存在；仍需统一引用回收流程、锁等待/峰值内存/磁盘指标及规模验收。见 [技术要求 TR-021–024](TECHNICAL_REQUIREMENTS.md)。

出口：主题无需刷新；非法恢复不修改数据库；损坏状态不被空状态覆盖；任意外部 Origin 无法读写账户。

## M2 · 账本、资金与模拟执行

依赖：M1。优先解决 T10；为后续复盘提供统一事实数据。

- [x] R201 账户与证券身份模型，真实 / 模拟 / 回测范围隔离（F04/F38）。
  - 已验：见 [证券身份与账户核对 F04/F38](FEATURE_COMPLETION_AUDIT.md)、[模拟估值验收](SIM_EQUITY_ACCEPTANCE.md)；同号股票/指数不合并。
- [x] R202 手动交易 CRUD / 修订、费用分项和覆盖、重复核对（F41/F44）。
  - 已验：见 [功能核对 F41/F44](FEATURE_COMPLETION_AUDIT.md) 与 [账本费用/修订测试](../backend/tests/test_trade_rebuild.py)。
- [x] R203 导入批次与待确认编辑 / 逐条 / 批量确认，用固定假识别结果验证（F43；AI 在 M6）。
  - 已验：见 [功能核对 F42/F43](FEATURE_COMPLETION_AUDIT.md)；固定模型响应进入待确认，确认幂等与失败行保留已有 API/UI 验证。
- [x] R204 FIFO、可卖持仓、稳定回合 ID、异常交易处理和回合重建（F36/F38/F45）。
  - 已验：见 [功能核对 F36/F45](FEATURE_COMPLETION_AUDIT.md)、[模拟 FIFO 与估值](SIM_EQUITY_ACCEPTANCE.md)；异常与已确认事实分开。
- [x] R205 初始 / 出入金、每日快照与持仓明细、修订与资产核对（F46/F47，OCR 在 M6）。
  - 已验：见 [功能核对 F46/F47](FEATURE_COMPLETION_AUDIT.md)、[快照/现金预演验收](REVIEW_PLANNING_ACCEPTANCE.md)。
- [x] R206 NAV、份额、净值段、缺失估值与零净值规则、历史更正重算（F49）。
  - 已验：见 [功能核对 F49](FEATURE_COMPLETION_AUDIT.md) 及 [NAV/份额/修订测试](../backend/tests/test_trade_rebuild.py)。缺估值保持缺失。
- [x] R207 模拟委托 / 撤单 / 成交 / 结算 / 重置、资金与费用配置（F36–F39）。
  - 已验：见 [功能核对 F36–F39](FEATURE_COMPLETION_AUDIT.md) 与对应 simulation/reset 浏览器脚本；无真实券商执行。
- [x] R208 完成端到端建账 → 交易 → 快照 → 净值 → 备份恢复；对同一确认 / 订单重复提交（F70）。
  - 已验：账本/快照/NAV/备份往返、重复确认与订单幂等见 [新应用端到端测试](../backend/tests/test_trade_rebuild.py)、[功能核对 F36/F43/F70](FEATURE_COMPLETION_AUDIT.md)。
- [x] R209 账户输入版本、事实修订 / 审计 / 重算请求同事务，合并重算范围并支持崩溃恢复（TR-005–TR-007）。
  - 已验：事实 revision/审计/重算排队同事务，范围合并和重启恢复已实现；见 [账本与 superseded 投影测试](../backend/tests/test_trade_rebuild.py)、[当前实施总览](IMPLEMENTATION_STATUS.md)。
- [x] R210 一致输入计算、结果整批发布、迟到任务版本校验；保持人工快照和回合摘要（TR-008）。
  - 已验：一致读事务与整批投影发布，输入更新后旧结果拒绝发布，人工回合摘要独立保留；见 [投影/回合回归](../backend/tests/test_trade_rebuild.py)。
- [x] R211 统计新鲜度与版本状态、失败保留上一完整结果、导出版本标记（TR-009）。
  - 已验：fresh/recalculating/stale/failed、旧完整结果与导出版本可读；见 [功能核对 F41/F49/F51](FEATURE_COMPLETION_AUDIT.md)、[统计导出](EXPORT_GUIDE.md)。
- [x] R212 抽取交易域纯规则内核，明确时钟 / 成交适配接口；先固定费用、现金、持仓与订单样例（TR-010–TR-012）。
  - 已验：共享费用、金额/数量、T+1/FIFO 纯规则用于模拟和研究；时点适配与原版差异见 [组合执行](PORTFOLIO_EXECUTION.md)、[模拟估值](SIM_EQUITY_ACCEPTANCE.md)。
- [x] R213 与最小日复盘 R303/R306 联调通过 G1，验证更正历史后的跨页面重算和完整备份恢复（TR-025）。
  - 已验：历史更正、日复盘/回合摘要保留、跨页面投影及备份恢复已有 [新应用流程测试](../backend/tests/test_trade_rebuild.py)；编辑恢复另见 [草稿验收](REVIEW_DRAFT_ACCEPTANCE.md)。

出口：账本与模拟账户各自完整闭环；异常不污染正常统计；原数据不变；金额可逐笔核对。

## M3 · 完整复盘、统计与知识卡片

依赖：M2。优先解决 T09/T13。

- [x] R301 总览、资产 / NAV / 回撤与日周月收益、统计口径说明（F40/F51）。
  - 已验：见 [功能核对 F40/F51](FEATURE_COMPLETION_AUDIT.md)、[模拟权益](SIM_EQUITY_ACCEPTANCE.md)、[统计图表/导出验收](EXPORT_GUIDE.md)。累计已实现盈亏与总资产分开。
- [x] R302 节点配置、点亮 / 熄灭、首次耗时和版本历史（F50）。
  - 已验：见 [功能核对 F50](FEATURE_COMPLETION_AUDIT.md) 和其中节点版本/投影升级测试及真实页面验证。
- [x] R303 日复盘全部字段、截图粘贴 / 管理、六维手动评分、原逐笔扩展字段（F52/F53 手动部分）。
  - 已验：见 [功能核对 F52/F53](FEATURE_COMPLETION_AUDIT.md)；原独立日字段、附件、六维/逐笔评分和历史均已接入。
- [x] R304 次日关注列表、风险预案、持仓预演、昨日计划对照；无行情时允许手录价格（F54）。
  - 已验：完整范围与无行情/缺现金/版本边界见 [持仓预演验收](REVIEW_PLANNING_ACCEPTANCE.md)。未知日历要求手填，已存日期不隐式修改。
- [x] R305 周 / 月 / 回合复盘、相关日 / 周 / 月摘录、摘要保留（F55–F57）。
  - 已验：见 [功能核对 F55–F57](FEATURE_COMPLETION_AUDIT.md)、[复盘草稿验收](REVIEW_DRAFT_ACCEPTANCE.md)。
- [x] R306 自动保存状态、快速切日期 / 切路由、断网恢复草稿、冲突比较（F58）。
  - 已验：真实浏览器验证快速切页、接口断线、账户隔离与跨标签冲突，见 [复盘草稿验收](REVIEW_DRAFT_ACCEPTANCE.md)；存储失败的内存降级范围明确。
- [x] R307 缺复盘与缺快照提醒；标签管理 / 绑定 / 统计（F58/F59）。
  - 已验：见 [功能核对 F58/F59](FEATURE_COMPLETION_AUDIT.md) 中提醒日期跳转、标签范围/统计与删除保留成交测试。
- [x] R308 灵感卡片、标签、每日温故（F62）。
  - 已验：见 [功能核对 F62](FEATURE_COMPLETION_AUDIT.md) 及卡片/标签/按日稳定选择 API 与浏览器证据。
- [x] R309 把所有新增复盘实体、回合摘要、附件加入备份清单，做完整往返（F70）。
  - 已验：数据库实体与内容哈希附件统一备份；附件/回合/AI/旧资料往返见 [功能核对 F52/F57/F70](FEATURE_COMPLETION_AUDIT.md)、[旧资料验收](LEGACY_IMPORT_ACCEPTANCE.md)。

出口：无 AI / 无网络也能完成日周月复盘；快速切页不丢字；统计可追溯到交易与快照。

## M4 · 行情、选股、图表与市场研究

依赖：公共任务设施、账户上下文与固定数据接口。G2 可以先实现本组一个数据源 / 策略及必需页面，再按本组清单扩展全部能力。

- [x] R401 行情 provider 协议、通达信 / akshare / baostock / 东方财富原能力映射与回退（F03）。
  - 已验：适配能力、明确回退和显式健康探测见 [功能核对 F03](FEATURE_COMPLETION_AUDIT.md)；GET 不主动联网，外部可用性与实现状态分开。
- [x] R402 证券搜索、代码标准化、日线 / 指数 / 指定日期 / 分时，缓存与数据质量（F04–F05）。
  - 已验：规范身份/搜索/日线/指定日/分时缓存已接，见 [分时验收](INTRADAY_ACCEPTANCE.md)、[功能核对 F04](FEATURE_COMPLETION_AUDIT.md)。在线分时只承诺列出的近期范围。
- [x] R403 增量 / 全量同步、存储诊断、事件仓统计 / 回填、来源与截至日展示（F06–F07）。
  - 已验：见 [同步 CLI](MARKET_SYNC_CLI.md)、[TDX 工具](TDX_BUNDLE_GUIDE.md)、[事件仓验收](EVENT_STORE_ACCEPTANCE.md)。事件缓存尚未接所有研究热路径，GET 不触发回填。
- [x] R404 四步漏斗、独立股票池、B1 观察池、偏好与历史恢复（F08–F10）。
  - 已验：见 [功能核对 F08–F10](FEATURE_COMPLETION_AUDIT.md)、[策略/B1 预设验收](STRATEGY_REGISTRY_ACCEPTANCE.md)；观察池显式保存及版本冲突已有真实 UI 证据。
- [x] R405 统一 K 线 / 分时 / 图层、标注、选区统计、人工与 AI 日期对照（F11–F13；旧 AI 记录用样本）。
  - 已验：见 [功能核对 F11–F13](FEATURE_COMPLETION_AUDIT.md)、[分时验收](INTRADAY_ACCEPTANCE.md)，图层逐点对照、日期与证券隔离已有测试。
- [x] R406 趋势龙头、涨停梯队、板块资金、异动快照与诊断（F14–F17）。
  - 已验：见 [功能核对 F14–F17](FEATURE_COMPLETION_AUDIT.md) 对应独立服务、真实页面和原函数字段对照；缺基准明确诊断。
- [x] R407 情绪估值所有因子 / 预设 / 报价边界，新闻窗口与过期态（F18/F61）。
  - 已验：见 [功能核对 F18/F61](FEATURE_COMPLETION_AUDIT.md)；原因子/预设/亏损不适用、新闻时间窗口/失败缓存均有实现和测试。
- [x] R408 16 个策略注册、schema / 默认参数 / 能力标记 / Top N，事件判定配置（F19/F21）。
  - 已验：见 [策略注册验收](STRATEGY_REGISTRY_ACCEPTANCE.md)、[策略审查](archive/STRATEGY_AUDIT.md)；新提交门禁与已冻结任务执行分开。
- [x] R409 信号年龄、Active / Expiring、上下文，转模拟委托草稿与数量换算（F23/F24）。
  - 已验：[信号工作区](SIGNAL_WORKSPACE_ACCEPTANCE.md) 新增21项完整候选/门槛/排名原版对照；build797真实浏览器通过完整上下文双策略、事件模板修订、权重、冻结CSV和375px展开参数。最后全后端1366项与新UI104项通过。
- [x] R410 从账本 + 行情估算资产，明确估算质量；给计划与复盘补价格上下文（F48/F54）。
  - 已验：见 [功能核对 F48](FEATURE_COMPLETION_AUDIT.md)、[持仓预演验收](REVIEW_PLANNING_ACCEPTANCE.md)。估算不改确认快照，缺价不补零。
- [ ] R411 每个 provider 的固定响应适配测试与真实连通性分别记录；策略做第一轮基线对照。
  - 进度 / 未验部分：固定响应、显式探测和策略对照已有；见 [功能核对 F03/F05/F61](FEATURE_COMPLETION_AUDIT.md)、[策略审查](archive/STRATEGY_AUDIT.md)。仍需汇总每个提供方在最终环境的实测日期/成功或失败证据，不能用 mock 代替真实连通性。
- [ ] R412 行情 manifest、证券集合 / 日历 / 复权版本、不可变数据引用与缺失预检（TR-013）。
  - 进度 / 未验部分：内容寻址行情、来源/复权声明、冻结证券与缺失预检已实现；见 [扫描任务](SCAN_JOB_ACCEPTANCE.md)、[持仓预演本地日历](REVIEW_PLANNING_ACCEPTANCE.md)。全任务统一 manifest 中的日历/证券集合版本仍需专项核对；未知历史成分只标无法核验，不列为待补造数据。
- [x] R413 事件 / 可得 / 采集时间及修订的区分，严格模式与未知数据质量标记；构造未来资料验证隔离（TR-014）。
  - 已验：晚到/未来/未知可得性反例与前缀隔离见 [扫描任务](SCAN_JOB_ACCEPTANCE.md)、[事件仓](EVENT_STORE_ACCEPTANCE.md)、[信号工作区](SIGNAL_WORKSPACE_ACCEPTANCE.md)。

出口：市场 → 筛选 → 信号 → 图表 → 模拟委托路径可用；数据源 / 日期 / 近似数据状态清晰。

## M5 · 回测、交叉验证与参数研究

依赖：M4 中对应的行情 / 策略能力；G2 先验证一个原有策略。计算核心先对照再优化，不因拆模块改变信号语义。

- [x] R501 参数归一化、只读字段、参数收藏、版本、分享码与差异应用（F20/F35）。
  - 已验：参数归一化/分享差异/版本化预设及 B1 五参数共用合同，见 [注册与 B1 预设验收](STRATEGY_REGISTRY_ACCEPTANCE.md)、[功能核对 F35/F67](FEATURE_COMPLETION_AUDIT.md)。
- [x] R502 单日 / 区间交叉验证、共振、策略池过滤、历史与组合回测（F25/F26）。
  - 已验：区间/交并集/冻结过滤报告与真实组合执行已接；见 [扫描任务](SCAN_JOB_ACCEPTANCE.md)、[信号工作区](SIGNAL_WORKSPACE_ACCEPTANCE.md)、[组合执行](PORTFOLIO_EXECUTION.md)。价格区间表现不冒充组合收益。
- [x] R503 待买篮子创建 / 自动生成 / 编辑 / 批量删除、T+1/T+2、成分股与统计（F27）。
  - 已验：见 [功能核对 F27](FEATURE_COMPLETION_AUDIT.md) 的 CRUD/自动来源/原毛收益/费用后与 pending 测试；两种持有期锚点明确区分。
- [x] R504 回测矩阵 / 传统路径、数据与参数冻结、滚动 / 持仓触发构池（F28/F29）。
  - 已验：原 S1–S9/传统/对齐事件三路径和冻结滚动池已完成；见 [组合执行验收](PORTFOLIO_EXECUTION.md)。
- [x] R505 入场 / 延迟 / 交易单位 / 费用 / 滑点 / 止损止盈 / 持有期 / 双层回撤完整对照（F30）。
  - 已验：完整执行参数、延迟/共享费用与双层回撤及有意修正见 [组合执行](PORTFOLIO_EXECUTION.md)、[策略迁移差异](archive/STRATEGY_PORTING_NOTES.md)。日线执行不宣称复原分钟顺序。
- [ ] R506 回测 / 平原暂停继续、检查点、重启恢复、取消竞态与结果发布（F32）。
  - 进度 / 未验部分：平原/组合实验的检查点暂停恢复、重启、取消/迟到结果围栏已通过；见 [功能核对 F31/F32](FEATURE_COMPLETION_AUDIT.md)。普通单次回测明确 pause/resume=false，仍不满足本条未限定范围的“回测暂停继续”，保留能力差异待收口。
- [x] R507 收益平原 grid / LHS 扫描、热图、失败点、点详情、区域中心、邻域 / 敏感性 / 相关性与候选参数（F31/F35）。
  - 已验：单股/组合 grid/LHS、区域/邻域/相关性、失败点、候选参数与全点详情已接；见 [组合执行](PORTFOLIO_EXECUTION.md)、[全点导出验收](RESEARCH_TABLE_EXPORT_ACCEPTANCE.md)。
- [x] R508 单股策略扫描 / 信号回溯、历史参数回填（F34）。
  - 已验：单股区间命中、源运行/图表和显式历史参数回填见 [功能核对 F34/F35](FEATURE_COMPLETION_AUDIT.md)、[信号工作区](SIGNAL_WORKSPACE_ACCEPTANCE.md)。
- [x] R509 原报告包构建 / 导入 / 查询 / 删除、Excel 全参数交易 / 资金曲线、报告验证（F33）。
  - 已验：新 v1/v2 冻结包与旧 FTBT/平原只读转换、校验、历史/删除及结构导出已验；见 [旧报告验收](LEGACY_REPORT_ACCEPTANCE.md)、[研究表格导出](RESEARCH_TABLE_EXPORT_ACCEPTANCE.md)、[组合报告](PORTFOLIO_EXECUTION.md)。旧 HTML 仅安全存档。
- [ ] R510 16 策略 × 代表数据边界的差异报告；检查未来数据引用、停牌 / 无行情和最后交易日。
  - 进度 / 未验部分：策略纯函数/候选/时点及矩阵多条对照已有 [策略审查](archive/STRATEGY_AUDIT.md) 与测试；仍缺覆盖16策略×全部列出代表边界的统一差异报告，不能把部分夹具逐字段相等推广为完整矩阵。
- [x] R511 风险指标、稳定性、市况分组、蒙特卡洛、Walk-forward、资金曲线当日持仓 / 次日计划；保留高级分析开关与不足样本状态（F28/F33）。
  - 已验：风险/稳定性/市况代理/MonteCarlo、单股及组合 WF、当日持仓/次日条件计划已完成；见 [组合执行验收](PORTFOLIO_EXECUTION.md)。不足样本与未来未知价保留空值。
- [ ] R512 规范化输入与完整缓存标识，随机种子 / 依赖版本固定、数据更新后的旧实验重放（TR-013–TR-015）。
  - 进度 / 未验部分：规范化输入、代码/数据/结果摘要及种子已有检查；[扫描恢复](SCAN_JOB_ACCEPTANCE.md) 与 [组合执行](PORTFOLIO_EXECUTION.md) 会拒绝变更代码/损坏样本。完整依赖锁定和跨数据更新/环境升级的旧实验可重放验收尚未闭合，只读历史不等于可重跑。
- [x] R513 联调一个数据源 / 策略 → 回测 → 模拟交易 → 结果核对，通过 G2；验证共享规则与计算时保存可用（TR-010–TR-020/TR-025）。
  - 已验：研究→扫描/篮子/模拟草稿与共享规则、真实计算期间保存测试见 [扫描任务验收](SCAN_JOB_ACCEPTANCE.md)、[模拟权益/草稿验收](SIM_EQUITY_ACCEPTANCE.md)。持续负载 p95 单列 R111。

出口：原回测核心功能全部可达；任务中断不会变成成功；同版本可复现；每项预期算法差异有书面说明。

## M6 · AI 与截图识别

依赖：M3/M4/M5。解决旧审查 T08/T18。

- [x] R601 统一提供方、文本 / 视觉配置、测试、密钥引用、缺配置降级（F63）。
  - 已验：见 [功能核对 F63](FEATURE_COMPLETION_AUDIT.md) 与 AI workspace 测试；只保存环境变量名，外发需用户明确测试/执行。
- [x] R602 会话、流式输出、停止、历史、错误与用量；全局抽屉接同一服务（F64/F68）。
  - 已验：见 [AI 快捷入口与流式工作台验收](AI_SHORTCUTS_ACCEPTANCE.md)、[功能核对 F64/F68](FEATURE_COMPLETION_AUDIT.md)。中断不自动重发。
- [x] R603 页面上下文、提示词预览 / 编辑、快捷提示、本地 playbook（F65）。
  - 已验：显式采用页面来源、冻结上下文、提示词/快捷项/只读 playbook 见 [AI 快捷入口验收](AI_SHORTCUTS_ACCEPTANCE.md)。
- [x] R604 个股分析结构、记录查询删除、输入数据版本（F66）。
  - 已验：见 [功能核对 F66](FEATURE_COMPLETION_AUDIT.md)；结构结果/冻结输入/版本化接受与删除，未绑定账户的行情分析可用。
- [x] R605 参数建议白名单、只读校验、差异、版本冲突和幂等应用（F67）。
  - 已验：见 [功能核对 F67](FEATURE_COMPLETION_AUDIT.md)、[B1/预设参数链](STRATEGY_REGISTRY_ACCEPTANCE.md)；模型输出不自动改参数，差异及修订确认后才应用。
- [x] R606 成交 / 持仓截图识别，低确定性字段提示与人工确认（F42/F47）。
  - 已验：见 [功能核对 F42/F47](FEATURE_COMPLETION_AUDIT.md)；图片摘要冻结、严格结构/低确定性提示，成交仅进入待确认。模型真实识别准确率不由固定响应测试证明。
- [x] R607 整日 / 逐笔 / 批量 / 做 T 评分，保留人工最终分（F53）。
  - 已验：见 [功能核对 F53](FEATURE_COMPLETION_AUDIT.md)；AI建议与人工最终分分开，批量版本检查，复制为人工分需独立操作。
- [x] R608 日 / 周 / 月 / 回合 AI 草稿、预演分析、接受 / 撤回版本（F54/F69）。
  - 已验：日/周/月/回合/预演结构草稿、版本接受撤回见 [功能核对 F54/F69](FEATURE_COMPLETION_AUDIT.md)、[AI 与预演验收](REVIEW_PLANNING_ACCEPTANCE.md)。
- [ ] R609 同时运行慢 AI / 回测和保存复盘，验证响应性、取消与超时；日志密钥脱敏。
  - 进度 / 未验部分：慢流取消/超时/持久化、密钥隔离与计算时保存已有定向测试；见 [AI 验收](AI_SHORTCUTS_ACCEPTANCE.md)、[扫描验收](SCAN_JOB_ACCEPTANCE.md)。三种负载同时运行的复盘保存响应性基准尚未形成统一实测记录。

出口：AI 只生成可核对结果；OCR 不越过待确认；无 Key / 断网仍可使用核心账本；人工内容不会被自动覆盖。

## M7 · 完整输出、独立工具、交付与替代验收

依赖：此前全部阶段。解决旧审查 T02/T07/T11/T12/T14/T15/T16，并完成 T17/T19/T20 的新架构替代。

- [x] R701 Markdown / JSON / Excel / CSV / PDF / PNG / 报告包出口逐项对照（F60/F70–F73）。
  - 已验：Markdown/JSON/Excel/CSV/PDF/PNG/原与新报告包均有新入口；见 [导出指南](EXPORT_GUIDE.md)、[筛选导出](SCREENING_EXPORTS.md)、[旧报告验收](LEGACY_REPORT_ACCEPTANCE.md)、[研究表格真实下载验收](RESEARCH_TABLE_EXPORT_ACCEPTANCE.md)。
- [x] R702 日周月打印模板、统计报告、署名、导出目录与中文字体；长内容分页验证（D07）。
  - 已验：日周月/统计PDF、署名/目录及中文字体/长表分页已有渲染和下载验证，见 [导出验收](EXPORT_GUIDE.md)。系统默认查看器/其他平台包另列发布验收。
- [x] R703 数据目录选择 / 预览 / 一致性复制 / 切换 / 重启 / 回退（F74）。
  - 已验：预览/复制到新目录、排空/恢复点/重新验证、受控切换与失败回滚已有隔离进程及浏览器验证；见 [功能核对 F74](FEATURE_COMPLETION_AUDIT.md)、[受控启动说明](REBUILD_PACKAGING.md)。未实际切换用户原数据目录。
- [x] R704 独立混合选股、优化、晨报、波段研究与所有诊断脚本去向登记、可复现 CLI（F22）。
  - 已验：[策略实验室](STRATEGY_LAB.md) 逐项映射16个原脚本；第二规则profile、原评分分桶、完整周期目标/快目标统计及四预设全部落地。39项实验室定向测试、含分析/实验/报告的102项联测和最后全后端1366项通过。
- [ ] R705 Windows EXE、macOS 构建、Linux nginx、Windows TDX 同步；依赖 / 可选 provider 打包策略（F06/F76）。
  - 进度 / 未验部分：[最终Windows包](RELEASE_ACCEPTANCE.md) 已实际通过启动/计算/图表PDF/备份/目录切换和退出，TDX独立工具已完成。macOS/Linux实际产物、签名/公证、原生目录选择、真实用户源连通仍待对应环境验收。
- [x] R706 失败即停构建、静态资源清单、版本匹配、无缓存环境启动、非默认端口、深链接（F01/F76）。
  - 已验：[最终发行包](RELEASE_ACCEPTANCE.md) 逐文件SHA清单、源快照匹配、构建失败即停、随机非默认端口/深链接/独立空目录启动及子进程均验证通过。
- [x] R707 28 原业务页面与全部新路由逐项验收；移除产品中的 iframe 和第二套前端运行时（D04/D09）。
  - 已验：[28原页面映射](PAGE_MIGRATION_MAP.md) 按来源仓库+路径逐项匹配基线，全部有新入口、关键行为与已运行专项 smoke；最终 build797 全页86项通过（trade-all-pages-ui-8KuV21）。新产品只运行 rebuild 前端，无 iframe/第二运行时；完整尺寸/焦点与跨平台另列 R708/R705。
- [ ] R708 浅深色、320/768/1280/1440px、键盘 / 焦点、图表、自动保存、离线流程（F75/F77；D05/D06/D08）。
  - 进度 / 未验部分：主题、320/375px多页、图表与离线草稿已有真实验证，见 [草稿验收](REVIEW_DRAFT_ACCEPTANCE.md)、[功能核对 F75/F77](FEATURE_COMPLETION_AUDIT.md)。768/1280/1440全部页面矩阵与全键盘/焦点顺序专项验收尚未齐全。
- [ ] R709 全实体备份恢复、崩溃 / 磁盘满 / 附件缺失 / 半迁移演练；升级与程序回滚（F70/F74/F78）。
  - 进度 / 未验部分：完整备份预检/新目录恢复、任务中断/损坏拒绝、附件缺失与切换回滚已有测试；见 [旧资料验收](LEGACY_IMPORT_ACCEPTANCE.md)、[受控生命周期](REBUILD_PACKAGING.md)。磁盘满、迁移中断和真实程序升级/降级的整组故障演练仍待完成。
- [ ] R710 性能基准与包体检查；记录所有平台未验项、提供方限制和算法差异。
  - 进度 / 未验部分：[指定Windows基准](INTERACTIVE_LOAD_BENCHMARK.md) 与 [最终包体/源码清单](RELEASE_ACCEPTANCE.md) 已有实际记录。仍待满规模持续负载及其他目标平台实测，不把局部基准外推。
- [ ] R711 完成 F01–F78 的实现 / 验收矩阵，用户使用手册与发布说明；全量验收后标记完整替代。
  - 进度 / 未验部分：[78项功能核对](FEATURE_COMPLETION_AUDIT.md)、[新版使用手册](QUICKSTART.md)、[最终Windows发行记录](RELEASE_ACCEPTANCE.md) 已齐；后端1366通过，前端137通过/1原有跳过。其他平台签收及本清单余下工程强化项未全闭合，暂不标所有约束“完整替代”。

出口：包含两个原软件的全部约定功能，具备可分发产物和回退方式。旧数据迁移仍由用户另行主动发起，不因完成发布而自动迁移。

## 8. 前序代码审查如何承接

| 旧 TODO | 新任务 / 设计约束 |
|---|---|
| T01 破坏性恢复、T09 缺回合摘要 / 附件 | R104/R309/R709；完整 manifest + 临时恢复 + 预览 |
| T02 在线移动失效 | R703；维护锁、复制校验、指针切换、受控重启 |
| T03 损坏状态被覆盖、T04 数据目录冲突 | R101/R102；独立数据根、单实例锁、恢复模式 |
| T05 跨域与密钥 | R103/R601；会话 / 同源 / 密钥引用 / 脱敏 |
| T06 误杀其他进程 | R101/R706；启动器仅管理自身实例 |
| T07 akshare 打包矛盾、T12 两前端部署、T15 构建退出码、T16 Node / 端口 | R005/R705/R706；锁文件、可选能力探测、统一构建 |
| T08 同步 AI 阻塞 | R109/R602/R609；任务与异步 I/O |
| T10 NAV 边界 | R004/R206；精度、净值段、零值与超额赎回 |
| T11 旧图片地址 | R104/R309；附件 ID；未来导入器解析旧相对 / 绝对 URL |
| T13 保存丢稿 | R306；自动保存状态与路由保护 |
| T14 缺 journal CI | R002/R003/R208/R711；按能力验收，无独立 journal 漏测区 |
| T17 iframe、T18 双设置、T19 巨型文件、T20 依赖 / lint | R005/R106/R108/R601/R707；单应用与模块边界 |

## 9. 每个功能的完成记录模板

```text
功能 ID / 子功能：
关联技术要求 TR-xxx / 业务流程门槛 Gx：
来源路径与基线提交：
新页面与 API：
实现文件 / 数据迁移版本：
正常流程验收：
边界 / 错误 / 取消验收：
原版差异与原因：
导出 / 备份 / 主题影响：
运行证据（测试、截图、样本结果）：
已知限制 / 是否阻止完整替代：
结论：待实现 / 待验收 / 通过 / 存在阻塞
```

下一步按未勾条目的明确缺口收口：完成本轮兼容核对与最终源码回归，再做发行包、目标平台、故障演练和性能基准。已有 G1/G2 端到端样例继续用于防回归，不重新标为待启动。新需求可追加编号，原子能力和有意差异继续保留追踪记录。
