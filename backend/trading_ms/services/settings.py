from sqlalchemy.orm import Session

from ..models import Setting

DEFAULTS: dict[str, str] = {
    # 目标系统
    "wave_pct": "30",    # 每节点涨幅 %
    "node_count": "50",  # 胜利节点总数
    # 费率
    "commission_rate": "0.00025",   # 佣金 万2.5
    "commission_min": "5",          # 最低佣金
    "stamp_tax_rate": "0.0005",     # 印花税(卖出) 万5
    "transfer_fee_rate": "0.00001", # 过户费 十万分之一
    # 行情
    "tdx_path": r"D:\new_tdx\vipdoc",
    "market_priority": "tdx,akshare,web",
    # AI 操作打分（文本模型）
    "ai_score_base_url": "",
    "ai_score_api_key": "",
    "ai_score_text_model": "",
    # AI 截图识别（视觉模型）
    "ai_ocr_base_url": "",
    "ai_ocr_api_key": "",
    "ai_ocr_vision_model": "",
    # —— 以下旧 key 仅作向后兼容回退（存量用户未迁移时使用）——
    "ai_base_url": "",
    "ai_api_key": "",
    "ai_text_model": "",
    "ai_vision_model": "",
    # PDF 导出
    "pdf_username": "",
    "pdf_export_dir": "",
    # 界面：AI 长任务等待提示 — banner（顶部条）| overlay（全屏遮罩）
    "ai_busy_mode": "banner",
}


def get_all(db: Session) -> dict[str, str]:
    stored = {s.key: s.value for s in db.query(Setting).all()}
    return {**DEFAULTS, **stored}


def get(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key, "")


def set_many(db: Session, values: dict[str, str]) -> None:
    for key, value in values.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=str(value)))
        else:
            row.value = str(value)
    db.commit()


def get_float(db: Session, key: str) -> float:
    try:
        return float(get(db, key))
    except ValueError:
        return float(DEFAULTS.get(key, "0"))
