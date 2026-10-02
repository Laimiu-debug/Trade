# Trade 代码审查 TODO

审查日期：2026-09-25。基线提交：`3de868bc29ef906dddee6d9cc7082cfb1a6898f8`。

范围：统一入口、启动与打包、数据存储、备份恢复、实盘复盘接口、前端保存流程、部署与测试。主界面继续以 final-trade 为基础，旧数据迁移不纳入当前实施范围。

这是一轮针对关键路径的审查，不代表已逐行验证全部策略和回测算法。P1 表示应先修复的数据安全、可用性或交付阻塞问题；P2 表示功能完整性和整合工作；P3 表示后续维护工作。下列项目均未实施修复。

## P1：优先修复

- [ ] **T01 · 备份恢复必须先完整校验，再修改数据库。**
  - 现状：只检查 `exported_at` 是否存在，随后删除所有受管理表；缺失或类型错误的表数据被当作空列表。临时数据库中，导入仅含 `exported_at` 的 JSON 后，闪记记录由 1 条变成 0 条，接口逻辑未报错。
  - 位置：[restore_backup](../backend/trading_ms/services/backup.py#L63)。
  - TODO：增加备份版本、必需字段、表结构和记录校验；恢复前生成可回退备份；在事务内执行恢复；明确区分完整恢复和部分导入。
  - 验收：缺失字段、错误类型、未知版本和中途失败均保留原数据；合法的空库备份仍可显式恢复。

- [ ] **T02 · 修复在线移动数据目录后的数据库失效和半迁移风险。**
  - 现状：`engine.dispose()` 后移动文件，却未更新 `engine`、`SessionLocal`、`UPLOAD_DIR` 及静态文件挂载。界面只刷新网页，进程没有重启；临时目录实验确认下一次数据库查询抛出 `OperationalError`。逐个移动文件时失败也没有回滚。
  - 位置：[move_data](../backend/trading_ms/routers/misc.py#L88)、[Settings.moveData](../journal-frontend/src/pages/Settings.tsx#L124)。
  - TODO：增加维护状态，暂停写入；使用一致性备份、复制校验、原子切换和失败回滚；成功后受控重启服务或重新绑定所有数据库与上传目录引用。
  - 验收：迁移后能继续读写和查看截图；模拟复制失败时原库完整；并发保存不会写入旧位置。

- [ ] **T03 · 禁止读取异常时用空账户覆盖模拟交易文件。**
  - 现状：模拟账户读取、JSON 解析、迁移乃至持久化异常均进入同一 `except`，随后把默认账户写回原路径。临时损坏 JSON 文件被直接覆盖，丢失恢复线索。
  - 位置：[SimAccountEngine._load_or_init_state](../backend/app/sim_engine.py#L68)。
  - TODO：区分文件不存在、损坏、版本不兼容和权限错误；保留原文件，进入可诊断的恢复状态；提供最近成功快照。
  - 验收：损坏文件、无写权限、未知版本均不改变原始文件内容，界面明确提示恢复操作。

- [ ] **T04 · 隔离新旧程序的默认数据目录，并防止多进程互相覆盖。**
  - 现状：Trade 与原 final-trade 都默认读写用户目录 `.tdx-trend`；模拟账户初始化就可能写回文件。锁仅为进程内 `RLock`，持久化还使用固定 `.tmp` 文件名；同时运行两个版本可能覆盖彼此的内存快照。当前“原数据未迁移”不等于运行后不会写入旧数据。
  - 位置：[模拟账户默认路径](../backend/app/sim_engine.py#L65)、[写入状态](../backend/app/sim_engine.py#L139)、[主状态默认路径](../backend/app/store.py#L1489)、[复盘数据目录选择](../backend/trading_ms/database.py#L54)。
  - TODO：集中配置 Trade 数据根目录；默认使用独立、可写的应用数据目录；共享旧目录时增加进程级锁和明确提示；不要按数据库文件大小自动决定权威数据源。
  - 验收：首次运行 Trade 不修改原软件数据；同一数据目录的第二个写入进程被拒绝或安全串行化。

- [ ] **T05 · 收紧复盘子应用的跨源访问，并避免默认备份泄露密钥。**
  - 现状：子应用允许所有 Origin；备份导出包含全部 `Setting` 原始值，包括 AI Key。带任意 Origin 的测试 GET 返回 200、`Access-Control-Allow-Origin: *`，响应含临时假密钥。父应用的 CORS 配置没有阻止这一简单 GET 响应。
  - 位置：[子应用 CORS](../backend/trading_ms/main.py#L16)、[备份导出](../backend/trading_ms/routers/misc.py#L413)。
  - TODO：统一同源/可信来源策略，按本地服务或远程部署模式增加访问保护；普通备份默认排除密钥，迁移凭据使用独立的受保护流程。
  - 验收：不可信来源无法读取业务数据；默认导出中不存在测试密钥；合法本地界面的导出正常。

- [ ] **T06 · 启动时只管理本项目启动的进程。**
  - 现状：开发脚本按端口强杀进程，也会按名字结束旧版 FinalTrade；桌面启动器在端口被占用时同样直接 `taskkill /F`。启动整合版可能终止原软件或其他服务。
  - 位置：[开发启动清理](../start-dev.ps1#L179)、[桌面启动器](../backend/desktop_launcher.py#L190)。
  - TODO：验证 PID、命令行及工作目录的归属；优先选择空闲端口；仅对本项目子进程执行受控退出；后台进程隐藏窗口并记录生命周期。
  - 验收：测试程序占用 8000、8010 或 4173 时保持运行，Trade 使用其他端口或给出明确错误。

- [ ] **T07 · 解决 Windows 打包依赖自相矛盾。**
  - 现状：`requirements.txt` 安装 `akshare`，打包脚本随即检查并禁止该包，干净环境按当前脚本正常安装成功后必然报错退出。打包排除列表也包含该包。
  - 位置：[运行依赖](../backend/requirements.txt#L9)、[禁止包列表](../build-exe.ps1#L44)、[安装后检查](../build-exe.ps1#L81)。
  - TODO：明确行情可选依赖与打包依赖的边界；让源码运行和 exe 的可用行情源与界面说明保持一致。
  - 验收：干净 Windows 环境可生成 exe；启动后两套页面可用；配置中的可用行情源均有对应依赖。

- [ ] **T08 · 将截图识别中的同步 AI 调用移出事件循环。**
  - 现状：两个截图导入路由是 `async def`，但直接调用同步 `httpx.post`，超时配置为 120 秒。由于两套软件共用服务，截图识别会阻塞主工作台请求。替换为 0.3 秒模拟 AI 后，事件循环心跳也延迟了约 0.302 秒。
  - 位置：[交易截图导入](../backend/trading_ms/routers/trades.py#L143)、[账户截图导入](../backend/trading_ms/routers/capital.py#L231)、[同步 AI 请求](../backend/trading_ms/services/ai.py#L91)。
  - TODO：使用异步 HTTP，或将整段同步工作连同合适的数据库会话生命周期交给线程池/任务队列；增加取消、超时和状态反馈。
  - 验收：模拟 AI 请求等待 30 秒时，健康检查、复盘读取和主工作台操作仍及时响应。

## P2：补齐功能与整合

- [ ] **T09 · 让“全量备份”真正包含回合复盘和截图。**
  - 现状：`RoundReview` 不在导出和恢复清单中；图片只有 URL，没有文件内容。临时实验确认回合摘要未导出，恢复后旧摘要仍残留；换机恢复会缺失这些数据。
  - 位置：[备份模型清单](../backend/trading_ms/services/backup.py#L9)、[导出表清单](../backend/trading_ms/routers/misc.py#L422)、[RoundReview](../backend/trading_ms/models.py#L145)。
  - TODO：使用带清单和校验值的备份包，包含全部不可重建业务表及上传文件；恢复时校验关联关系并清理不属于该备份的旧摘要。
  - 验收：备份恢复到全新目录后，交易、回合摘要、日周月复盘及所有截图一致。

- [ ] **T10 · 处理净值为零、超额出金和非有限数值。**
  - 现状：快照允许总资产为零，后续 `threshold / last.nav` 以及资金流的 `signed / nav` 可能除零；临时账户“初始资金 100 + 总资产快照 0”已触发 `ZeroDivisionError`。
  - 位置：[快照校验](../backend/trading_ms/routers/capital.py#L182)、[资金流折算](../backend/trading_ms/services/netvalue.py#L63)、[下一目标计算](../backend/trading_ms/services/netvalue.py#L112)。
  - TODO：定义净值归零、全部出金和重新入金的业务规则；对金额、份额、费率、数量校验有限性与范围；页面展示不可计算状态。
  - 验收：零资产、全部出金、出金超额、NaN/Infinity 输入均有明确结果或校验错误，不返回 500。

- [ ] **T11 · 兼容旧复盘图片 URL。**
  - 现状：旧库保存 `/uploads/...`，新上传保存 `/journal-app/uploads/...`；读取时原样返回 URL，主应用未挂载旧路径。接入已有数据目录或恢复旧记录后，历史截图无法按旧地址加载。
  - 位置：[读取 images](../backend/trading_ms/routers/reviews.py#L526)、[新图片 URL](../backend/trading_ms/routers/reviews.py#L639)、[图片渲染](../journal-frontend/src/pages/Journal.tsx#L1221)。
  - TODO：数据中保存相对附件标识，由统一 URL 构造函数生成访问地址；为旧值添加兼容读取或版本迁移。
  - 验收：旧、新两种记录都能查看、打印和删除对应截图。

- [ ] **T12 · 更新 Linux 部署和复盘开发服务的路径配置。**
  - 现状：Linux 部署/更新只构建主前端，Nginx 只代理 `/api/`，`/journal-app/` 会进入主前端 fallback；复盘前端单独执行 `npm run dev` 时，请求带 `/journal-app/api`，其代理仍只匹配 `/api`。
  - 位置：[部署构建](../deploy/deploy.sh#L125)、[Nginx 配置](../deploy/deploy.sh#L181)、[更新脚本](../deploy/update.sh#L30)、[复盘 Vite 配置](../journal-frontend/vite.config.ts#L7)。
  - TODO：将复盘构建、静态资源、API、上传路径纳入每种运行模式；构建产物就绪后再启动依赖它的后端。
  - 验收：源码开发、单端口桌面、Nginx 部署三种模式中，复盘页面、API、图片和备份均可访问。

- [ ] **T13 · 为复盘编辑增加路由离开保护和可靠保存。**
  - 现状：离开保护仅监听 `beforeunload`；自动保存延迟 2 秒，组件卸载会清除计时器。React 页内导航以及主界面移除 iframe 没有相应保存协调，快速切换页面存在丢稿窗口。
  - 位置：[自动保存及离开保护](../journal-frontend/src/hooks/usePersist.ts#L4)、[嵌入入口](../frontend/src/pages/journal/JournalPage.tsx#L10)。
  - TODO：路由级 dirty 状态、离开确认或离开前保存；持久化草稿；在 iframe 保留期间同步子页面保存状态与父导航。
  - 验收：输入后立即切换日/周/月复盘或主导航，内容被保存或出现离开确认；保存失败可恢复草稿。

- [ ] **T14 · 将实盘复盘纳入 CI 和业务回归测试。**
  - 现状：CI 仅检查主前端；新增复盘测试只有接口可达及退出接口检查，且静态页面测试在缺少构建产物时被跳过。原 LaimiuTrade 的 10 个核心测试没有迁入整合仓库。
  - 位置：[CI](../.github/workflows/ci.yml#L47)、[复盘挂载测试](../backend/tests/test_journal_mount.py#L7)、[现有 E2E](../frontend/e2e/main-flow.spec.ts#L3)。
  - TODO：加入复盘前端 build/lint/test、原核心测试和 T01–T13 的关键回归；测试使用临时数据库及附件目录；增加真实后端驱动的整合 E2E。
  - 验收：干净 checkout 中缺少复盘构建、图片路径错误、恢复丢数据、阻塞事件循环时 CI 必须失败。

- [ ] **T15 · 打包脚本检查每个外部命令的退出码。**
  - 现状：Windows 脚本依赖 `$ErrorActionPreference`，但未逐步检查 pip、npm、PyInstaller 的原生命令退出码；仅检查产物是否存在。旧 dist 存在时，新构建失败仍可能继续包装旧版本。
  - 位置：[Windows 构建](../build-exe.ps1#L79)。
  - TODO：为原生命令统一增加非零退出即停止机制；使用独立构建目录和构建清单；前端采用锁文件安装。
  - 验收：人为制造 TypeScript 错误且保留旧 dist 时，打包立即失败，不产出带旧前端的新安装包。

- [ ] **T16 · 修正运行环境要求和自定义端口参数。**
  - 现状：README 写 Node.js 18+，两套已安装 Vite 声明的最低范围均为 `^20.19.0 || >=22.12.0`；开发脚本接受 `FrontendUrl`，启动命令却固定执行端口 4173 的 `dev:host`。
  - 位置：[环境要求](../README.md#L9)、[启动参数](../start-dev.ps1#L2)、[前端启动](../start-dev.ps1#L230)、[固定端口](../frontend/package.json#L8)。
  - TODO：在文档、package engines、CI 和启动前检查中统一版本要求；解析端口后将同一个值用于启动、代理、健康检查和浏览器地址。
  - 验收：低版本 Node 启动前有清晰错误；指定 4174 等自定义端口后可以正常启动访问。

- [ ] **T17 · 将复盘功能逐步接入主应用的路由与导航。**
  - 现状：当前仍是整个旧软件嵌在 iframe 中，保留双层导航、双套主题，子页面地址也未体现在主应用路由。
  - 位置：[JournalPage](../frontend/src/pages/journal/JournalPage.tsx#L3)、[复盘 App](../journal-frontend/src/App.tsx#L1)。
  - TODO：按资金账本、交易记录、日/周/月复盘逐页迁入主路由；统一日期、股票跳转、错误和加载状态；保留实盘与模拟账户的明确类型。
  - 验收：单一导航和主题；可直接打开某日复盘链接；浏览器前进后退及窄屏布局正常。

- [ ] **T18 · 统一 AI 和行情配置的服务入口。**
  - 现状：主工作台和复盘模块各自维护模型、Key、通达信路径与行情优先级；主 AI 助手把 `/journal` 识别为 generic，也没有实盘复盘上下文。
  - 位置：[复盘配置](../backend/trading_ms/services/settings.py#L15)、[复盘 AI 配置读取](../backend/trading_ms/services/ai.py#L31)、[主 AI 路由上下文](../frontend/src/shared/ai/routeAIContext.ts#L4)。
  - TODO：建立共享配置适配层，保留文本与视觉模型分工；统一行情数据日期和来源状态；为复盘建立有边界的 AI 上下文。
  - 验收：配置一次后两侧可用；能区分模拟与实盘数据来源；未配置 AI 时核心记账和复盘不受影响。

## P3：维护与性能

- [ ] **T19 · 拆分大文件并明确模块边界。**
  - 现状：`backend/app/store.py` 为 18,631 行，`BacktestPage.tsx` 为 8,455 行；数据、任务、缓存、策略与页面状态集中，变更影响面大。
  - 位置：[store.py](../backend/app/store.py)、[BacktestPage.tsx](../frontend/src/pages/backtest/BacktestPage.tsx)。
  - TODO：按任务调度、持久化、行情、策略执行、统计查询拆分后端；按表单、历史记录、任务状态和结果展示拆分页面；在接口不变的前提下分批重构。
  - 验收：现有测试和关键回归保持通过，单一功能修改不再同时触及多个不相关模块。

- [ ] **T20 · 收敛依赖版本、lint 告警和构建资源体积。**
  - 现状：两套前端使用不同 Vite/TypeScript 主版本，主前端同时保留 npm/pnpm 锁文件；本轮主前端 lint 有 67 个 warning，复盘前端有 9 个 warning。后端部分依赖仅指定下限，干净安装不固定。
  - 位置：[主前端依赖](../frontend/package.json)、[复盘依赖](../journal-frontend/package.json)、[后端依赖](../backend/requirements.txt)。
  - TODO：选择统一包管理方式并固定受验证的依赖组合；优先处理可能造成陈旧状态的 Hook 依赖告警；按需加载复盘页面和图表，建立资源体积基线。
  - 验收：干净环境重复安装结果一致；新增告警受 CI 控制；主要页面无无效重复请求和明显重复渲染。

## 本轮验证记录

| 检查 | 结果 |
| --- | --- |
| 后端现有测试，用户目录重定向至临时目录 | 266 passed |
| 主前端测试 | 33 passed，1 skipped，14 个测试文件通过 |
| 主前端 lint | 0 error，67 warnings |
| 复盘前端 lint | 0 error，9 warnings |
| 不完整备份恢复 | 仅含 exported_at 的备份将临时库 1 条记录清为 0 |
| 数据目录移动 | 移动后原 engine 再查询抛出 OperationalError |
| 零资产净值计算 | 抛出 ZeroDivisionError |
| 模拟账户损坏文件 | 原损坏内容被默认账户覆盖 |
| 跨源导出测试 | 200 + Allow-Origin: *，响应含临时假 Key |
| 截图识别事件循环实验 | 0.3 秒模拟同步 AI 导致约 0.302 秒心跳延迟 |
| 回合复盘备份 | 未导出 round_reviews，恢复时旧摘要未清理 |

涉及写入的定向实验全部使用内存数据库或临时目录，没有对原软件业务数据执行恢复、迁移或损坏操作。没有实际执行杀进程逻辑、Windows/macOS 安装包生成、Linux 部署、真实 AI 或外部行情调用；对应项目以代码路径为依据。新增复盘整合流程尚无完整浏览器 E2E 验证。

建议顺序：先处理 T01–T08；随后补齐 T09–T16 和对应回归；再推进 T17–T18 的界面及服务整合，最后实施 T19–T20。
