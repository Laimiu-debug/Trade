"""AI 能力：OpenAI 兼容接口。未配置时优雅降级（抛 AIUnavailable，由路由转为 400）。"""

import base64
import json
import re
from datetime import date

import httpx
from sqlalchemy.orm import Session

from ..models import Snapshot, Trade
from . import market as market_svc
from . import netvalue
from . import rounds as rounds_svc
from . import settings as settings_svc

SCORE_DIMENSIONS = {
    "position": "仓位控制",
    "drawdown": "回撤控制",
    "discipline": "计划执行力",
    "entry": "买点质量",
    "exit": "卖点质量",
    "emotion": "情绪管理",
}


class AIUnavailable(Exception):
    pass


def _ai_config(db: Session, vision: bool = False) -> tuple[str, str, str]:
    """返回 (base_url, api_key, model)。vision=True 取截图识别三件套，否则取打分三件套。

    新 key 优先；为空则回退到旧 key（兼容存量配置）。
    """
    if vision:
        base = settings_svc.get(db, "ai_ocr_base_url") or settings_svc.get(db, "ai_base_url")
        key = settings_svc.get(db, "ai_ocr_api_key") or settings_svc.get(db, "ai_api_key")
        model = settings_svc.get(db, "ai_ocr_vision_model") or settings_svc.get(db, "ai_vision_model")
    else:
        base = settings_svc.get(db, "ai_score_base_url") or settings_svc.get(db, "ai_base_url")
        key = settings_svc.get(db, "ai_score_api_key") or settings_svc.get(db, "ai_api_key")
        model = settings_svc.get(db, "ai_score_text_model") or settings_svc.get(db, "ai_text_model")
    return base, key, model


def is_configured(db: Session, vision: bool = False) -> bool:
    base, key, model = _ai_config(db, vision=vision)
    return bool(base and key and model)


def test_connection(base_url: str, api_key: str, model: str) -> dict:
    """用给定配置发一条最小测试请求，返回 {ok, message}。不依赖已保存设置。

    兼容两种用途：score（文本）/ ocr（视觉）。视觉模型若不支持纯文本输入，
    仍能通过本测试（多数视觉模型兼容文本输入）。
    """
    missing = [name for name, val in [("Base URL", base_url), ("API Key", api_key), ("模型名", model)] if not val]
    if missing:
        return {"ok": False, "message": "未填写：" + "、".join(missing)}
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            timeout=30,
        )
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"网络错误：{e}"}
    if resp.status_code == 200:
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            return {"ok": True, "message": f"连接成功，模型回复「{content[:20]}」"}
        except (KeyError, IndexError):
            return {"ok": True, "message": "连接成功"}
    # 非 200：尽量提取错误信息
    try:
        detail = resp.json()
        msg = detail.get("error", {}).get("message") or detail.get("message") or str(detail)[:120]
    except Exception:  # noqa: BLE001
        msg = resp.text[:120] if resp.text else resp.reason_phrase
    return {"ok": False, "message": f"HTTP {resp.status_code}：{msg}"}


def _chat(db: Session, messages: list[dict], vision: bool = False) -> str:
    base, key, model = _ai_config(db, vision=vision)
    if not (base and key and model):
        raise AIUnavailable(
            "AI 未配置：请在设置中填写" + ("截图识别" if vision else "操作打分") + "的 Base URL、API Key 和模型名"
        )
    resp = httpx.post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": messages, "temperature": 0.2},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _extract_json(text: str):
    """模型偶尔会包 markdown 代码块，做一层剥离。"""
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(f"AI 返回内容中未找到 JSON: {text[:200]}")
    return json.loads(text[start:])


SCORE_DIMENSIONS = {
    "position": "仓位控制",
    "drawdown": "回撤控制",
    "discipline": "计划执行力",
    "entry": "买点质量",
    "exit": "卖点质量",
    "emotion": "情绪管理",
}

TRADE_SCORE_DIMENSIONS = {
    "timing": "时机质量",
    "discipline": "计划执行力",
    "emotion": "情绪管理",
}


def _trade_lines_for(trades: list) -> list[dict]:
    return [
        {
            "id": t.id,
            "side": t.side,
            "line": (
                f"#{t.id} {t.trade_date} {('买入' if t.side == 'buy' else '卖出')} {t.name or t.code}({t.code}) "
                f"{t.qty}股 @ {t.price}元，费用 "
                f"{t.fee_commission + t.fee_stamp + t.fee_transfer:.2f}元"
            ),
        }
        for t in trades
    ]


