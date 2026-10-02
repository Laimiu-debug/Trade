# Trade · 统一交易工作台

## 从零重构设计文档

新版本的设计入口：[Trade 重构文档](docs/REBUILD_GUIDE.md)。独立应用覆盖实盘账本、模拟执行、行情筛选、统一策略目录、组合回测与参数实验、AI辅助、结构化复盘和备份导出。原有 16 项策略与新增经典参考规则的分组、执行入口及核对证据见 [策略整合审查](docs/STRATEGY_CONSOLIDATION_AUDIT.md)。逐项实现、兼容边界与发布验证以 [F01–F78 功能核对](docs/FEATURE_COMPLETION_AUDIT.md) 为准；历史阶段记录见 [实施状态](docs/IMPLEMENTATION_STATUS.md)。

实施约束见 [重构技术要求](docs/TECHNICAL_REQUIREMENTS.md)：模块边界、数据重算、共享计算规则、研究可复现、任务资源预算和验收标准。

---

## 新版重构应用

Windows 本地运行包与验证记录见 [发行验收](docs/RELEASE_ACCEPTANCE.md)，操作步骤见 [新版使用指南](docs/QUICKSTART.md)。

2026-10-03 全项目代码检查与优化记录见 [代码质量检查](docs/CODE_QUALITY_AUDIT.md)：账本身份与 FIFO、会话恢复、防重复提交、备份完整性、旧复盘保存与数据目录迁移，以及构建和测试流程。

2026-09-26 页面更新：[研究/行情/模拟工作区拆分](docs/UI_LAYOUT_AUDIT.md)，修复深色表单、资讯阅读和账户切换隔离；新增 [Donchian、SMA、Bollinger 三种经典参考规则](docs/CLASSIC_STRATEGIES.md)，规则和实现边界可逐项核对。

源码环境：Python 3.11+（CI 使用 3.13），Node.js `^20.19.0 || >=22.12.0`。在两个终端分别运行：

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn trade_app.main:app --host 127.0.0.1 --port 8011
```

```powershell
cd frontend
npm ci
npm run dev:rebuild
```

打开 `http://127.0.0.1:4174/rebuild.html`。数据默认保存在用户目录 `TradeRebuild/trade.sqlite`，也可在启动后端前设置环境变量 `TRADE_REBUILD_DATA_DIR`。新应用不读取或修改旧应用的数据目录。

如先在 `frontend` 执行 `npm run build:rebuild`，后端也会提供构建后的界面：`http://127.0.0.1:8011/rebuild.html`，无需单独启动 Vite。

源码一键启动可在项目根目录运行 `python scripts/run_trade_rebuild.py`。启动器检查构建产物、寻找 8011 起的空闲本地端口、等待健康检查后打开浏览器；可用 `--no-browser`、`--port`、`--data-dir` 和 `--state-file` 指定行为。日志保存在新应用数据目录的 `launch.log`，按 Ctrl+C 只结束本次启动的服务。页面提供受控退出、复制/恢复到新目录及明确确认切换；Windows打包与跨平台构建入口见 [发行说明](docs/REBUILD_PACKAGING.md)。

新界面顶部可新建模拟账户；模拟委托使用当前模拟日期、限价和冻结费用，可手动确认成交价，也可选择冻结样本按次日开盘价撮合。实盘账本、模拟资金和研究回测分开保存。行情样本页接收按日期升序的 CSV（`date,open,high,low,close,volume,available_at,amount`，最后两列可选）及通达信 `.day`。通达信导入前设置 `TRADE_TDX_ROOT` 为安装目录或 `vipdoc`；证券统一为 `sh/sz/bj` 身份，原文件只读。历史可得时间未知时严格研究排除，非严格模式明确标注假设。

单股研究、原始 S1–S9、aligned事件矩阵和传统策略组合各自有独立运行入口。组合共享资金、T+1、费用/滑点和因果退出，支持分块恢复、参数平原、独立测试期Walk-forward、高级分析、日终条件计划和冻结报告。日线不能证明分钟触达顺序或历史全市场成员，详见 [组合执行口径](docs/PORTFOLIO_EXECUTION.md) 和 [独立研究工具](docs/STRATEGY_LAB.md)。

