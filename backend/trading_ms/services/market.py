"""行情数据三源：本地通达信 vipdoc 直读 / akshare / 东方财富公开接口。

统一输出日K: [{date, open, high, low, close, volume, amount}]
优先级链路由设置 market_priority 控制，逐源尝试直至成功。
"""

import struct
import time
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from . import settings as settings_svc

INDEX_CODES = {"000001.SH", "399001.SZ", "399006.SZ", "000300.SH", "000905.SH"}


def _market_prefix(code: str) -> tuple[str, str]:
    """返回 (市场目录, 纯代码)。支持 '600000'、'000001.SH' 两种写法。"""
    if "." in code:
        pure, suffix = code.split(".")
        return suffix.lower(), pure
    if code.startswith(("6", "5", "9")):
        return "sh", code
    if code.startswith(("4", "8")):
        return "bj", code
    return "sz", code


def _tdx_price_scale(code: str) -> float:
    """通达信 .day 价格缩放：A 股/指数 ÷100，基金/债券 ÷1000（官方 vipdoc 规则）。"""
    market, pure = _market_prefix(code)
    if market == "sh" and (pure.startswith("5") or pure.startswith("204")):
        return 1000.0
    if market == "sz" and pure.startswith("1"):
        return 1000.0
    return 100.0


# ---------- 通达信 ----------

def read_tdx_day(vipdoc: str, code: str, limit: int = 120) -> list[dict]:
    market, pure = _market_prefix(code)
    path = Path(vipdoc) / market / "lday" / f"{market}{pure}.day"
    if not path.exists():
        raise FileNotFoundError(f"通达信数据文件不存在: {path}")
    scale = _tdx_price_scale(code)
    records: list[dict] = []
    raw = path.read_bytes()
    count = len(raw) // 32
    start = max(0, count - limit)
    for i in range(start, count):
        chunk = raw[i * 32:(i + 1) * 32]
        d, o, h, low, c, amount, vol, _ = struct.unpack("<IIIIIfII", chunk)
        records.append({
            "date": f"{d // 10000:04d}-{d % 10000 // 100:02d}-{d % 100:02d}",
            "open": o / scale, "high": h / scale, "low": low / scale, "close": c / scale,
            "volume": vol, "amount": amount,
        })
    return records


# ---------- akshare ----------

def read_akshare(code: str, limit: int = 120) -> list[dict]:
    import akshare as ak  # noqa: PLC0415 - akshare 导入极慢，仅在启用该源时加载

    market, pure = _market_prefix(code)
    if f"{pure}.{market.upper()}" in INDEX_CODES or (market == "sh" and pure.startswith("000")):
        df = ak.stock_zh_index_daily(symbol=f"{market}{pure}")
        df = df.tail(limit)
        return [
            {
                "date": str(r["date"])[:10], "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": float(r["volume"]), "amount": 0.0,
            }
            for _, r in df.iterrows()
        ]
    df = ak.stock_zh_a_hist(symbol=pure, period="daily", adjust="")
    df = df.tail(limit)
    return [
        {
            "date": str(r["日期"])[:10], "open": float(r["开盘"]), "high": float(r["最高"]),
            "low": float(r["最低"]), "close": float(r["收盘"]),
            "volume": float(r["成交量"]), "amount": float(r["成交额"]),
        }
        for _, r in df.iterrows()
    ]


# ---------- 东方财富 ----------

def read_eastmoney(code: str, limit: int = 120) -> list[dict]:
    market, pure = _market_prefix(code)
    secid = f"{'1' if market == 'sh' else '0'}.{pure}"
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid, "klt": "101", "fqt": "0",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "beg": "0", "end": "20500101", "lmt": str(limit),
    }
    resp = httpx.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    klines = (data.get("data") or {}).get("klines") or []
    records = []
    for line in klines:
        parts = line.split(",")
        records.append({
            "date": parts[0], "open": float(parts[1]), "close": float(parts[2]),
            "high": float(parts[3]), "low": float(parts[4]),
            "volume": float(parts[5]), "amount": float(parts[6]),
        })
    return records


# ---------- 链路 ----------

_daily_cache: dict[tuple[str, int], tuple[float, dict]] = {}
_DAILY_CACHE_TTL = 300.0


