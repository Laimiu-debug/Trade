# 本地发行验收 · 更新于 2026-10-03

## 当前可运行产物

### 2026-10-03 全项目检查优化 · 独立 Windows 单文件包

本轮可运行产物：`.release-dist/20261002T163231294173Z/TradeRebuild.exe`，大小 **149556312 字节（约 142.6 MiB）**。本地验收日期 2026-10-03；构建号使用 UTC。旧固定入口 `.release-dist/current/TradeRebuild.exe` 和桌面快捷方式保持原样，没有关闭或替换用户正在使用的实例。源码修改、测试范围与剩余边界见 [代码质量检查](CODE_QUALITY_AUDIT.md)。

- 新包包含证券别名与历史事实兼容、FIFO 加速、统计恢复、会话自动恢复、保存防重复，以及完整数据库/ZIP 备份校验与特殊数据目录 URI 修复。
- 本轮也修复旧研究/复盘的草稿、AI 评分、备份和开发脚本；这些过渡界面不属于重构 EXE 的打包内容，各自的构建与测试另列在检查报告中。
- 采用源码快照构建，412 份应用来源逐项 SHA-256 匹配当前 `backend/trade_app`、`frontend/dist-rebuild`、启动器和 spec；没有复制账本、密钥或用户运行数据。

| 验证与指纹 | 结果 |
|---|---|
| 后端验证 | 全量 **1544 passed，0 failed / 0 skipped，427.14 秒**；最后单行存储 URI 修复及新增特殊路径回归经 **83 个独立相关用例通过**。全量 5 个 warning 为依赖弃用及重复 ZIP 成员拒绝测试 |
| 前端验证 | **200 passed，0 skipped**；三个前端构建通过；frontend lint 0 errors / 101 warnings，journal lint 0 errors / 6 warnings |
| 实际浏览器验证 | 新版页面 174 个检查通过；实盘交易、入金、快照双提交各仅一个写请求；旧开发脚本空闲端口、主 API 和复盘 API 代理、自定义 Origin 写入通过 |
| 最终 EXE 完整验收 | `%TEMP%/trade-frozen-smoke-D20N1E`：只复制 EXE 至中文空格目录，使用 `source#history%25` 和 `恢复 数据#history%25` 数据目录、独立指针与随机端口；存储页读取成功且没有创建被截断路径的空文件；19 策略、真实单股/组合/分析进程、漏斗/B1、实验室 CLI、中文 PDF、备份恢复、目录切换、重启保留数据、退出及解压目录清理全部通过 |
| 构建来源 | `.release-build/20261002T163231294173Z/source`，**412 份**文件与当前应用来源逐项一致 |
| EXE SHA-256 | `e583ec36a4f2d5ef474f77d584a1bdc042c6106cb89a425c6c8082a583d8820f` |
| 清单 SHA-256 | `805fa45f664e5a7d5af3a1dbbee96022ec1035858970645c67771ec986035d1f` |

所有新增交易、行情、计算和数据恢复试验使用独立临时目录。实际行情提供方、AI 服务、macOS 及 Linux 部署未在本轮在线验收。Windows 构建仍有既有可选依赖 hidden-import 提示，实际包的完整运行验收通过；这些提示不能作为未测试路径的通过证明。

本轮记录在 `.tmp-rebuild-code-audit/`：`all-pages-final.log`、`onefile-verified.log`、`packaged-verified.log`、`main-flow-verified.log`、`legacy-start-verified.log` 和 `release-verification.json`。后端完整日志与最后 URI 定向日志在 `%TEMP%/trade-platform-fix-6bca241b8d9c45fb8b1febd780cfd0dc/`，前端完整日志为 `%TEMP%/trade-frontend-final-tests.log`。临时证据不随应用包分发。

此前构建 `.release-dist/20261002T162306140673Z/TradeRebuild.exe` 也通过完整验收，但缺少最后特殊路径 URI 修复；使用上方最终产物。

### 2026-09-27 Windows 单文件 EXE（现有固定入口：通达信目录选择与扫描）

构建：`.release-dist/20260927T054406747428Z/TradeRebuild.exe`，大小 **149543983 字节（约 142.6 MiB）**。已更新固定入口 `.release-dist/current/TradeRebuild.exe`，桌面 `Trade 复盘工作台.lnk` 继续指向该入口；安装副本与构建 SHA-256 相同。