def _is_t_trading(trades: list) -> bool:
    """同一标的当日既有买入又有卖出，视为做 T。"""
    if len(trades) < 2:
        return False
    codes = {t.code for t in trades}
    if len(codes) != 1:
        return False
    sides = {t.side for t in trades}
    return "buy" in sides and "sell" in sides


def score_trades(
    db: Session,
    review_date: date,
    review_texts: dict[str, str],
    trade_ids: list[int] | None = None,
) -> dict:
    """对指定交易（或当日全部）逐条 6 维度打分。"""
    ctx = _review_context(db, review_date)
    trades = ctx["trades"]
    if trade_ids is not None:
        wanted = set(trade_ids)
        trades = [t for t in trades if t.id in wanted]
    if not trades:
        return {"trades": [], "daily_summary": ""}

    trade_lines = _trade_lines_for(trades)
    other_lines = [
        t["line"] for t in ctx["trade_lines"] if t["id"] not in {x.id for x in trades}
    ]
    dims_desc = "、".join(f"{k}({v})" for k, v in SCORE_DIMENSIONS.items())
    trade_block = "\n".join(t["line"] for t in trade_lines)
    other_block = "\n".join(other_lines) if other_lines else "（无）"
    scope = "以下指定交易" if trade_ids else "以下每一笔交易"
    id_hint = ""
    if len(trades) == 1:
        id_hint = f"\n**重要**：本次仅评价 1 笔交易，JSON 中 trades[0].id 必须为整数 {trades[0].id}，scores 各维度必须含 score 与 comment。\n"
    t_hint = ""
    if _is_t_trading(trades):
        code = trades[0].code
        name = trades[0].name or code
        t_hint = f"""
## 做T提示
以上交易为 **{name}({code}) 当日做T**（同日买卖同一标的）。请：
- 在每笔交易的 summary 和点评中体现做T节奏（低吸高抛/追涨杀跌等）
- daily_summary 中单独点评做T整体得失与节奏，而非割裂评价各笔
"""

    prompt = f"""你是一位严格的 A 股交易教练。请对{scope}**分别**按 6 个维度打分（0-10 整数），每条维度附 25-40 字点评；并给每笔交易一句整体总评。
{id_hint}{t_hint}
## 日期
{review_date.isoformat()}

## 当日行情
{ctx["market_text"]}

## 账户
{ctx["asset_line"]}

## 本次需评价的交易（id 必须原样返回）
{trade_block}

## 当日其他交易（仅供仓位/节奏参考，勿重复评价）
{other_block}

## 交易者自述（若有）
盘面观察：{review_texts.get('market_observation') or '（未填写）'}
决策复盘：{review_texts.get('decision_review') or '（未填写）'}
错误教训：{review_texts.get('mistakes') or '（未填写）'}
前一日计划对照：{review_texts.get('plan') or '（无）'}

## 评分维度
{dims_desc}
- 买入侧重 entry/position；卖出侧重 exit/position
- 不适用的维度可给 5 分并注明「不适用」

## 输出要求
严格输出 JSON：
{{"trades": [{{"id": 交易ID, "summary": "此笔整体40字内", "scores": {{{", ".join(f'"{k}": {{"score": 0, "comment": ""}}' for k in SCORE_DIMENSIONS)}}}}}, ...], "daily_scores": {{{", ".join(f'"{k}": {{"score": 0, "comment": "整日该维度30-50字总评"}}' for k in SCORE_DIMENSIONS)}}}, "daily_summary": "当日整体80字内（仅评价本次涉及的交易）"}}

daily_scores 是对当日操作在 6 维度上的**整体**评价（不是某一只股票的点评）；必须为 6 个维度都输出 score 与 comment。
必须为「本次需评价的交易」中每笔都输出一项。"""

    content = _chat(db, [{"role": "user", "content": prompt}])
    return _extract_json(content)


