"""闪记卡片、设置、行情、统计、数据导出。"""

import json
import random
import shutil
import sys
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import DATA_DIR, ROOT_DIR, engine, get_db
from ..models import (
    CapitalFlow, DailyReview, FlashCard, MonthlyReview, PendingTrade, Setting, Snapshot, Trade, WeeklyReview,
)
from ..services import ai as ai_svc
from ..services import backup as backup_svc
from ..services import capital_estimate as capital_est_svc
from ..services import folder_dialog
from ..services.storage import copy_data_directory, publish_location
from ..services import market as market_svc
from ..services import netvalue, pdf_export as pdf_export_svc, rounds as rounds_svc, settings as settings_svc, stats

router = APIRouter(prefix="/api", tags=["misc"])


# ---------- 系统 ----------

class MoveDataIn(BaseModel):
    target_dir: str


DATA_SUBDIR = "TradingMS-data"


def _resolve_migration_target(raw: str) -> tuple[Path, str]:
    """解析迁移目标：空目录直接用，非空目录自动使用子文件夹。"""
    supplied = Path(raw).expanduser()
    if not supplied.is_absolute() or '\x00' in raw:
        raise HTTPException(400, '目标目录必须是有效绝对路径')
    base = supplied.resolve()
    source = DATA_DIR.resolve()
    if base == source or base in source.parents or source in base.parents or base == Path(base.anchor):
        raise HTTPException(400, "请使用原目录之外的独立目录，不能选择父目录、子目录或根目录")
    if base == ROOT_DIR.resolve():
        raise HTTPException(400, "不能使用程序根目录作为数据目录")

    if not base.exists():
        return base, "将创建新目录"
    if not base.is_dir():
        raise HTTPException(400, '目标路径不是目录')

    if not any(base.iterdir()):
        return base, "使用所选空目录"

    sub = base / DATA_SUBDIR
    if sub.exists() and any(sub.iterdir()):
        if sub.resolve() == DATA_DIR.resolve():
            raise HTTPException(400, "该位置已是当前数据目录")
        raise HTTPException(
            400,
            f"子目录「{DATA_SUBDIR}」已存在且非空，请更换位置或先清空该文件夹",
        )
    return sub, f"所选目录非空，将自动使用子文件夹 {DATA_SUBDIR}"


@router.get("/system/pick-folder")
def pick_folder_api():
    """弹出系统文件夹选择框（Windows）。"""
    if sys.platform != "win32":
        raise HTTPException(501, "文件夹选择仅支持 Windows")
    try:
        path, err = folder_dialog.pick_folder("选择数据存储位置")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"无法打开文件夹选择框：{exc}") from exc
    if err:
        raise HTTPException(500, f"无法打开文件夹选择框：{err}")
    if path and not folder_dialog.is_plausible_folder_path(path):
        return {"path": None, "cancelled": True}
    if not path:
        return {"path": None, "cancelled": True}
    return {"path": path, "cancelled": False}


@router.post("/system/preview-data-dir")
def preview_data_dir(body: MoveDataIn):
    """预览迁移后的实际目录路径。"""
    target, note = _resolve_migration_target(body.target_dir)
    return {"target_dir": str(target), "note": note}


@router.post("/system/move-data")
def move_data(body: MoveDataIn, request: Request):
    """Copy a consistent snapshot; keep the source alive until an explicit restart."""
    target, _note = _resolve_migration_target(body.target_dir)
    state = request.app.state
    with state.storage_lock:
        if state.storage_copying or state.data_switch_pending or state.active_writes > 1:
            raise HTTPException(409, '仍有操作正在保存，请完成后再复制目录')
        state.storage_copying = True
    try:
        copy_data_directory(DATA_DIR, target)
        publish_location(ROOT_DIR / 'data_location.txt', target)
        with state.storage_lock:
            state.data_switch_pending = True
    except (OSError, ValueError) as exc:
        raise HTTPException(400 if isinstance(exc, ValueError) else 503,
                            '目录复制未完成，原数据仍可使用；请检查目标路径、权限和空间后重试') from exc
    finally:
        with state.storage_lock:
            state.storage_copying = False
    return {'ok': True, 'new_dir': str(target), 'active_dir': str(DATA_DIR), 'need_restart': True,
            'restart_required': True, 'disposition': 'copied_restart_required'}