def get_daily(db: Session, code: str, limit: int = 120) -> dict:
    key = (code, limit)
    now = time.monotonic()
    cached = _daily_cache.get(key)
    if cached is not None:
        ts, val = cached
        if now - ts < _DAILY_CACHE_TTL:
            return val
    priority = [s.strip() for s in settings_svc.get(db, "market_priority").split(",") if s.strip()]
    errors: list[str] = []
    for source in priority:
        try:
            if source == "tdx":
                data = read_tdx_day(settings_svc.get(db, "tdx_path"), code, limit)
            elif source == "akshare":
                data = read_akshare(code, limit)
            elif source == "web":
                data = read_eastmoney(code, limit)
            else:
                continue
            if data:
                result = {"source": source, "code": code, "klines": data}
                _daily_cache[key] = (now, result)
                return result
        except Exception as exc:  # noqa: BLE001 - 逐源降级，任何异常都尝试下一源
            errors.append(f"{source}: {exc}")
    result = {"source": None, "code": code, "klines": [], "errors": errors}
    _daily_cache[key] = (now, result)
    return result


def close_on_day(db: Session, code: str, day_str: str, limit: int = 40) -> float | None:
    """指定交易日（或之前最近交易日）的收盘价。"""
    klines = get_daily(db, code, limit).get("klines") or []
    return _close_on_or_before(klines, day_str)


# ---------- 股票 / ETF 搜索 ----------

_stock_cache: list[dict[str, str]] | None = None
_etf_cache: list[dict[str, str]] | None = None


def _load_stock_list() -> list[dict[str, str]]:
    global _stock_cache
    if _stock_cache is not None:
        return _stock_cache
    try:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_info_a_code_name()
        _stock_cache = [
            {"code": str(row["code"]).zfill(6), "name": str(row["name"])}
            for _, row in df.iterrows()
        ]
    except Exception:  # noqa: BLE001
        _stock_cache = []
    return _stock_cache


def _load_etf_list() -> list[dict[str, str]]:
    global _etf_cache
    if _etf_cache is not None:
        return _etf_cache
    try:
        import akshare as ak  # noqa: PLC0415

        df = ak.fund_etf_spot_em()
        code_col = "代码" if "代码" in df.columns else "fund_code"
        name_col = "名称" if "名称" in df.columns else "name"
        _etf_cache = [
            {"code": str(row[code_col]).zfill(6), "name": str(row[name_col])}
            for _, row in df.iterrows()
        ]
    except Exception:  # noqa: BLE001
        _etf_cache = []
    return _etf_cache


def _all_securities() -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for item in _load_stock_list() + _load_etf_list():
        merged[item["code"]] = item
    return list(merged.values())


def lookup_by_code(code: str) -> dict[str, str] | None:
    """按 6 位 A 股 / ETF 代码精确查找。"""
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) != 6:
        return None
    target = digits.zfill(6)
    for item in _all_securities():
        if item["code"] == target:
            return item
    return None


# 券商 App 简称 → 标准代码（截图无 6 位代码时的兜底）
_OCR_NAME_TO_CODE: dict[str, str] = {
    "科半导体": "588710",
    "科创半导体": "588710",
    "科创半导体设备": "588710",
    "科创半导体etf": "588710",
    "科创半导体ETF": "588710",
    "科创板半导体": "588710",
    "科创板半导体etf": "588710",
    "科创板半导体ETF": "588710",
    "数据ETF": "515400",
    "数据 ETF": "515400",
}

# akshare 未加载时的静态名称
_STATIC_CODE_NAME: dict[str, str] = {
    "588710": "科创半导体设备ETF华泰柏瑞",
    "515400": "大数据ETF富国",
}


def _is_etf_code(code: str) -> bool:
    return code.startswith(("51", "56", "58", "15", "16"))


def _normalize_query_name(name: str) -> str:
    return name.replace(" ", "").replace("　", "").strip()


def _strip_etf_suffix(name: str) -> str:
    s = _normalize_query_name(name)
    for w in ("ETF", "etf", "基金", "LOF", "联接"):
        s = s.replace(w, "")
    return s


def _subsequence_match(query: str, target: str) -> bool:
    """简称是否为全称的有序子序列（如「科半导体」⊂「科创半导体设备ETF」）。"""
    q = _strip_etf_suffix(query)
    if len(q) < 2:
        return False
    ti = 0
    for ch in target:
        if ti < len(q) and ch == q[ti]:
            ti += 1
    return ti == len(q)