def score_t_group(
    db: Session,
    review_date: date,
    review_texts: dict[str, str],
    trade_ids: list[int],
) -> dict:
    """对同一标的当日做T（多笔买卖）做整体评价，并逐笔拆分。"""
    ctx = _review_context(db, review_date)
    wanted = set(trade_ids)
    trades = [t for t in ctx["trades"] if t.id in wanted]
    if len(trades) < 2 or not _is_t_trading(trades):
        raise ValueError("做T分析需要同一标的至少一笔买入和一笔卖出")
    code = trades[0].code
    name = trades[0].name or code
    trade_lines = _trade_lines_for(trades)
    trade_block = "\n".join(t["line"] for t in trade_lines)
    other_lines = [
        t["line"] for t in ctx["trade_lines"] if t["id"] not in wanted
    ]
    other_block = "\n".join(other_lines) if other_lines else "（无）"
    dims_desc = "、".join(f"{k}({v})" for k, v in SCORE_DIMENSIONS.items())

    prompt = f"""你是一位严格的 A 股交易教练。以下交易为 **{name}({code}) 当日做T**（同日对同一标的既有买入又有卖出）。

请完成两部分评价：
1. **做T整体**：从节奏、价差捕捉、仓位控制、情绪纪律等角度，对整次做T操作打 6 维度分并给 80 字内总评
2. **逐笔拆分**：对每一笔仍分别输出 6 维度分与 40 字内总评（id 必须原样返回）

## 日期
{review_date.isoformat()}

## 当日行情
{ctx["market_text"]}

## 账户
{ctx["asset_line"]}

## 做T涉及交易
{trade_block}

## 当日其他交易（参考，勿重复评价）
{other_block}

## 交易者自述（若有）
盘面观察：{review_texts.get('market_observation') or '（未填写）'}
决策复盘：{review_texts.get('decision_review') or '（未填写）'}
错误教训：{review_texts.get('mistakes') or '（未填写）'}

## 评分维度
{dims_desc}

## 输出要求
严格输出 JSON：
{{
  "group_summary": "做T整体80字内总评",
  "group_scores": {{{", ".join(f'"{k}": {{"score": 0, "comment": ""}}' for k in SCORE_DIMENSIONS)}}},
  "trades": [{{"id": 交易ID, "summary": "此笔40字内", "scores": {{{", ".join(f'"{k}": {{"score": 0, "comment": ""}}' for k in SCORE_DIMENSIONS)}}}}}, ...],
  "daily_summary": "做T整体80字内（可与 group_summary 相同）"
}}
必须为做T涉及交易中每笔都输出 trades 一项。"""

    content = _chat(db, [{"role": "user", "content": prompt}])
    return _extract_json(content)


def score_review(
    db: Session,
    review_date: date,
    review_texts: dict[str, str],
    trade_ids: list[int] | None = None,
) -> dict:
    """兼容旧调用：等同 score_trades。"""
    return score_trades(db, review_date, review_texts, trade_ids)


def _review_context(db: Session, review_date: date) -> dict:
    trades = db.query(Trade).filter(Trade.trade_date == review_date).order_by(Trade.id).all()
    trade_lines = _trade_lines_for(trades)
    snap = db.query(Snapshot).filter(Snapshot.snap_date == review_date).first()
    prev_snap = (
        db.query(Snapshot)
        .filter(Snapshot.snap_date < review_date)
        .order_by(Snapshot.snap_date.desc())
        .first()
    )
    asset_line = "（无资产快照）"
    if snap:
        asset_line = f"当日收盘总资产 {snap.total_assets:.2f} 元"
        if prev_snap and prev_snap.total_assets:
            change = (snap.total_assets / prev_snap.total_assets - 1) * 100
            asset_line += f"，较前一交易日 {change:+.2f}%"
    codes = sorted({t.code for t in trades})
    market_text = market_svc.market_context_text(db, codes, review_date)
    all_rounds = rounds_svc.build_rounds(db)
    day_rounds = rounds_svc.rounds_touching_day(all_rounds, review_date)
    round_lines = [
        rounds_svc.round_line_summary(r, rounds_svc.round_review_meta(db, r)[0])
        for r in day_rounds
    ]
    return {
        "trades": trades,
        "trade_lines": trade_lines,
        "asset_line": asset_line,
        "market_text": market_text,
        "round_lines": round_lines,
    }