- “系统设置 → 行情来源”新增原生目录选择、扫描本机、候选列表和“使用此目录”。只在本次运行采用手动选择，不保存目录配置、不修改 Windows 环境变量。
- 受控启动时浅层扫描固定磁盘和 Program Files；唯一候选自动使用，多个候选需选择，深层位置可手动指定。当前机器发现 `E:\TDX`，不是代码内预设的路径。
- 日线/分时读取和全市场任务读取当前运行中的选择；未完成任务使用来源指纹防止混入另一个安装的行情，任务中心显示重新选择原目录的说明。
- 完整发行测试发现“已打开页面在首次健康轮询前立即退出”会被误报启动失败；启动器已核对有效退出意图并正常结束，加入合法/过期/缺失意图、异常退出码和不匹配动作回归。

| 验证与指纹 | 结果 |
|---|---|
| 通达信及相关后端 | **87 passed，17.76 秒**：选择/扫描/重启不持久化、行情同步与身份、武隆全市场、生命周期；**10 passed，8.20 秒**：TDX 导入和 6 类全市场任务；原生目录选择服务 **6 passed，4.20 秒**。各批次仅既有 dateutil 弃用提示 |
| 启动与退出修复 | **36 passed，38.79 秒**：生命周期、真实进程回收与 Windows 托盘；新增提前退出 5 例全部通过 |
| 前端与静态检查 | 来源选择与设置工作区 **7 passed**；TypeScript/Vite 构建、OpenAPI/TypeScript 契约、架构边界通过；保留已有图表 chunk 体积提示 |
| 最终 EXE 通达信验收 | `%TEMP%/trade-tdx-frozen-0F09Mt`：只复制 EXE、随机端口、独立数据/指针/解压目录；启动自动识别、页面选择/扫描、只读检查、实际导入、后台全市场任务、375px/1440px 布局、重启不保留选择且保留导入数据、正常退出清理均通过 |
| 最终 EXE 完整验收 | `%TEMP%/trade-frozen-smoke-w9x9uh`：19 策略、真实单股/组合/分析计算子进程、漏斗/B1、研究导航、CLI、中文 PDF、备份与数据目录切换、重新解包启动与数据保留、退出清理全部通过 |
| 构建来源 | `.release-build/20260927T054406747428Z/source`，**410 份**应用文件与当前源码/静态资源逐项一致 |
| EXE SHA-256 | `0bb8cc5bcc945725595ddba2bb82c635bc35856bc21f6e6850a04640780f469f` |
| 清单 SHA-256 | `bc8f07d16938b4c5d1aeb508d819eee8f5bfae9d32a9516411658d8dd0bf783c` |

安装前确认固定入口已无运行实例和运行记录，然后替换 EXE 并正常启动。2026-09-27 13:47 本地验证：常用数据目录仍为 `C:\Users\25647\TradeRebuild`，服务端口 8011，实例 `130790bf01714aec9aa771e400e40181`；扫描 349 个候选目录发现唯一 `E:\TDX`，状态 `auto`、`persisted=false`。600000 日线检查返回 `readable`、1250 条，首末日期 2021-08-02 / 2026-09-24；这是首尾记录与文件结构探测，不是逐条完整校验。Windows 用户环境变量 `TRADE_TDX_ROOT` 仍为空。

初版定向冻结包验证目录：`%TEMP%/trade-tdx-frozen-wGjrjX`；初版完整验证 `%TEMP%/trade-frozen-smoke-Wd20F1` 发现提前退出误报问题，已修复并在最终包复测通过。最终日志：`%TEMP%/trade-tdx-final-onefile-20260927.log`、`trade-tdx-final-frozen-20260927.log`、`trade-tdx-final-full-frozen-20260927.log`。所有写入/导入/任务试验仅使用独立临时数据；实际 `E:\TDX` 只用于读取发现和样本检查，常用账本没有写入测试行情或测试账户。

### 2026-09-27 托盘退出与启动恢复（此前构建）

固定使用入口：`G:\CODE\trade\Trade\.release-dist\current\TradeRebuild.exe`，桌面快捷方式为 `Trade 复盘工作台.lnk`。原始构建：`.release-dist/20260926T232324513933Z/TradeRebuild.exe`。两份文件 SHA-256 完全一致，大小 **149531915 字节（约 142.6 MiB）**。