def _implied_price(
    price: float | None,
    qty: int | None,
    market_value: float | None,
) -> float | None:
    if qty is not None and qty > 0 and market_value is not None and market_value > 0:
        return market_value / qty
    if price is not None and price > 0:
        return price
    return None


def _close_on_or_before(klines: list[dict], day_str: str | None) -> float | None:
    if not klines:
        return None
    if day_str:
        valid = [k for k in klines if k["date"] <= day_str]
        if not valid:
            return None
        return float(valid[-1]["close"])
    return float(klines[-1]["close"])


def _quick_name_match(name: str, hits: list[dict[str, str]]) -> tuple[str, str] | None:
    """恢复快速路径：精确名 / 包含关系 / 唯一命中。"""
    for hit in hits:
        if hit["name"] == name:
            return hit["code"], hit["name"]
    q = _normalize_query_name(name)
    for hit in hits:
        full = _normalize_query_name(hit["name"])
        if q and len(q) >= 2 and (q in full or full in q):
            return hit["code"], hit["name"]
    for hit in hits:
        if name in hit["name"] or hit["name"] in name:
            return hit["code"], hit["name"]
    if len(hits) == 1:
        return hits[0]["code"], hits[0]["name"]
    return None


def _needs_abbrev_match(name: str, hits: list[dict[str, str]]) -> bool:
    alias_key = _normalize_query_name(name)
    if name in _OCR_NAME_TO_CODE or alias_key in _OCR_NAME_TO_CODE:
        return False
    if not _etf_like_name(name):
        return False
    if not hits:
        return True
    q = _normalize_query_name(name)
    q_core = _strip_etf_suffix(name)
    for hit in hits[:3]:
        full = _normalize_query_name(hit["name"])
        if q and q in full:
            return False
        if q_core and len(q_core) >= 2 and q_core in full:
            return False
    return True


def _abbrev_etf_candidates(name: str) -> list[dict[str, str]]:
    """仅在简称匹配失败时扫描 ETF 列表（避免每次都全量遍历）。"""
    seen: set[str] = set()
    candidates: list[dict[str, str]] = []
    for hit in search_stocks(name, limit=10):
        if hit["code"] not in seen:
            seen.add(hit["code"])
            candidates.append(hit)
    q_core = _strip_etf_suffix(name)
    if len(q_core) > 10:
        return candidates
    for item in _load_etf_list():
        if item["code"] in seen:
            continue
        full = _normalize_query_name(item["name"])
        if _subsequence_match(name, item["name"]) or (
            len(q_core) >= 2 and q_core in full
        ):
            seen.add(item["code"])
            candidates.append(item)
    return candidates


def _code_name_pair(code: str) -> tuple[str, str] | None:
    hit = lookup_by_code(code)
    if hit:
        return hit["code"], hit["name"]
    static = _STATIC_CODE_NAME.get(code)
    if static:
        return code, static
    return None


def _rank_name_candidates(name: str, pool: list[dict[str, str]]) -> list[tuple[int, dict[str, str]]]:
    q = _normalize_query_name(name)
    q_lower = q.lower()
    q_core = _strip_etf_suffix(name)
    ranked: list[tuple[int, dict[str, str]]] = []
    for item in pool:
        full = item["name"]
        full_norm = _normalize_query_name(full)
        full_lower = full_norm.lower()
        score = -1
        if full == name or full_norm == q or full_lower == q_lower:
            score = 200
        elif q_core and len(q_core) >= 2 and q_core in full_norm:
            score = 130 + min(len(q_core), 20)
        elif q and len(q) >= 2 and (q in full_norm or q_lower in full_lower):
            score = 110 + min(len(q), 15)
        elif q_core and len(q_core) >= 2 and _subsequence_match(q_core, full_norm):
            score = 85 + min(len(q_core), 15)
        elif q and len(q) >= 2 and _subsequence_match(q, full_norm):
            score = 75 + min(len(q), 12)
        if score >= 0:
            if _is_etf_code(item["code"]) and _etf_like_name(name):
                score += 8
            ranked.append((score, item))
    ranked.sort(key=lambda x: (-x[0], x[1]["code"]))
    return ranked


def _etf_like_name(name: str) -> bool:
    return any(k in name for k in ("ETF", "etf", "基金", "半导体", "科创", "指数", "LOF", "数据"))