def generate_daily_review(db: Session, review_date: date, review_texts: dict[str, str] | None = None) -> dict:
    """根据交易与行情自动生成复盘正文。"""
    review_texts = review_texts or {}
    ctx = _review_context(db, review_date)
    trade_block = "\n".join(t["line"] for t in ctx["trade_lines"]) or "（当日无交易）"
    round_block = "\n".join(f"- {line}" for line in ctx.get("round_lines") or []) or "（当日无相关回合）"
    prompt = f"""你是一位 A 股波段交易教练。请根据以下客观数据，帮交易者撰写**每日复盘**草稿（中文，具体、可执行，避免空话）。

## 日期
{review_date.isoformat()}

## 当日行情
{ctx["market_text"]}

## 账户
{ctx["asset_line"]}

## 当日交易
{trade_block}

## 相关回合（含本日清仓或本日有成交的标的）
{round_block}

## 交易者已写内容（可吸收、补充，勿重复啰嗦）
盘面观察：{review_texts.get('market_observation') or '（未填写）'}
决策复盘：{review_texts.get('decision_review') or '（未填写）'}
错误教训：{review_texts.get('mistakes') or '（未填写）'}

## 输出要求
严格输出 JSON：
{{
  "market_observation": "大盘/板块/情绪，3-5句",
  "decision_review": "逐笔或按标的梳理买卖理由与对错，每笔须标注操作日期，4-8句",
  "mistakes": "具体错误与改进，2-4句",
  "next_market_forecast": "次日大盘预判，2-3句",
  "next_position_plan": "次日仓位计划，1-2句",
  "next_risk_plan": "风险预案，1-2句"
}}"""

    content = _chat(db, [{"role": "user", "content": prompt}])
    return _extract_json(content)


def generate_rehearsal_analysis(
    db: Session,
    review_date: date,
    rehearsal: list,
    watchlist: list,
    today_positions: list,
    baseline: dict,
    market_text: str = "",
    review_texts: dict[str, str] | None = None,
    recent_reviews_text: str = "",
) -> str:
    """分析交易者填写的次日操作预演是否合理。"""
    review_texts = review_texts or {}
    def fmt_positions(items: list) -> str:
        if not items:
            return "（无）"
        lines = []
        for p in items:
            code = p.get("code") or "?"
            name = p.get("name") or code
            qty = p.get("qty", 0)
            note = p.get("note") or ""
            mv = p.get("market_value")
            close = p.get("close") or p.get("price")
            if mv and qty:
                try:
                    close = round(float(mv) / int(qty), 4)
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            extra = f" @{close}" if close else ""
            mv_hint = f" 市值{mv}" if mv else ""
            lines.append(
                f"- {name}({code}) {qty}股{extra}{mv_hint}{(' · ' + note) if note else ''}"
            )
        return "\n".join(lines)

    watch_block = "\n".join(
        f"- {w.get('name') or w.get('code') or '?'}({w.get('code') or ''}) "
        f"条件:{w.get('condition') or '—'} 动作:{w.get('action') or '—'}"
        for w in (watchlist or [])
    ) or "（无观察标的）"

    today_block = fmt_positions(today_positions)
    rehearsal_block = fmt_positions(rehearsal)
    cash = baseline.get("cash", 0)
    total = baseline.get("total_assets", 0)

    prompt = f"""你是一位 A 股波段交易教练。交易者已完成 {review_date.isoformat()} 复盘，并填写了**明日操作预演**（计划收盘持仓）。
你的唯一任务：在了解市场环境的前提下，评判这份预演**是否合理、有哪些遗漏、如何改进**。

## 分析原则（必须遵守）
- 交易者提交的所有持仓、价格、资金数据**均视为准确**，不要质疑数据、OCR、录入或小数点。
- **不要**将预演与「次日预研」文案（大盘预判/仓位计划/风险预案等）做一致性对照；那些多为 AI 复盘草稿，不代表交易者主观定论。
- **禁止**使用「左右脑互博」「自相矛盾」「预研与预演不符」等措辞；用户要的是对**预演本身**的点评，不是文字对照考试。
- 资金紧张时，从**交易安排**角度给建议（先卖后买、减仓、留现金等），不要归因于数据错误。

## 近期大盘与板块行情（评判预演的背景）
{market_text or '（行情数据不可用）'}

## 交易者手写复盘（优先参考其主观判断）
- 盘面观察：{review_texts.get('market_observation') or '（未填写）'}
- 决策复盘：{review_texts.get('decision_review') or '（未填写）'}
- 错误教训：{review_texts.get('mistakes') or '（未填写）'}

## 近几日盘面记录（补充背景）
{recent_reviews_text or '（无）'}

## 今日收盘基准
- 可用现金约 {cash} 元，总资产约 {total} 元
- 今日实际持仓：
{today_block}

## 明日操作预演（分析核心）
{rehearsal_block}

## 明日观察标的（执行参考，可与预演仓位交叉审视）
{watch_block}

## 输出要求
直接输出中文正文（非 JSON），4-6 段，聚焦预演本身：
1. **市场环境**：简要说明近几日大盘/相关板块所处阶段，作为评判背景（2-3 句即可）
2. **预演仓位变化**：逐类解读增仓、减仓、新开、清仓的逻辑是否说得通，与当前行情是否匹配
3. **资金与仓位安排**：现金是否够用、集中度是否过高、买卖顺序是否合理；若紧张，给出可执行的调仓顺序建议
4. **遗漏与风险**：预演未覆盖的情景（跳空、板块退潮、止损/止盈、单票过重等）
5. **改进建议**：2-4 条具体、可执行的建议
语气直接、务实，禁止空话，禁止质疑数据准确性，禁止做预研文案对照。"""

    return _chat(db, [{"role": "user", "content": prompt}]).strip()