本地日期 2026-09-27。本次实际无法启动的原因是 `20260926T113254711508Z` 旧包仍在后台持有常用数据目录的锁，而其本身没有托盘和运行发现记录。确认旧实例身份、数据目录、没有进行中的写入/后台工作后，通过旧实例自己的受控退出 API 关闭；未清除数据库或手工删锁。

- Windows 原生托盘支持点击打开页面、右键“退出 Trade（关闭全部后台）”；退出先等待最多 15 秒，未完成则回收自己的服务及子进程，最后移除图标。
- 重复双击复用运行实例，退出中等待释放再启动；对无发现记录的旧包增加同目录身份核验。启动失败显示错误与日志位置。
- 新版已从固定入口实际启动，常用目录仍是 `C:\Users\25647\TradeRebuild`；`http://127.0.0.1:8011/rebuild.html` 返回 200、实例身份匹配，Windows Shell 确认托盘图标已注册。此次保留新版运行供用户使用。

| 验证与指纹 | 结果 |
|---|---|
| 相关源码回归 | **55 passed，1 个既有 dateutil 弃用警告，52.66 秒**；含原生托盘、生命周期、真实子孙进程回收、计算限制、冻结环境和目录选择。最后调整退出状态发布时间后，**18 项定向检查通过，30.83 秒** |
| 真实 EXE 托盘验收 | `%TEMP%/trade-tray-frozen-c3f8331a081847038610de4069c11311`，**1 passed，42.92 秒**：实际单文件包连续三次启动→原生退出菜单/窗口关闭→重开；Shell 图标注册/移除、锁/端口释放、账户保留、旧包兼容发现、`_MEI` 清理全部通过 |
| 强制结束验收 | `%TEMP%/trade-launcher-cleanup-lycdRV`：并发双启动复用、只杀测试启动器后的后台回收、重启数据保留、正常退出及解压目录清理均通过 |
| 完整冻结包验收 | `%TEMP%/trade-frozen-smoke-djANbv`：19 策略、经典规则/单股/组合/分析 worker、漏斗与 B1、研究页面、CLI、中文 PDF、备份、目录切换、重启及退出均通过 |
| 构建与静态检查 | `python scripts/build_trade_rebuild.py --onefile --skip-frontend`；复用已验证的 802 模块前端。架构边界与 diff 检查通过 |
| 构建来源 | `.release-build/20260926T232324513933Z/source`，**408 份**文件与当前应用来源逐项一致 |
| EXE SHA-256 | `66899dd614088c1180dea8206957537f0b8a3f394743893c70b1921d488f5e9d` |
| 清单 SHA-256 | `73aca4b47dee61a889fbf6e08c9aca0ca0efb38c7ec3496b7d24972d624c3f4e` |

所有功能测试使用独立临时数据、状态指针和随机端口；常用目录仅用于验证旧实例、受控关闭旧包及启动新版，没有写入测试账户/行情。日志前缀均在 `%TEMP%`：`trade-tray-final-tests-20260927.log`、`trade-tray-final-targeted-20260927.log`、`trade-tray-onefile-20260927.log`、`trade-tray-frozen-20260927.log`、`trade-tray-full-frozen-20260927.log`、`trade-tray-abrupt-frozen-20260927.log`。托盘机制与操作见 [打包说明](REBUILD_PACKAGING.md) 和 [使用指南](QUICKSTART.md)。

### 2026-09-27 统一应用图标（此前构建）

`G:\CODE\trade\Trade\.release-dist\20260926T230508118911Z\TradeRebuild.exe`

本地日期 2026-09-27，UTC 构建号 `20260926T230508118911Z`。大小 **149511543 字节（约 142.6 MiB）**。新增青绿 T 与行情柱形图标，统一侧栏、浏览器页签和 Windows EXE。保留上一版后台清理与重复启动逻辑。先从系统设置退出旧程序，再运行新版。

