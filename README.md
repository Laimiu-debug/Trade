# Trade · 统一交易工作台

本仓库将 [final-trade](https://github.com/Laimiu-debug/final-trade) 的选股、信号、模拟交易和策略回测，与 [LaimiuTrade](https://github.com/Laimiu-debug/LaimiuTrade) 的实盘记账、资金净值、每日及周期复盘接入同一个本地工作台。

主界面基于 final-trade，左侧「实盘复盘」入口打开波段复盘志。两套 FastAPI 功能由同一个后端进程提供：原工作台接口位于 `/api`，实盘复盘接口和页面位于 `/journal-app`。模拟交易与实盘账本仍各自存储，避免混合两类交易记录。

## Windows 开发启动

需要 Python 3.10+ 和 Node.js 18+。首次使用：

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