def generate_round_review(db: Session, code: str, start_date: str) -> dict:
    """针对单个回合生成复盘摘要。"""
    r = rounds_svc.find_round(db, code, start_date)
    if r is None:
        raise ValueError("回合不存在")

    trades = r.get("trades") or []
    trade_lines = [
        f"{t['date']} {'买' if t['side'] == 'buy' else '卖'} {t['name'] or code} "
        f"{t['price']}×{t['qty']} 费用{t.get('fees', 0)}"
        for t in trades
    ]
    excerpt, review_dates = rounds_svc.round_review_meta(db, r)
    daily_parts: list[str] = []
    if excerpt:
        daily_parts.append(excerpt)
    if review_dates:
        daily_parts.append(f"有复盘日期: {', '.join(review_dates)}")

    end_day = date.fromisoformat(r["end_date"]) if r.get("end_date") else date.fromisoformat(r["start_date"])
    market_text = market_svc.market_context_text(db, [code], end_day)

    span = f"{r['start_date']} → {r['end_date'] or '持仓中'}"
    if r["status"] == "closed" and r.get("pnl") is not None:
        pnl_line = f"盈亏 {r['pnl']} 元 ({r.get('pnl_pct')}%)"
    elif r["status"] == "anomaly":
        pnl_line = "数据异常回合"
    else:
        pnl_line = "持仓中"

    existing = rounds_svc.get_round_summary(db, code, start_date)

    prompt = f"""你是一位 A 股波段交易教练。请为下面这一完整交易回合撰写**回合复盘摘要**（中文，3-6句，具体、可执行，避免空话）。

## 回合
标的：{r['name']} ({code})
周期：{span}
状态：{r['status']}
结果：{pnl_line}
成交笔数：{len(trades)}

## 回合内交易流水
{chr(10).join(trade_lines) or '（无明细）'}

## 行情背景（清仓日/最近交易日）
{market_text}

## 回合跨度内的每日复盘摘录
{chr(10).join(daily_parts) or '（暂无日复盘摘录）'}

## 已有回合摘要（可吸收改进，勿重复啰嗦）
{existing or '（未填写）'}

## 输出要求
严格输出 JSON：
{{
  "review_summary": "3-6句：买卖时机评价、纪律与情绪、本回合核心教训或可复制优点；已清仓须点评盈亏原因，持仓中须点评当前应对"
}}"""

    content = _chat(db, [{"role": "user", "content": prompt}])
    return _extract_json(content)


