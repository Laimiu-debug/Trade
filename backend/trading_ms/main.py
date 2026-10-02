import sys
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import threading

from .database import UPLOAD_DIR, Base, engine, ensure_schema
from .routers import capital, misc, reviews, trades

Base.metadata.create_all(bind=engine)
ensure_schema()

app = FastAPI(title="Trading MS", version="0.1.0")
app.state.storage_lock = threading.RLock()
app.state.active_writes = 0
app.state.storage_copying = False
app.state.data_switch_pending = False


@app.exception_handler(ValueError)
def invalid_legacy_value(_request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)

allowed_origins = [
    f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (4173, 5173)
]
allowed_origins.extend(origin.strip() for origin in os.environ.get('TRADING_MS_ALLOWED_ORIGINS', '').split(',')
                       if origin.strip() and origin.strip() != '*')

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware('http')
async def reject_untrusted_origin(request, call_next):
    origin = request.headers.get('origin')
    local_same_origin = (request.url.hostname in {'localhost', '127.0.0.1', 'testserver'}
                         and origin == f'{request.url.scheme}://{request.url.netloc}')
    if origin and origin not in allowed_origins and not local_same_origin:
        return JSONResponse({'detail': '不支持的请求来源'}, status_code=403)
    unsafe = request.method not in {'GET', 'HEAD', 'OPTIONS'}
    if unsafe:
        with app.state.storage_lock:
            if app.state.storage_copying or app.state.data_switch_pending:
                return JSONResponse({'detail': '数据目录正在复制或等待切换，请完全退出并重新启动后再保存'}, status_code=409)
            app.state.active_writes += 1
    try:
        return await call_next(request)
    finally:
        if unsafe:
            with app.state.storage_lock:
                app.state.active_writes -= 1

app.include_router(capital.router)
app.include_router(trades.router)
app.include_router(reviews.router)
app.include_router(misc.router)

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# 生产模式：托管前端构建产物
if getattr(sys, "frozen", False):
    FRONTEND_DIST = Path(getattr(sys, "_MEIPASS")) / "journal_frontend_dist"
else:
    FRONTEND_DIST = Path(__file__).resolve().parents[2] / "journal-frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