def _disambiguate_by_price(
    candidates: list[dict[str, str]],
    implied: float,
    db: Session,
    snap_date: date | None = None,
) -> dict[str, str] | None:
    best_item: dict[str, str] | None = None
    best_err = float("inf")
    day_str = snap_date.isoformat() if snap_date else None
    for item in candidates[:12]:
        try:
            klines = get_daily(db, item["code"], limit=90)["klines"]
            close = _close_on_or_before(klines, day_str)
            if close is None or close <= 0:
                continue
            err = abs(close - implied) / implied
            if err < best_err:
                best_err = err
                best_item = item
        except Exception:  # noqa: BLE001
            continue
    if best_item is not None and best_err < 0.35:
        return best_item
    return None


def resolve_stock(
    code: str,
    name: str,
    *,
    price: float | None = None,
    market_value: float | None = None,
    qty: int | None = None,
    db: Session | None = None,
    snap_date: date | None = None,
) -> tuple[str, str]:
    """从 OCR/手输的代码或名称解析为标准 6 位代码与名称。"""
    code = (code or "").strip()
    name = (name or "").strip()
    digits = "".join(ch for ch in code if ch.isdigit())

    if len(digits) == 6:
        pair = _code_name_pair(digits.zfill(6))
        if pair:
            return pair

    if not digits and name:
        alias_key = _normalize_query_name(name)
        alias_code = _OCR_NAME_TO_CODE.get(name) or _OCR_NAME_TO_CODE.get(alias_key)
        if alias_code:
            pair = _code_name_pair(alias_code)
            if pair:
                return pair

    if name:
        hits = search_stocks(name, limit=10)
        if not _needs_abbrev_match(name, hits):
            quick = _quick_name_match(name, hits)
            if quick:
                return quick

        candidates = _abbrev_etf_candidates(name)
        ranked = _rank_name_candidates(name, candidates)
        if ranked:
            top_score = ranked[0][0]
            top_group = [item for sc, item in ranked if sc >= top_score - 5][:8]
            implied = _implied_price(price, qty, market_value)

            if db is not None and implied is not None and len(top_group) > 1:
                picked = _disambiguate_by_price(top_group, implied, db, snap_date)
                if picked:
                    return picked["code"], picked["name"]

            if len(top_group) == 1:
                return top_group[0]["code"], top_group[0]["name"]

            if len(ranked) >= 2 and ranked[0][0] - ranked[1][0] >= 12:
                return ranked[0][1]["code"], ranked[0][1]["name"]

            if top_score >= 95:
                return ranked[0][1]["code"], ranked[0][1]["name"]

        quick = _quick_name_match(name, hits)
        if quick:
            return quick

    if digits:
        hits = search_stocks(digits, limit=3)
        if hits and hits[0]["code"] == digits.zfill(6):
            return hits[0]["code"], hits[0]["name"]

    if len(digits) == 6:
        return digits.zfill(6), name
    return code, name


def search_stocks(query: str, limit: int = 12) -> list[dict[str, str]]:
    """按代码或名称模糊匹配 A 股 / ETF 列表。"""
    q = query.strip()
    if not q:
        return []
    stocks = _all_securities()
    q_lower = q.lower()
    q_digits = "".join(ch for ch in q if ch.isdigit())
    q_norm = _normalize_query_name(q)
    q_core = _strip_etf_suffix(q)

    scored: list[tuple[int, dict[str, str]]] = []
    for item in stocks:
        code, name = item["code"], item["name"]
        name_norm = _normalize_query_name(name)
        score = -1
        if q_digits and code == q_digits.zfill(6):
            score = 100
        elif q_digits and code.startswith(q_digits):
            score = 80 - len(code)
        elif q_norm and len(q_norm) >= 2 and q_norm in name_norm:
            score = 70 + min(len(q_norm), 15)
        elif q_lower in name.lower():
            score = 60
        elif q_core and len(q_core) >= 2 and _subsequence_match(q_core, name_norm):
            score = 52 + min(len(q_core), 12)
        elif q_lower in code:
            score = 50
        if score >= 0:
            scored.append((score, item))

    scored.sort(key=lambda x: (-x[0], x[1]["code"]))
    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for _, item in scored:
        if item["code"] in seen:
            continue
        seen.add(item["code"])
        results.append(item)
        if len(results) >= limit:
            break
    return results