def generate_weekly_review(
    db: Session,
    year: int,
    week: int,
    start: date,
    end: date,
    auto: dict,
    context: dict,
    existing: dict[str, str] | None = None,
) -> dict:
    """生成本周复盘草稿。"""
    existing = existing or {}
    trade_block = "\n".join(f"- {line}" for line in context.get("trade_lines") or []) or "（本周无交易）"
    daily_block = "\n".join(f"- {line}" for line in context.get("daily_excerpts") or []) or "（无每日复盘记录）"
    round_block = "\n".join(f"- {line}" for line in context.get("round_lines") or []) or "（本周无清仓回合）"
    win_rate = (
        round(auto["win_rounds"] / auto["closed_rounds"] * 100, 1)
        if auto.get("closed_rounds", 0) > 0 else None
    )

    prompt = f"""你是一位 A 股波段交易教练，擅长从「行为与体系」角度做周度复盘。请根据以下数据，撰写**本周复盘**草稿。

## 周期
{year} 年第 {week} 周（{start.isoformat()} ~ {end.isoformat()}）

## 账户与交易绩效
- 区间收益率：{auto.get('return_pct')}%
- 区间最大回撤：{auto.get('max_drawdown_pct')}%
- 期末净值：{auto.get('end_nav')}
- 交易笔数：{auto.get('trade_count')}
- 清仓回合：{auto.get('closed_rounds')}（盈利 {auto.get('win_rounds')} 笔{ f"，胜率 {win_rate}%" if win_rate is not None else ""}）
- 回合合计盈亏：{auto.get('round_pnl')} 元

## 本周行情背景
{context.get('market_text') or '（行情数据不可用）'}

## 本周交易流水（含操作日期）
{trade_block}

## 本周已清仓回合
{round_block}

## 本周每日复盘摘要
{daily_block}

## 交易者已写内容（可吸收、补充，避免重复）
本周盘面回顾：{existing.get('market_review') or '（未填写）'}
本周做对：{existing.get('right_things') or '（未填写）'}
本周做错：{existing.get('wrong_things') or '（未填写）'}
下周策略：{existing.get('next_strategy') or '（未填写）'}

## 写作要求
1. **本周盘面回顾**：资金去了哪些板块/风格？市场整体热度如何？大盘是向上、震荡还是向下？结合本周交易标的与指数表现写 4-6 句
2. **做对的事**：聚焦可复制的正确行为，不要流水账
3. **做错的事**：指出具体行为问题；点评每笔操作时务必带上**操作日期**（如「3月5日买入XX…」）
4. **下周策略**：仓位基调（几成仓）、主攻方向、一条纪律红线
5. 结合胜率、回撤、交易频率，判断本周节奏（乱动/踏空/良好）
6. 语气直接、具体，禁止空话套话

## 输出要求
严格输出 JSON：
{{
  "market_review": "4-6句，板块/热度/大盘方向/资金去向",
  "right_things": "4-6句，分点叙述也可",
  "wrong_things": "4-6句，每笔操作点评须含日期",
  "next_strategy": "3-5句，含仓位+方向+纪律"
}}"""

    content = _chat(db, [{"role": "user", "content": prompt}])
    return _extract_json(content)