行情页还可联网从 AKShare / 东方财富或 BaoStock 获取单股不复权日线，选择全量或增量同步。自动模式默认先试 BaoStock，也可改为先试 AKShare，界面会记住来源偏好；来源失败后回退到另一提供方，并显示实际来源。在线数据保存为独立冻结样本，失败不覆盖旧样本。AKShare 与 BaoStock 的成交量原始单位不同，入库时统一为“股”；历史可得时间保持未知。BaoStock 客户端在隔离进程运行，超时后结束该进程。在线接口的字段与单位依据 [AKShare 股票数据文档](https://akshare.akfamily.xyz/data/stock/stock.html) 和 [BaoStock 官方资料](https://www.baostock.com/mainContent?file=stockKData.md)。批量在线任务最多 50 只，逐只保存结果和进度，可取消、重试失败代码，并在服务重启后继续未处理代码；目前尚无定时同步与通达信自动上行任务。

旧版 AkShare / Baostock 日线缓存也可导入：启动前分别设置 `TRADE_AKSHARE_CACHE_DIR` 或 `TRADE_BAOSTOCK_CACHE_DIR` 为对应同步脚本的 CSV 输出目录，在行情页选择实际来源和代码。导入只读取本地缓存，不会触发联网同步；新样本与原 CSV 分开保存。行情页可按精确日期查冻结收盘价、双击日 K 线读取同日通达信 `.lc1` 原始一分钟分时，并检查文件哈希及数据日期。分时当前实时读取原文件，不随日线备份。

研究页可把已触发的观察信号转成模拟委托草稿，并按手数、金额或现金比例估算数量和费用。情绪估值页保留旧版五因子公式与示例参数；示例非实时行情，盈利、市值与基准 PE 需人工核对。

研究页支持四步选股漏斗、B1多周期、趋势/涨停梯队/板块代理资金/异动筛选，保留实际参数、日期和缺失原因。流通股本可人工填写或只读提取 `base.dbf` 并记录来源哈希；未来或缺失股本不会充作有效换手率。观察池在服务端版本化保存，支持人工成员、顺序、B1交集和显式自动更新；旧本机状态需预览确认迁入。筛选信号经明确选择生成模拟草稿，资产比例预算使用冻结估值报告和当前钱包版本。

日、周、月复盘可导出 Markdown、离线中文 PDF 与浏览器打印预览；实盘/模拟统计可导出PDF、Excel和逐表CSV。人工正文与评分有按账户隔离的本机草稿和版本冲突比较，AI建议需单独采用。打印署名、独立命令和系统PDF预览见 [导出指南](docs/EXPORT_GUIDE.md)。

顶部“导出数据”可下载当前账户 Excel 工作簿：实盘包含交易、资金、快照、回合和净值，模拟盘包含委托、成交与持仓；Meta 页注明账户、版本及统计状态。原有逐表 CSV 仍可下载。已完成的单股回测报告也可下载包含输入版本、参数、费用配置、交易和资金曲线的 Excel 工作簿。

备份可在页面已连接本地服务时访问 `/api/v1/backups/export` 下载；离线验证和恢复到一个**新的空目录**：

```powershell
python scripts/trade_rebuild_backup.py verify .\trade-backup.zip
python scripts/trade_rebuild_backup.py restore .\trade-backup.zip .\restored-trade-data
```

测试和架构边界检查：

```powershell
$env:PYTHONPATH='backend'
python -m pytest backend/tests -q
python scripts/check_rebuild_architecture.py
cd frontend
npm run build:rebuild
npx vitest run src/rebuild
```

独立工具：[增量行情同步](docs/MARKET_SYNC_CLI.md)、[通达信只读打包和显式传送](docs/TDX_BUNDLE_GUIDE.md)、[研究脚本](docs/STRATEGY_LAB.md)、[受认证的可选远程部署](docs/REMOTE_DEPLOYMENT.md)。旧资料从系统设置主动选文件导入，先预览与校验，再确认写入新资料；旧数据库和附件不会被自动扫描或改写。

---

## 当前过渡整合版

本仓库将 [final-trade](https://github.com/Laimiu-debug/final-trade) 的选股、信号、模拟交易和策略回测，与 [LaimiuTrade](https://github.com/Laimiu-debug/LaimiuTrade) 的实盘记账、资金净值、每日及周期复盘接入同一个本地工作台。

主界面基于 final-trade，左侧「实盘复盘」入口打开波段复盘志。两套 FastAPI 功能由同一个后端进程提供：原工作台接口位于 `/api`，实盘复盘接口和页面位于 `/journal-app`。模拟交易与实盘账本仍各自存储，避免混合两类交易记录。

## Windows 开发启动

需要 Python 3.11+ 和 Node.js `^20.19.0 || >=22.12.0`（建议 Node.js 22；CI 使用 Python 3.13）。首次使用：

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
cd ..\frontend
npm ci
cd ..\journal-frontend
npm ci
cd ..
.\start-dev.ps1
```

打开 `http://127.0.0.1:4173`。启动脚本会构建实盘复盘页面，随后启动统一后端和主界面开发服务。实盘复盘也可直接通过 `http://127.0.0.1:8010/journal-app/` 访问。

## 源码单端口运行

分别在 `frontend/` 与 `journal-frontend/` 执行 `npm ci` 和 `npm run build`，安装 `backend/requirements.txt` 后运行：

```powershell
cd backend
python desktop_launcher.py --no-browser
```

默认访问 `http://127.0.0.1:8010`。Windows 和 macOS 打包脚本会同时构建两套前端并打入安装包。

## 数据说明

- `final-trade` 延用用户目录中的 `.tdx-trend` 配置和模拟交易数据。
- 实盘复盘默认使用本仓库 `data/laimiutrade.db` 及 `data/uploads/`。
- 原 `LaimiuTrade` 项目的 `data/` 没有复制或改动；本阶段不自动迁移数据。如需沿用旧库，可在确认路径和备份后使用复盘模块已有的数据目录设置。
- 本地数据与密钥不会进入 Git 仓库。

## 目前的整合边界

实盘复盘通过主界面嵌入原功能页面，界面主题和部分导航仍保留其原设计。模拟交易和实盘记账没有自动互写；后续可按实际使用流程统一交互及数据模型。