def market_context_text(db: Session, codes: list[str], day: date) -> str:
    """给 AI 打分用的行情上下文：大盘 + 涉及个股当日与近5日表现。"""
    lines: list[str] = []
    targets = [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"), ("399006.SZ", "创业板指")]
    targets += [(c, "") for c in codes]
    day_str = day.isoformat()
    for code, label in targets:
        result = get_daily(db, code, limit=10)
        klines = result["klines"]
        if not klines:
            continue
        recent = [k for k in klines if k["date"] <= day_str][-6:]
        if not recent:
            continue
        today = recent[-1]
        change = ""
        if len(recent) >= 2:
            prev_close = recent[-2]["close"]
            if prev_close:
                change = f"，当日涨跌 {((today['close'] / prev_close) - 1) * 100:+.2f}%"
        seq = " -> ".join(f"{k['close']:.2f}" for k in recent)
        lines.append(f"{label or code}（{code}）近{len(recent)}日收盘: {seq}{change}")
    return "\n".join(lines) if lines else "（行情数据不可用）"


def rehearsal_market_context(db: Session, day: date, codes: list[str], lookback: int = 7) -> str:
    """预演分析用：近 N 日大盘走势 + 预演涉及标的涨跌。"""
    sections: list[str] = []
    day_str = day.isoformat()
    index_targets = [
        ("000001.SH", "上证指数"),
        ("399001.SZ", "深证成指"),
        ("399006.SZ", "创业板指"),
        ("000300.SH", "沪深300"),
        ("000905.SH", "中证500"),
    ]
    index_lines: list[str] = []
    for code, label in index_targets:
        klines = [k for k in get_daily(db, code, limit=lookback + 5)["klines"] if k["date"] <= day_str][-lookback:]
        if len(klines) < 2:
            continue
        first_close = klines[0]["close"]
        last_close = klines[-1]["close"]
        period_chg = ((last_close / first_close) - 1) * 100 if first_close else 0.0
        daily_parts: list[str] = []
        for i, k in enumerate(klines):
            chg = ""
            if i > 0 and klines[i - 1]["close"]:
                chg = f"({((k['close'] / klines[i - 1]['close']) - 1) * 100:+.1f}%)"
            daily_parts.append(f"{k['date'][5:]}:{k['close']:.2f}{chg}")
        index_lines.append(f"- {label}: 近{len(klines)}日 {period_chg:+.2f}% | {' '.join(daily_parts)}")
    if index_lines:
        sections.append(f"【大盘近{lookback}个交易日】\n" + "\n".join(index_lines))

    stock_lines: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        code = raw.split(".")[0] if "." in raw else raw
        if not code or code in seen:
            continue
        seen.add(code)
        klines = [k for k in get_daily(db, code, limit=lookback + 5)["klines"] if k["date"] <= day_str][-lookback:]
        if len(klines) < 2:
            continue
        first_close = klines[0]["close"]
        last_close = klines[-1]["close"]
        period_chg = ((last_close / first_close) - 1) * 100 if first_close else 0.0
        today_chg = ""
        if klines[-2]["close"]:
            today_chg = f"，当日{((last_close / klines[-2]['close']) - 1) * 100:+.2f}%"
        stock_lines.append(f"- {code}: 近{len(klines)}日 {period_chg:+.2f}%{today_chg}，收 {last_close:.3f}")
        if len(stock_lines) >= 12:
            break
    if stock_lines:
        sections.append("【预演/持仓相关标的走势】\n" + "\n".join(stock_lines))

    sector_targets = [
        ("512480", "半导体ETF"),
        ("512880", "证券ETF"),
        ("515400", "大数据ETF"),
        ("512760", "芯片ETF"),
        ("512660", "军工ETF"),
        ("512010", "医药ETF"),
        ("512170", "医疗ETF"),
        ("159915", "创业板ETF"),
        ("510300", "沪深300ETF"),
        ("512800", "银行ETF"),
        ("515790", "光伏ETF"),
    ]
    sector_lines: list[str] = []
    for code, label in sector_targets:
        klines = [k for k in get_daily(db, code, limit=lookback + 5)["klines"] if k["date"] <= day_str][-lookback:]
        if len(klines) < 2:
            continue
        first_close = klines[0]["close"]
        last_close = klines[-1]["close"]
        period_chg = ((last_close / first_close) - 1) * 100 if first_close else 0.0
        sector_lines.append(f"- {label}({code}): 近{len(klines)}日 {period_chg:+.2f}%，收 {last_close:.3f}")
    if sector_lines:
        sections.append("【主要板块 ETF 近{}日表现】\n".format(lookback) + "\n".join(sector_lines))

    return "\n\n".join(sections) if sections else "（行情数据不可用）"
