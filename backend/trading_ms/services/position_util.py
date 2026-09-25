"""持仓定价工具：截图场景下市值÷数量比 OCR 现价更可靠。"""


def position_close(p: dict) -> float | None:
    """推断持仓收盘价/现价。有市值时优先用 市值÷数量，避免 ETF 小数点错位。"""
    if not isinstance(p, dict):
        return None
    qty_raw = p.get("qty")
    mv_raw = p.get("market_value")
    price_raw = p.get("price")
    close_raw = p.get("close")

    qty: int | None = None
    mv: float | None = None
    price: float | None = None

    try:
        if qty_raw is not None:
            qty = int(float(qty_raw))
    except (TypeError, ValueError):
        qty = None
    try:
        if mv_raw is not None:
            mv = float(mv_raw)
    except (TypeError, ValueError):
        mv = None
    try:
        if price_raw is not None:
            price = float(price_raw)
    except (TypeError, ValueError):
        price = None

    if qty is not None and qty > 0 and mv is not None and mv > 0:
        implied = mv / qty
        if price is not None and price > 0:
            ratio = price / implied
            if ratio >= 5 or ratio <= 0.2:
                return round(implied, 4)
        return round(implied, 4)

    for raw in (close_raw, price_raw):
        if raw is None:
            continue
        try:
            val = float(raw)
            if val > 0:
                return round(val, 4)
        except (TypeError, ValueError):
            continue
    return None


def sanitize_position(p: dict) -> dict:
    """规范化单条持仓：修正 price/close，保留原始 market_value。"""
    if not isinstance(p, dict):
        return {}
    row = dict(p)
    close = position_close(row)
    if close is not None:
        row["price"] = close
        row["close"] = close
    return row


def is_etf_code(code: str | None) -> bool:
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) != 6:
        return False
    return digits.startswith(("51", "56", "58", "15", "16"))


def price_looks_suspicious(price: float | None, code: str | None) -> bool:
    """ETF 现价异常偏高（常见 OCR 小数点错位）。"""
    if price is None or price <= 0:
        return True
    if is_etf_code(code) and price > 15:
        return True
    return False