| 验证与指纹 | 结果 |
|---|---|
| 图标资产 | 可编辑 SVG、512px PNG、180px Apple 图标；ICO 含 16 / 24 / 32 / 48 / 64 / 128 / 256px，逐尺寸直接从 SVG 渲染 |
| 前端构建 | `npm run icons:rebuild`、`npm run build:rebuild` 通过；TypeScript 通过，802 modules；保留既有图表 chunk 体积提示 |
| EXE 资源 | 解析实际 PE 的图标组，7 个图标帧与源 ICO 对应帧逐字节一致 |
| 独立启动验收 | `%TEMP%/trade-icon-smoke-WLGEiS`：只复制 EXE，随机端口、独立数据与解压目录；4 个图标资源与源码逐字节一致，页签链接及侧栏 SVG 加载正常；已查看明暗主题截图，页面异常为 0 |
| 正常退出 | 独立实例退出码为 0，运行发现记录与 `_MEI` 解压目录已清理；未启动或修改用户正在使用的数据目录 |
| 构建与来源 | `python scripts/build_trade_rebuild.py --onefile --skip-frontend`；`.release-build/20260926T230508118911Z/source`，**407 份**文件与当前来源逐项校验一致 |
| EXE SHA-256 | `5f04c3b556f2207d4ecd8edec6bdffe056bf41239a3925c6f3144669374f2bd0` |
| 清单 SHA-256 | `38291358063a9b9fd71133e35f1d784f55831058b0987750c0bdda6c48f82e05` |