# ---------- 闪记卡片 ----------

class CardIn(BaseModel):
    content: str
    tags: str = ""


@router.get("/cards")
def list_cards(db: Session = Depends(get_db)):
    rows = db.query(FlashCard).order_by(FlashCard.id.desc()).all()
    return [
        {"id": r.id, "content": r.content, "tags": r.tags,
         "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@router.post("/cards")
def add_card(body: CardIn, db: Session = Depends(get_db)):
    if not body.content.strip():
        raise HTTPException(400, "内容不能为空")
    row = FlashCard(content=body.content.strip(), tags=body.tags.strip())
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db)):
    row = db.get(FlashCard, card_id)
    if row is None:
        raise HTTPException(404, "卡片不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/cards/random")
def random_card(db: Session = Depends(get_db)):
    rows = db.query(FlashCard).all()
    if not rows:
        return None
    # 以当天日期为种子，保证同一天展示同一张
    rng = random.Random(date.today().toordinal())
    r = rng.choice(rows)
    return {"id": r.id, "content": r.content, "tags": r.tags,
            "created_at": r.created_at.isoformat()}


# ---------- 设置 ----------

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    values = settings_svc.get_all(db)
    # 两套 AI Key 各自的「已配置」标记，密文不下发
    values["ai_score_api_key_set"] = bool(values.get("ai_score_api_key") or values.get("ai_api_key"))
    values["ai_ocr_api_key_set"] = bool(values.get("ai_ocr_api_key") or values.get("ai_api_key"))
    values["ai_score_api_key"] = ""
    values["ai_ocr_api_key"] = ""
    # 旧 key（兼容）也不下发
    values.pop("ai_api_key", None)
    values["ai_api_key_set"] = values["ai_score_api_key_set"]
    values["data_dir"] = str(DATA_DIR)
    return values


class SettingsIn(BaseModel):
    values: dict


# 密钥类字段：留空表示「不修改已保存值」
_SECRET_KEYS = {"ai_score_api_key", "ai_ocr_api_key", "ai_api_key"}


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db)):
    values = {k: v for k, v in body.values.items() if k in settings_svc.DEFAULTS}
    # 空 key 表示不修改已保存的 key
    for k in list(values):
        if k in _SECRET_KEYS and values[k] == "":
            values.pop(k, None)
    if "pdf_export_dir" in values:
        pdf_dir = str(values["pdf_export_dir"]).strip()
        if pdf_dir and not folder_dialog.is_plausible_folder_path(pdf_dir):
            values["pdf_export_dir"] = ""
    settings_svc.set_many(db, values)
    return {"ok": True}


class TestAIIn(BaseModel):
    kind: str  # "score" | "ocr"
    # 可选：传入表单当前值（未保存也能测试）。为空则回退已保存配置。
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@router.post("/settings/test-ai")
def test_ai(body: TestAIIn, db: Session = Depends(get_db)):
    """测试 AI 配置连通性。优先用传入值，为空则回退已保存值（含旧 key 兼容）。"""
    if body.kind == "ocr":
        base = body.base_url or settings_svc.get(db, "ai_ocr_base_url") or settings_svc.get(db, "ai_base_url")
        key = body.api_key or settings_svc.get(db, "ai_ocr_api_key") or settings_svc.get(db, "ai_api_key")
        model = body.model or settings_svc.get(db, "ai_ocr_vision_model") or settings_svc.get(db, "ai_vision_model")
    else:
        base = body.base_url or settings_svc.get(db, "ai_score_base_url") or settings_svc.get(db, "ai_base_url")
        key = body.api_key or settings_svc.get(db, "ai_score_api_key") or settings_svc.get(db, "ai_api_key")
        model = body.model or settings_svc.get(db, "ai_score_text_model") or settings_svc.get(db, "ai_text_model")
    return ai_svc.test_connection(base, key, model)


