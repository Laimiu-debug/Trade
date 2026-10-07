# Trade backend

FastAPI 服务，入口 `trade_app.main:app`。安装、运行和测试见仓库根目录 [README](../README.md)。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn trade_app.main:app --host 127.0.0.1 --port 8011
.venv\Scripts\python -m pytest tests -q
```

模块边界由 `python ../scripts/check_rebuild_architecture.py` 检查，设计见 [docs/REBUILD_GUIDE.md](../docs/REBUILD_GUIDE.md)。