图形源与重新生成方式见 [统一设计规范](DESIGN_SYSTEM.md#应用图标)。本轮仅调整图标、前端品牌引用和 EXE 图标配置，未重跑下列历史全功能回归。打包日志：`%TEMP%/trade-icon-onefile-20260927.log`。

### 2026-09-27 后台清理与重复启动修复（此前构建）

`G:\CODE\trade\Trade\.release-dist\20260926T225353191020Z\TradeRebuild.exe`

本地日期 2026-09-27，UTC 构建号 `20260926T225353191020Z`。大小 **149374921 字节（约 142.5 MiB）**。先从系统设置正常退出正在运行的旧版，再运行这份修复版；已有数据目录沿用，未自动重启用户正在使用的旧程序。

- 正常退出沿用写入/任务收尾；启动器闪退或被强制结束时，Windows Job 自动回收其服务及子进程，释放数据目录锁和端口。
- 归属绑定完成前，服务必须等到启动握手；绑定失败不能开始访问数据库。只管理自己创建的进程树，不清理同名软件、用户浏览器或其他服务。
- 同目录重复双击经健康状态与实例 ID 校验后重新打开已运行页面，并发启动只保留一个服务；过期运行记录不阻止重新启动。
- 关闭浏览器标签仍保留后台任务。强制结束不承诺保留未保存表单或完成在途计算；已提交数据和中断任务沿用原有事务/恢复机制。

| 验证与指纹 | 结果 |
|---|---|
| 相关后端回归 | **48 passed，1 个既有 dateutil 弃用警告，29.75 秒**；生命周期、真实 Windows 子孙进程/无关进程保护、并发启动、强制结束、锁/端口释放、计算限制、目录选择与冻结环境 |
| 真正单文件异常退出验收 | `%TEMP%/trade-launcher-cleanup-vGx2X5`：两份同时启动只保留一个；只强制结束实际 Python 启动器，服务自动消失；重启同一数据目录保留已创建账户；正常退出清理发现记录，正常及异常退出后 `_MEI` 均清理 |
| 真正单文件完整流程 | `%TEMP%/trade-frozen-smoke-uLmlUW`：19 策略/经典 worker、漏斗/B1、研究页面、单股/组合/分析 worker、CLI、中文 PDF、备份、目录切换、重启与退出均通过；验证嵌套计算 Job 仍可运行 |
| 静态门禁 | 架构、API 契约及 diff 检查通过；前端未变更，复用 build802 |
| 构建命令 | `python scripts/build_trade_rebuild.py --onefile --skip-frontend` |
| 来源 | `.release-build/20260926T225353191020Z/source`，**403 份**文件与当前构建来源逐项校验一致 |
| EXE SHA-256 | `f5b65e628fa528d7274eeb0ae136fe77536af58f6d524bf01be75dce851c264c` |
| 清单 SHA-256 | `338c0073de15fab96983f5e49d7f3412df4235e7d9ae056ef1b9825605b3e46c` |

所有本轮测试使用独立临时目录。日志：`%TEMP%/trade-launcher-cleanup-final-tests-20260927.log`、`trade-launcher-cleanup-onefile-20260927.log`、`trade-launcher-cleanup-frozen-20260927.log`、`trade-launcher-cleanup-packaged-20260927.log`。实现与机制资料见 [后台生命周期说明](REBUILD_PACKAGING.md)。

### 2026-09-26 页面与策略更新（此前构建）

`G:\CODE\trade\Trade\.release-dist\20260926T113254711508Z\TradeRebuild.exe`

只需复制这个 EXE，大小 **149366530 字节（约 142.4 MiB）**。双击启动本机服务并打开浏览器，无需另装 Python/Node。若旧程序仍在运行，请先通过旧程序的系统设置正常退出，再启动新版；使用原来选择的数据目录即可继续读取已有数据。程序不自动迁入两个旧项目的账本。

本轮改动：研究拆为 11 个子页面，组合配置采用 5 个步骤；行情、模拟与统计分开导航。修复深色输入框、开户按钮、资讯重复摘要、窄屏布局和跨账户迟到响应。原 16 策略按族和执行能力整理，新增 Donchian、SMA、Bollinger 三个参考规则，共 19 项，17 项有普通单股运行器。现有保存的启用集合不会自动增加新策略；可在策略目录管理中预览后启用。

| 项目 | 当前构建记录 |
|---|---|
| 构建命令 | `python scripts/build_trade_rebuild.py --onefile --skip-frontend`，使用已验证 build802 前端 |
| 构建源快照 | `.release-build/20260926T113254711508Z/source` |
| 发行清单 | `.release-dist/20260926T113254711508Z/release-manifest.json`，`bundle_mode=onefile` |
| 源文件 | **402 份**；构建后逐项与当前应用源码/前端产物校验一致 |
| EXE SHA-256 | `41b46a1a5d3bab2ab550d74b83fed4f364a72553e76c3df0ab67bee8df7344cb` |
| 清单 SHA-256 | `14d0e9709c94c09e5b9b777294d6310d92bef33ae2e8f1460cde923362a15440` |
| 环境 | Windows x64，Python 3.13.15，PyInstaller 6.19.0，Edge 无头浏览器 |

#### 本轮代码门禁

| 门禁 | 实际结果 |
|---|---|
| 后端全量 `python -m pytest tests -q` | **1422 passed，1 failed，5 warnings，456.43 秒**。唯一失败为旧目录固定 16 项的断言；修为原 16 ID + 新 3 ID 的精确集合后，失败用例和目录整合相关 **12 项全部通过**。后续未改后端生产代码，未将这 12 项重复计入全量数字。 |
| 修复后定向回归 | `python -m pytest tests/test_trade_rebuild.py::test_relative_strength_signal_matches_legacy_plugin_on_frozen_bars tests/test_strategy_consolidation.py -q`，12 passed，1 warning，6.98 秒；保留原相对强弱算法对照断言 |
| 前端全量 `npm run test` | **166 passed，1 skipped，45 文件**；唯一跳过仍为原版 `src/test/backtestPage.test.tsx` 的既有导入报告测试 |
| 前端构建 | `npm run build:rebuild` 通过，TypeScript 通过，802 modules；最后仅调整组合步骤底栏的窄屏排布并复验截图 |
| 静态检查 | lint **0 errors / 113 warnings**；架构、API 契约、设计令牌与 `git diff --check` 通过 |

日志位于 `%TEMP%`：`trade-ui-strategy-full-backend-20260926.log`、`trade-ui-strategy-final-tests-20260926.log`、`trade-ui-strategy-final-build-20260926.log`、`trade-ui-strategy-final-lint-20260926.log`。第三方弃用及图表 chunk 体积提示仍保留，没有降低规则消除警告。

#### 本轮实际浏览器与 EXE 验证

以下均为独立临时数据目录，无真实账户迁入或真实下单。目录前缀为 `%TEMP%`。

| 验证 | 结果与证据 |
|---|---|
| 全页面导航 | `trade-all-pages-ui-dnYYyX`：**174 检查通过**，实盘/模拟主导航、研究子页/实验/报告模式、明暗主题、320px、键盘、展开详情及原生控件；没有页面异常和意外外网请求 |
| 截图问题及账户隔离 | `trade-layout-redesign-wHmFlY`：**14 检查通过**；1440/768/375 开户和组合布局、资金/退出步骤、复选框、新闻配色/去重、后退保留配置；迟到读取及保存不能覆盖另一账户数据/草稿。资讯只使用明确标记的测试响应。已逐张查看桌面/窄屏及资金/退出步骤截图 |
| 三个经典策略 | `trade-classic-strategies-ui-yNSjrL`：各自经过目录选择、自定义参数、入场与独立退出研究、真实单股 worker；2025-02-03 买入、2025-02-05 后续开盘退出，费用/参数/中文退出原因可核对；页面错误与外网请求均为 0 |
| 当前单文件 EXE | `trade-frozen-smoke-1MNYav`：只复制 EXE 到中文且带空格的空目录启动；19 项目录、经典 SMA 真实 worker、漏斗/B1 保存和重启后读取（B1 96 根样本明确历史不足）、研究目录/单股/组合切页；保留原单股、组合、高级分析、lab CLI/worker、同步 CLI、中文 PDF、备份、目录切换、重新连接及退出测试；重新启动后经典结果/参数、中文复盘及回测指纹保持，两次 `_MEI` 展开目录正常清理 |

本轮测试数据仅验证规则与软件行为，没有形成 A 股样本外收益结论。实现、来源、参数和限制见 [经典策略说明](CLASSIC_STRATEGIES.md)；原软件比较与页面结构见 [UI 布局审查](UI_LAYOUT_AUDIT.md)，全目录关系见 [策略整合审查](STRATEGY_CONSOLIDATION_AUDIT.md)。

旧 smoke 脚本的导航适配另外通过 90 份脚本语法检查；筛选导出在独立临时目录 `trade-routing-smoke-f2adbc2a813b49f891daa891463b156a` 完整通过，未将语法检查计作业务运行验收。

验收过程有一次环境选择错误：旧筛选导出脚本默认连接正在运行的 8011 旧程序，新增 SH600091/SH600092 两份固定测试行情后失败，未创建账户、交易或报告。已按精确 ID、创建时间、内容 SHA-256 和无引用检查撤回两份样本及对应测试幂等记录，API 重新读取确认已消失；可恢复副本为 `%TEMP%/trade-smoke-fixture-rollback-ugvoyl5b`。该脚本已取消默认 8011 地址，要求显式设置独立测试服务 `TRADE_SMOKE_URL`，后续复验使用临时目录。

### 此前单文件 EXE（历史验收）

`G:\CODE\trade\Trade\.release-dist\20260926T103755035428Z\TradeRebuild.exe`

**只需复制这一个 EXE**，大小 149306879 字节（约 142.4 MiB）。双击启动本机服务并打开浏览器，不需要另装 Python 或 Node。内置资源临时展开，正常退出后清理；用户数据保存在独立目录，原项目数据不会自动迁入。

| 项目 | 单文件构建记录 |
|---|---|
| 构建命令 | `python scripts/build_trade_rebuild.py --onefile --skip-frontend`；复用下文已验证且未改动的 build797 前端 |
| 构建源快照 | `.release-build/20260926T103755035428Z/source` |
| 发行清单 | `.release-dist/20260926T103755035428Z/release-manifest.json`；`bundle_mode=onefile`，运行文件仅 1 份 |
| 源文件 | 382 份；当次构建后逐项 SHA-256 与当时应用源码/前端产物相符 |
| EXE SHA-256 | `520f356b4dcfe2641d8af1c69c8e6905a7379b0a7e0db806b29304c18ca6fff4` |
| 清单 SHA-256 | `c3d10b249ca9361630699fb7de3d85680b589eb7657158c7cd8ade0b799b496e` |
| 环境 | Windows x64，Python 3.13.15，PyInstaller 6.19.0；本机 Edge 无头浏览器 |
| 本轮回归 | 51 项子进程环境/计算限制/目录选择/行情适配/生命周期/回测测试通过，架构检查通过 |
| 实际 EXE 验证 | `%TEMP%\trade-frozen-smoke-A9VKaN`：见下文运行证据 |

本轮适配计算、行情和目录选择子进程的冻结环境，使它们复用启动器的资源目录，保留原有进程数、内存和取消限制。单文件与文件夹两种构建方式均保留，CI 中固定 PyInstaller 6.19.0；远端工作流没有在本轮执行。实际使用步骤见 [新版使用指南](QUICKSTART.md)，复现见 [打包说明](REBUILD_PACKAGING.md)。

### 此前文件夹版（历史验收）

以下记录对应较早的文件夹构建，不包含本轮单文件子进程适配：

`G:\CODE\trade\Trade\.release-dist\20260926T083554282331Z\TradeRebuild\TradeRebuild.exe`

可整体复制的压缩包：同目录 `TradeRebuild-Windows-x64.zip`（151300283 字节，约 144.3 MiB）。解压后运行 `TradeRebuild/TradeRebuild.exe`。ZIP 中 3763 个运行文件均重新读取并与发行清单的 SHA-256 核对通过。

请保留整个 `TradeRebuild` 文件夹。实际使用步骤见 [新版使用指南](QUICKSTART.md)，构建和跨平台工作流见 [打包说明](REBUILD_PACKAGING.md)。这是本地构建与验收的产物，其他平台的工作流尚未实际执行。

| 项目 | 本次记录 |
|---|---|
| 构建源快照 | `.release-build/20260926T083554282331Z/source` |
| 发行清单 | `.release-dist/20260926T083554282331Z/release-manifest.json` |
| 源文件 | 382 份；当次构建完成后逐项 SHA-256 与当时应用源码/前端产物相符 |
| 包内文件 | 3763 份，共 344824460 字节，约 328.9 MiB |
| EXE SHA-256 | `7f54f1253c35fc7c9bedfb05a806a8efd8bd0878135490ebef0ffd80176997f3` |
| 清单 SHA-256 | `c16050ca87be491143864192439d53c9eba48a4b5585e49a5f2b184420381512` |
| ZIP SHA-256 | `57e8022045aaae5f91312a38f3705b0178abc8c716e192ee6bd195ae69ef7950` |
| 环境 | Windows x64，Python 3.13.15，PyInstaller 6.19；本机 Edge 无头浏览器 |

发行包只收集新应用、构建前端、字体、离线股票库、迁移和受控 worker；未打包用户数据库、密钥、原数据或测试目录。测试均使用独立临时目录。

## 功能基线代码门禁（此前文件夹构建）

下列全量结果为此前功能基线；本轮打包适配的针对性测试和新 EXE 验证见上文，不将此前测试数当作重新执行的结果。

| 门禁 | 实际结果及范围 |
|---|---|
| `python -m pytest tests -q`（backend） | **1366 passed，0 failed/skip，423.19 秒**；包含原 app 和新 trade_app，不将旧 app 测试数充作新功能专属覆盖 |
| `npm run test` | **137 passed，1 skipped，38 文件**；跳过项是原 `src/test/backtestPage.test.tsx:383` 的导入报告测试；新增旧报告兼容由新模块的 API/浏览器测试验证 |
| 新应用 Vitest | **104 passed，24 文件**，是上行全前端测试的子集，不相加 |
| `npm run typecheck` / `npm run build:rebuild` | 通过；最终构建 797 modules |
| `npm run lint` | **0 error，113 warning**；未降低规则。生成空参数类型、渲染期 ref 更新、分享时间生成已修复 |
| 契约 / 架构 / 设计令牌 | 三项检查通过；OpenAPI 和 TypeScript 文件与当前路由相符 |

非阻塞信息：后端 dateutil/websockets 弃用和两个故意构造重复 ZIP 成员的警告；前端 Node localStorage、jsdom CSS、AntD 弃用警告。图表分包约 518 kB，有 Vite 体积提示，不宣称已达到所有设备性能目标。

## 此前版本实际运行证据

下列目录前缀均为本机 `%TEMP%`。每项只证明其列出的流程和环境；测试响应替身与真实外网服务分开标记。

| 验证 | 结果 / 证据目录 |
|---|---|
| 此前单文件 EXE `smoke-packaged-rebuild.mjs` | `trade-frozen-smoke-A9VKaN`：仅复制 EXE 到中文且带空格的空目录，并以该目录作为工作目录启动；UI/离线库、真实单股/组合/高级分析 worker、离线 lab CLI/worker、同步 CLI 入口、中文路径与 UTF-8、中文复盘/非空统计图 PDF、备份、复制/目录切换/重新连接/受控退出均通过；重新展开启动后中文复盘及回测指纹保持一致，两次正常退出后 `_MEI` 目录均清理 |
| 此前文件夹 EXE `smoke-packaged-rebuild.mjs` | `trade-frozen-smoke-5OlyZ7`：空目录启动、静态资源/离线库、单股回测、组合及高级分析子进程、离线 lab CLI/worker、同步 CLI 入口、中文路径和 UTF-8、独立复盘导出、含真实非空收益柱图的中文统计 PDF、备份、一致性复制/目录切换/重新连接及受控退出通过 |
| PDF 产物校验 | 同目录复盘 3 页、统计 2 页全部渲染查看，中文和日期可抽取；统计提取到 2025-01-02 的 10.0000% 收益。此前权益曲线/回撤/月收益与长表也经源码 PDF 专项测试和渲染 |
| 全页面回归 `smoke-all-pages.mjs` | `trade-all-pages-ui-8KuV21`：86 检查，实盘/模拟全部导航、明暗主题、320px、键盘、空态/展开详情，无页面异常和意外外网请求 |
| 完整信号工作区 | `trade-signals-ui-LaqLvf`：旧观察路径、完整上下文双策略/权重/模板修订、报告时点/排名/过滤/延迟入场、冻结 CSV、重载、375px 展开参数 |
| 研究表格导出 | `trade-table-exports-ui-eMerSg`：单股/组合交易 CSV/HTML、原生与旧平原全点 Excel、旧报告点详情、冻结交叉 CSV；改变未保存表单后导出字节保持不变 |
| 筛选导出 | `smoke-screener-exports.mjs`：四步漏斗和 B1，2 个流程 × PDF/Excel/CSV，选定证券/列与同一 selection SHA 可核对；50 证券长 PDF 中文分页通过 |
| 旧模拟和图片迁入 | `trade-legacy-final-ui-heUNvN`：独立账户续卖、坏现金拒绝、重复转换保护、图片字节/正文/账户隔离、重载及 320px |
| 旧报告原格式 | `trade-legacy-report-ui-IuVZqN`：原 `.ftbt` 预览/确认/平原点详情/6类下载/只读档案/删除；原 HTML 只作不可信二进制附件，重建 HTML 转义 |
| 偏好 / 统计 / 权益 PDF | `trade-preferences-statistics-ui-FhkbFt`：跨标签偏好冲突、买入/卖出日期过滤、批次口径、冻结权益报告 PDF 和窄屏 |
| 草稿 / AI / 分享卡最终修复 | build797 的 `smoke-ai-shortcuts.mjs` 和 `smoke-share-card.mjs` 通过真实主壳账户忙状态、取消、草稿隔离、320px 与完整不透明 PNG；AI 使用受控本地响应，未验证真实模型准确率 |
| 认证代理 | `trade-proxy-smoke-6bba0pm6`：实际 Caddy、本机 TLS 明确信任测试证书、认证、Secure Cookie、CSRF、伪造代理头拒绝/覆盖及静态资源；未修改系统信任、hosts 或用户服务器 |

专项实现与旧版差异见 [功能核对](FEATURE_COMPLETION_AUDIT.md)、[信号工作区](SIGNAL_WORKSPACE_ACCEPTANCE.md)、[组合执行](PORTFOLIO_EXECUTION.md)、[研究实验室](STRATEGY_LAB.md)、[导出验收](RESEARCH_TABLE_EXPORT_ACCEPTANCE.md)。

[28 个原业务页面迁移映射](PAGE_MIGRATION_MAP.md) 已逐项与原始路由清单核对，无重复或遗漏；每项分别记录新入口、功能 ID、关键行为和专项证据，R707 已完成。

## 并发交互基准

[TR-020原始记录与复现](INTERACTIVE_LOAD_BENCHMARK.md)：10万成交、5000复盘、1万事件摘要，真实回测child与慢HTTP行情请求竞争时，100对查询/保存均成功；87对严格重叠采样查询p95 **42.99ms**、保存p95 **23.45ms**。这是一台Windows机器、1证券×1200日计算样本的基线，不代表5000证券全规模或所有平台SLA。

## 尚不能宣称完成的验收

- macOS/Linux 原生包、目录选择器、系统 PDF 默认查看器、签名/公证及公开域名证书部署需要对应环境实际运行。
- 真实 AI 质量、第三方行情在用户网络和目标日期的覆盖、真实 TDX/SSH 传输不由本机替身测试证明。
- 工程强化项仍在 [开发 TODO](DEVELOPMENT_TODO.md) 逐条列出，包括全部依赖锁定、全规模/全尺寸性能矩阵、统一资源观测/引用回收、组件样例页及完整故障演练。当前 F01–F78 核对未保留明确业务主链实施缺口，不能据此把这些额外门禁标为全部完成。
- 保留原始数据是既定范围；程序不会为了验收自动迁入用户真实账本。

未来改动应用源码或前端产物后，须重新构建并运行包内验证；本页每份构建的结论绑定对应清单和 EXE 指纹。