@router.post("/settings/test-market")
def test_market(db: Session = Depends(get_db)):
    """测试行情源：拉取指数与样本 ETF，返回命中数据源。"""
    probes = [
        ("000001.SH", "上证指数"),
        ("399001.SZ", "深证成指"),
        ("588710", "科创半导体ETF"),
    ]
    lines: list[str] = []
    ok = False
    for code, label in probes:
        result = market_svc.get_daily(db, code, limit=3)
        klines = result.get("klines") or []
        source = result.get("source") or "无"
        if klines:
            ok = True
            close = klines[-1]["close"]
            lines.append(f"{label}({code}): 源={source}，收盘 {close:.4f}")
        else:
            errs = result.get("errors") or []
            lines.append(f"{label}({code}): 失败 — {'; '.join(errs) if errs else '无数据'}")
    return {
        "ok": ok,
        "message": "\n".join(lines),
    }


class ImportJsonIn(BaseModel):
    data: dict


@router.post("/import/json")
def import_json(body: ImportJsonIn, db: Session = Depends(get_db)):
    """从 export/json 备份恢复全量数据（覆盖现有数据）。"""
    try:
        counts = backup_svc.restore_backup(db, body.data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"恢复失败: {exc}") from exc
    return {"ok": True, "counts": counts}


# ---------- 行情 ----------

@router.get("/market/search/stocks")
def search_stocks(q: str = "", limit: int = 12):
    return market_svc.search_stocks(q, limit=min(limit, 20))


@router.get("/market/lookup/stock")
def lookup_stock(q: str = ""):
    """按代码或名称精确解析单只股票，供前端自动补全。"""
    query = q.strip()
    if not query:
        raise HTTPException(400, "请提供代码或名称")
    digits = "".join(ch for ch in query if ch.isdigit())
    if len(digits) == 6:
        hit = market_svc.lookup_by_code(digits)
        if hit:
            return hit
    code, name = market_svc.resolve_stock(query, query if not digits else "")
    if code and len("".join(ch for ch in code if ch.isdigit())) == 6:
        hit = market_svc.lookup_by_code(code)
        if hit:
            return hit
        return {"code": code, "name": name}
    raise HTTPException(404, "未找到匹配股票")


@router.get("/market/{code}/close")
def market_close_on_day(code: str, day: date, db: Session = Depends(get_db)):
    """指定日收盘价（取该日或之前最近交易日），比拉全量 K 线更轻。"""
    close = market_svc.close_on_day(db, code, day.isoformat())
    return {"code": code, "day": day.isoformat(), "close": close}


@router.get("/market/{code}")
def market_daily(code: str, limit: int = 120, db: Session = Depends(get_db)):
    return market_svc.get_daily(db, code, limit)


# ---------- 统计总览 ----------

@router.get("/stats/overview")
def overview(db: Session = Depends(get_db)):
    rate, count = netvalue.node_config(db)
    points = netvalue.build_series(db)
    rounds = rounds_svc.build_rounds(db)
    events = netvalue.compute_node_events(points, rate, count)
    start_day = points[0].day if points else None
    return {
        "state": netvalue.current_state(points, rate, count),
        "curve": stats.nav_curve(points),
        "max_drawdown_pct": stats.max_drawdown(points),
        "weekly_returns": stats.period_returns(points, stats.week_key),
        "monthly_returns": stats.period_returns(points, stats.month_key),
        "round_stats": rounds_svc.round_stats(rounds),
        "node_timing": netvalue.node_timing(events, start_day),
        "missing_reviews": stats.missing_reviews(db),
        "missing_snapshots": stats.missing_snapshots(db),
    }


