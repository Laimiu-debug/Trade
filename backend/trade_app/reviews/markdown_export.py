"""Account-scoped, read-only Markdown exports for human review records."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.analytics.service import projection_status
from trade_app.platform.types import TradeError
from trade_app.reviews.attachments import list_attachments
from trade_app.reviews.periods import bounds, get_period
from trade_app.reviews.rounds import list_round_notes
from trade_app.reviews.scores import list_scores
from trade_app.reviews.service import get_review, validate_day
from trade_app.reviews.print_settings import get_settings
from trade_app.trading.service import account_or_error
from trade_app.trading.models import Trade


DAILY_FIELDS = [('overall_summary', '总体总结'), ('market_observation', '市场观察'),
                ('decision_review', '决策复盘'), ('mistakes', '错误与教训'),
                ('reflection', '反思'), ('tomorrow_plan', '次日计划'),
                ('next_market_forecast', '大盘预判'),
                ('next_position_plan', '仓位计划'), ('next_risk_plan', '风险计划')]
PERIOD_FIELDS = {
    'weekly': [('core_goals', '核心目标'), ('achievements', '成果'),
               ('resource_analysis', '资源分析'), ('market_rhythm', '市场节奏'),
               ('right_things', '做对的事'), ('wrong_things', '做错的事'),
               ('market_review', '市场复盘'), ('next_strategy', '交易策略'),
               ('next_week_strategy', '下周策略'), ('key_insight', '关键认知'),
               ('tags', '标签')],
    'monthly': [('summary', '月度总结'), ('market_review', '市场复盘'),
                ('system_iteration', '体系迭代'), ('next_goal', '下月目标'),
                ('tags', '标签')],
}


def _section(lines: list[str], title: str, value: str | None) -> None:
    if value:
        lines.extend([f'## {title}', '', value.strip(), ''])


def _rounds(session: Session, account_id: str, start: str, end: str) -> list[dict]:
    projection = projection_status(session, account_id)
    rows = (projection['result'] or {}).get('rounds', [])
    return [row for row in rows if row.get('end_date') and start <= row['end_date'] <= end]


def export_markdown(session: Session, account_id: str, kind: str, key: str) -> str:
    account = account_or_error(session, account_id, real=True)
    if kind == 'daily':
        validate_day(key)
        start = end = key
        review = get_review(session, account_id, key)
        if review is None:
            raise TradeError('REVIEW_NOT_FOUND', '该日期没有已保存的日复盘', 404)
        lines = [f'# {review['title'].strip() or key + ' 每日复盘'}', '',
                 f'- 账户：{account.name}', f'- 日期：{key}', f'- 修订：{review['revision']}', '']
        for field, title in DAILY_FIELDS:
            _section(lines, title, review.get(field))
        if review['tags']:
            lines.extend(['## 标签', '', '、'.join(review['tags']), ''])
        target = review.get('next_target_date')
        if target:
            lines.extend(['## 计划执行日', '', target, ''])
        watchlist = review.get('next_watchlist') or []
        if watchlist:
            lines.extend(['## 关注股', ''])
            lines.extend(f"- {item['code']} {item.get('name') or ''}：{item.get('condition') or '未填触发条件'}；{item.get('action') or '未填动作'}"
                         for item in watchlist)
            lines.append('')
        rehearsal = review.get('next_position_rehearsal') or []
        if rehearsal:
            lines.extend(['## 持仓预演', ''])
            lines.extend(f"- {item['code']} {item.get('name') or ''}：{item['qty']} 股；预计价 {item.get('price') or '未填写'}；{item.get('note') or ''}"
                         for item in rehearsal)
            lines.append('')
        scores = list_scores(session, account_id, key)
        if scores:
            lines.extend(['## 人工评分', ''])
            for sheet in scores:
                values = ', '.join(f"{dimension}={entry.get('final') if entry.get('final') is not None else '未评分'}"
                                   for dimension, entry in sheet['scores'].items())
                lines.append(f"- {sheet['scope']} {', '.join(sheet['trade_ids']) or key}：{values}；{sheet['comment']}")
            lines.append('')
        attachments = list_attachments(session, account_id, key)
        if attachments:
            lines.extend(['## 图片附件', '', '图片文件未包含在此 Markdown，请在 Trade 应用中打开原复盘查看。', ''])
            lines.extend(f"- {item['original_name']}（{item['width']}×{item['height']}，ID {item['id']}）"
                         for item in attachments)
            lines.append('')
    elif kind in PERIOD_FIELDS:
        start, end = bounds(kind, key)
        period = get_period(session, account_id, kind, key)
        if period['revision'] == 0:
            raise TradeError('REVIEW_NOT_FOUND', '该周期没有已保存的复盘', 404)
        name = '周' if kind == 'weekly' else '月'
        lines = [f'# {key} {name}复盘', '', f'- 账户：{account.name}',
                 f'- 日期：{start} 至 {end}', f'- 修订：{period['revision']}', '']
        derived = period['derived']
        lines.extend(['## 账本统计', '', f"- 交易笔数：{derived['trade_count']}",
                      f"- 已结束回合：{derived['closed_rounds']}",
                      f"- 回合盈亏：{derived['closed_pnl']}",
                      f"- 周期收益：{derived['return_pct'] + '%' if derived['return_pct'] is not None else '缺少已确认快照或期初基线'}",
                      f"- 最小回撤：{derived['min_drawdown_pct'] + '%' if derived['min_drawdown_pct'] is not None else '缺失'}",
                      f"- 统计状态：{derived['status']}", ''])
        for field, title in PERIOD_FIELDS[kind]:
            _section(lines, title, period['sections'].get(field))
    else:
        raise TradeError('INVALID_PERIOD_KIND', '导出类型无效')

    trades = session.scalars(select(Trade).where(
        Trade.account_id == account_id, Trade.trade_date >= start,
        Trade.trade_date <= end, Trade.voided_at.is_(None)).order_by(
        Trade.trade_date, Trade.created_at, Trade.id)).all()
    if trades:
        lines.extend(['## 关联成交', ''])
        lines.extend(f'- {row.trade_date} {row.symbol} {row.side} {row.quantity} 股 × {row.price}；费用 {row.fee}；ID {row.id}'
                     for row in trades)
        lines.append('')
    related_rounds = _rounds(session, account_id, start, end)
    if related_rounds:
        notes = {row['round_id']: row for row in list_round_notes(session, account_id)}
        lines.extend(['## 关联回合', ''])
        for row in related_rounds:
            lines.append(f"- {row['id']} {row['symbol']} {row['status']}；盈亏 {row.get('pnl') or '未计算'}")
            if notes.get(row['id'], {}).get('summary'):
                lines.append(f"  - 人工摘要：{notes[row['id']]['summary']}")
        lines.append('')
    author = get_settings(session)['author']
    if author:
        lines.insert(2, '- 署名：' + author)
    return '\n'.join(lines).rstrip() + '\n'
