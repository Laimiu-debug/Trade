# Trade · 交易与复盘工作台

本地运行的交易工作台：实盘账本与净值、模拟交易、行情样本、策略研究与组合回测、每日/周期复盘、AI 辅助和备份导出，全部在一个应用里。

- 后端：Python 3.11+（CI 使用 3.13），FastAPI，代码在 [`backend/trade_app`](backend/trade_app)
- 前端：React 19 + Vite，Node.js `^20.19.0 || >=22.12.0`，代码在 [`frontend/src/rebuild`](frontend/src/rebuild)
- 设计系统：[docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md)，令牌源 [docs/design-tokens.json](docs/design-tokens.json)

> 早期的 final-trade / LaimiuTrade 两套应用已于 tag `legacy-final` 之后移除。它们的 28 个页面与 F01–F78 功能均已在新版承接（见 [页面映射](docs/PAGE_MIGRATION_MAP.md)、[功能核对](docs/FEATURE_COMPLETION_AUDIT.md)）；旧数据可在「系统设置 → 旧资料导入」预览后迁入。

## 快速开始

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
cd ..\frontend
npm ci
npm run build
cd ..
python scripts/run_trade_rebuild.py
```

启动器检查构建产物、从 8011 起寻找空闲本地端口、等待健康检查后打开浏览器；可用 `--no-browser`、`--port`、`--data-dir`、`--state-file`。日志写入数据目录的 `launch.log`，Ctrl+C 只结束本次启动的服务。打包与跨平台构建见 [发行说明](docs/REBUILD_PACKAGING.md)，使用步骤见 [使用指南](docs/QUICKSTART.md)。

### 开发模式

两个终端分别运行：

```powershell
cd backend
.venv\Scripts\python -m uvicorn trade_app.main:app --host 127.0.0.1 --port 8011
```

```powershell
cd frontend
npm run dev
```

打开 `http://127.0.0.1:4174/rebuild.html`。若改用其他后端端口，启动前设置 `TRADE_REBUILD_PORT` 为同一端口，否则浏览器的同源校验会拒绝请求。

## 数据

- 默认保存在用户目录 `TradeRebuild/trade.sqlite`；启动前设置 `TRADE_REBUILD_DATA_DIR` 可指定目录。
- 实盘账本、模拟资金和研究回测分开保存。
- 行情样本接收按日期升序的 CSV（`date,open,high,low,close,volume,available_at,amount`，后两列可选）和通达信 `.day`。导入通达信前设置 `TRADE_TDX_ROOT` 为安装目录或 `vipdoc`，原文件只读。
- 在线日线可从 AKShare / 东方财富或 BaoStock 获取，失败时回退到另一来源并显示实际来源；结果保存为独立冻结样本。旧 AkShare / BaoStock CSV 缓存可设置 `TRADE_AKSHARE_CACHE_DIR` / `TRADE_BAOSTOCK_CACHE_DIR` 后导入。

备份可在页面连接本地服务时从 `/api/v1/backups/export` 下载；离线验证并恢复到**新的空目录**：

```powershell
python scripts/trade_rebuild_backup.py verify .\trade-backup.zip
python scripts/trade_rebuild_backup.py restore .\trade-backup.zip .\restored-trade-data
```

## 功能文档

| 主题 | 文档 |
|---|---|
| 组合执行口径（T+1、费用、因果退出） | [PORTFOLIO_EXECUTION.md](docs/PORTFOLIO_EXECUTION.md) |
| 研究脚本与策略实验室 | [STRATEGY_LAB.md](docs/STRATEGY_LAB.md) |
| 经典参考策略 | [CLASSIC_STRATEGIES.md](docs/CLASSIC_STRATEGIES.md) |
| 复盘与统计导出 | [EXPORT_GUIDE.md](docs/EXPORT_GUIDE.md) |
| 增量行情同步 CLI | [MARKET_SYNC_CLI.md](docs/MARKET_SYNC_CLI.md) |
| 通达信只读打包与传送 | [TDX_BUNDLE_GUIDE.md](docs/TDX_BUNDLE_GUIDE.md) |
| 可选远程部署 | [REMOTE_DEPLOYMENT.md](docs/REMOTE_DEPLOYMENT.md) |
| 架构与技术要求 | [REBUILD_GUIDE.md](docs/REBUILD_GUIDE.md)、[TECHNICAL_REQUIREMENTS.md](docs/TECHNICAL_REQUIREMENTS.md) |

## 测试

```powershell
cd backend
.venv\Scripts\python -m pytest tests -q
cd ..
python scripts/check_rebuild_architecture.py
python scripts/generate_rebuild_tokens.py --check
python scripts/check_design_tokens.py
cd frontend
npm run typecheck
npm run test
npm run lint
```

新版算法与原实现的逐项对照测试使用录制在 [`backend/tests/fixtures/legacy_oracle`](backend/tests/fixtures/legacy_oracle) 的原实现输出（说明见 [legacy_oracle.py](backend/tests/legacy_oracle.py)）。浏览器冒烟脚本在 `frontend/scripts/smoke-*.mjs`，需要先 `npm run build` 并安装 Edge 或 Chromium。