@router.get("/stats/day/{day}")
def day_detail(day: date, db: Session = Depends(get_db)):
    """某日净值、交易、持仓与复盘摘要（供首页曲线点击）。"""
    points = netvalue.build_series(db)
    nav_point = next((p for p in points if p.day == day), None)
    curve = stats.nav_curve(points)
    curve_point = next((p for p in curve if p["date"] == day.isoformat()), None)
    trades = (
        db.query(Trade)
        .filter(Trade.trade_date == day)
        .order_by(Trade.id)
        .all()
    )
    snap = db.query(Snapshot).filter(Snapshot.snap_date == day).first()
    positions: list[dict] = []
    snapshot_info = None
    if snap:
        positions = json.loads(snap.positions or "[]")
        if not isinstance(positions, list):
            positions = []
        snapshot_info = {
            "total_assets": snap.total_assets,
            "available_cash": snap.available_cash,
            "position_value": snap.position_value,
        }
    elif not positions:
        est = capital_est_svc.estimate_snapshot(db, day)
        if est.get("ok"):
            positions = est.get("positions") or []
            snapshot_info = {
                "total_assets": est.get("total_assets"),
                "available_cash": est.get("cash"),
                "position_value": est.get("position_value"),
                "estimated": True,
            }
    review = db.query(DailyReview).filter(DailyReview.review_date == day).first()
    return {
        "date": day.isoformat(),
        "nav": round(nav_point.nav, 4) if nav_point else None,
        "assets": round(nav_point.assets, 2) if nav_point else None,
        "drawdown_pct": curve_point["drawdown_pct"] if curve_point else None,
        "trades": [
            {
                "id": t.id, "code": t.code, "name": t.name, "side": t.side,
                "price": t.price, "qty": t.qty,
                "fees": round(t.fee_commission + t.fee_stamp + t.fee_transfer, 2),
            }
            for t in trades
        ],
        "positions": positions,
        "snapshot": snapshot_info,
        "has_review": review is not None,
        "ai_summary": review.ai_summary if review else "",
    }


# ---------- 导出 ----------

class PdfExportIn(BaseModel):
    html: str
    filename: str


def _safe_pdf_filename(name: str) -> str:
    cleaned = "".join(c if c not in '<>:"/\\|?*' else "_" for c in name.strip())
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned or "export.pdf"


@router.post("/export/pdf")
def export_pdf(body: PdfExportIn, db: Session = Depends(get_db)):
    """将 HTML 渲染为 PDF 并保存到设置的 pdf_export_dir。"""
    export_dir = settings_svc.get(db, "pdf_export_dir").strip()
    if not export_dir:
        raise HTTPException(400, "请先在设置中配置 PDF 保存路径")
    target_dir = Path(export_dir).expanduser().resolve()
    filename = _safe_pdf_filename(body.filename)
    output_path = target_dir / filename
    try:
        pdf_export_svc.save_html_as_pdf(body.html, output_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True, "path": str(output_path), "filename": filename}


@router.get("/export/json")
def export_json(db: Session = Depends(get_db)):
    payload = backup_svc.export_backup(db)
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f"attachment; filename=tradingms-backup-{date.today().isoformat()}.json"},
    )


@router.get("/export/markdown")
def export_markdown(db: Session = Depends(get_db)):
    lines = [f"# Trading MS 复盘导出（{date.today().isoformat()}）", ""]
    reviews = db.query(DailyReview).order_by(DailyReview.review_date).all()
    for r in reviews:
        lines += [f"## {r.review_date.isoformat()} 每日复盘", ""]
        if r.market_observation:
            lines += ["### 盘面观察", r.market_observation, ""]
        if r.decision_review:
            lines += ["### 决策复盘", r.decision_review, ""]
        if r.mistakes:
            lines += ["### 错误教训", r.mistakes, ""]
        scores = json.loads(r.scores or "{}")
        if scores:
            lines.append("### 打分")
            for dim, item in scores.items():
                final = item.get("final")
                comment = item.get("comment", "")
                lines.append(f"- {dim}: {final if final is not None else '-'} {comment}")
            lines.append("")
        if r.next_market_forecast or r.next_position_plan:
            lines += ["### 次日预研",
                      f"大盘预判：{r.next_market_forecast}",
                      f"仓位计划：{r.next_position_plan}",
                      f"风险预案：{r.next_risk_plan}", ""]
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=tradingms-reviews-{date.today().isoformat()}.md"},
    )
