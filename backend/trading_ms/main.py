import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import UPLOAD_DIR, Base, engine, ensure_schema
from .routers import capital, misc, reviews, trades

Base.metadata.create_all(bind=engine)
ensure_schema()

app = FastAPI(title="Trading MS", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