def generate_monthly_review(
    db: Session,
    year: int,
    month: int,
    start: date,
    end: date,
    auto: dict,
    node_state: dict,
    context: dict,
    existing: dict[str, str] | None = None,
) -> dict:
    """生成本月复盘草稿。"""
    existing = existing or {}
    trade_block = "\n".join(f"- {line}" for line in context.get("trade_lines") or []) or "（本月无交易）"
    daily_block = "\n".join(f"- {line}" for line in context.get("daily_excerpts") or []) or "（无每日复盘记录）"
    weekly_block = "\n".join(f"- {line}" for line in context.get("weekly_excerpts") or []) or "（无周复盘记录）"
    round_block = "\n".join(f"- {line}" for line in context.get("round_lines") or []) or "（本月无清仓回合）"
    rate, node_count = netvalue.node_config(db)
    wave_note = f"节点倍率 {round((rate - 1) * 100, 1)}%，共 {node_count} 个节点"

    prompt = f"""你是一位 A 股波段交易教练，擅长交易系统迭代与长期复利思维。请根据以下数据，撰写**本月复盘**草稿。

## 周期
{year} 年 {month} 月（{start.isoformat()} ~ {end.isoformat()}）

## 账户与交易绩效
- 区间收益率：{auto.get('return_pct')}%
- 区间最大回撤：{auto.get('max_drawdown_pct')}%
- 期末净值：{auto.get('end_nav')}
- 交易笔数：{auto.get('trade_count')}
- 清仓回合：{auto.get('closed_rounds')}（盈利 {auto.get('win_rounds')} 笔）
- 回合合计盈亏：{auto.get('round_pnl')} 元

## 节点征途
- 已点亮节点：{node_state.get('lit_count')} / {node_state.get('node_count')}
- 当前净值：{node_state.get('nav')}
{('- ' + wave_note) if wave_note else ''}

## 本月行情背景
{context.get('market_text') or '（行情数据不可用）'}

## 本月交易流水（摘要）
{trade_block}

## 本月清仓回合
{round_block}

## 本月每日复盘摘要
{daily_block}

## 本月周复盘摘要
{weekly_block}

## 交易者已写内容（可吸收、补充，避免重复）
体系迭代：{existing.get('system_iteration') or '（未填写）'}
下月目标：{existing.get('next_goal') or '（未填写）'}

## 写作要求
1. **体系迭代**：哪些规则有效、哪些需修改/删除/新增；用本月数据举证，不要泛泛而谈
2. **下月目标**：至少包含 1 个行为目标（如「减少无效交易至 X 笔以内」）+ 1 个结果目标（如「净值目标/节点进度」）
3. 若本月回撤大或胜率低，优先反思 process 而非归因运气
4. 联系「50 节点复利」长期视角，避免短线赌徒心态

## 输出要求
严格输出 JSON：
{{
  "system_iteration": "5-8句，含具体规则层面的修正建议",
  "next_goal": "3-5句，行为目标+结果目标"
}}"""

    content = _chat(db, [{"role": "user", "content": prompt}])
    return _extract_json(content)


SCREENSHOT_PROMPT = """这是一张同花顺（或类似券商软件）的成交记录/持仓截图。请提取其中的交易记录，严格输出 JSON 数组，不要多余文字：
[{"date": "YYYY-MM-DD", "code": "6位股票代码", "name": "股票名称", "side": "buy或sell", "price": 成交价, "qty": 成交数量}]
注意：
- side 依据"买入/卖出/证券买入/证券卖出"等字样判断
- 如果截图是持仓页（没有买卖方向），输出每行持仓为 {"date": null, "code": ..., "name": ..., "side": "hold", "price": 成本价, "qty": 持仓数量}
- 识别不到任何记录则输出 []"""


def parse_screenshot(db: Session, image_bytes: bytes, mime: str) -> list[dict]:
    b64 = base64.b64encode(image_bytes).decode()
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": SCREENSHOT_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }]
    content = _chat(db, messages, vision=True)
    data = _extract_json(content)
    return data if isinstance(data, list) else []


ACCOUNT_SNAPSHOT_PROMPT = """这是券商账户的持仓/资产总览截图（如同花顺「资产」或「持仓」页）。
请提取账户信息，严格输出 JSON 对象，不要多余文字：
{
  "snap_date": "YYYY-MM-DD 或 null",
  "total_assets": 总资产数字或 null,
  "available_cash": 可用资金或 null,
  "positions": [
    {"code": "6位股票代码", "name": "名称", "qty": 持仓数量, "price": 成本价或现价, "market_value": 市值或 null}
  ]
}
注意：
- total_assets 优先取「总资产」「账户资产」「资产总值」等字段
- 若截图只有持仓列表没有总资产，positions 仍要完整输出，total_assets 可填 null
- 每条持仓：name 保留截图简称；code 无则填 null
- **必须**读取每行「市值」填入 market_value；读取「现价」填入 price；若 price 不确定，用 market_value÷qty
- ETF 现价通常 0.5～15 元（如 3.984、1.038），不要把小数点弄错
- 识别不到任何有效信息则 {"snap_date": null, "total_assets": null, "available_cash": null, "positions": []}"""


def parse_account_screenshot(db: Session, image_bytes: bytes, mime: str) -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": ACCOUNT_SNAPSHOT_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }]
    content = _chat(db, messages, vision=True)
    data = _extract_json(content)
    if not isinstance(data, dict):
        return {"snap_date": None, "total_assets": None, "available_cash": None, "positions": []}
    positions = data.get("positions")
    if not isinstance(positions, list):
        positions = []
    return {
        "snap_date": data.get("snap_date"),
        "total_assets": data.get("total_assets"),
        "available_cash": data.get("available_cash"),
        "positions": positions,
    }
