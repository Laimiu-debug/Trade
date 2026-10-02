"""Read-only reminders inferred from recorded facts, never invented trading days."""
from sqlalchemy import select, union

from trade_app.reviews.models import DailyReview
from trade_app.trading.models import AssetSnapshot, CashFlow, Trade
from trade_app.trading.service import account_or_error


def reminders(session, account_id: str, limit: int = 100) -> dict:
    account_or_error(session, account_id, real=True)
    trade_days = select(Trade.trade_date.label('day')).where(
        Trade.account_id == account_id, Trade.voided_at.is_(None)).distinct().subquery()
    flow_days = select(CashFlow.flow_date.label('day')).where(
        CashFlow.account_id == account_id, CashFlow.voided_at.is_(None)).distinct().subquery()
    snapshot_days = select(AssetSnapshot.snap_date.label('day')).where(
        AssetSnapshot.account_id == account_id).subquery()
    review_days = select(DailyReview.review_date.label('day')).where(
        DailyReview.account_id == account_id).subquery()
    all_days = union(select(trade_days.c.day), select(flow_days.c.day),
                     select(snapshot_days.c.day), select(review_days.c.day)).subquery()
    rows = session.execute(select(all_days.c.day, trade_days.c.day.label('trade'),
        flow_days.c.day.label('flow'), snapshot_days.c.day.label('snapshot'), review_days.c.day.label('review'))
        .outerjoin(trade_days, trade_days.c.day == all_days.c.day)
        .outerjoin(flow_days, flow_days.c.day == all_days.c.day)
        .outerjoin(snapshot_days, snapshot_days.c.day == all_days.c.day)
        .outerjoin(review_days, review_days.c.day == all_days.c.day)
        .where(snapshot_days.c.day.is_(None) | (review_days.c.day.is_(None) &
                (trade_days.c.day.is_not(None) | snapshot_days.c.day.is_not(None))))
        .order_by(all_days.c.day.desc())).mappings()
    reviews, snapshots = [], []
    counts = {'missing_reviews': 0, 'missing_snapshots': 0}
    for row in rows:
        item = {'date': row['day'], 'has_trades': row['trade'] is not None,
                'has_cash_flows': row['flow'] is not None, 'has_review': row['review'] is not None,
                'has_snapshot': row['snapshot'] is not None}
        if row['review'] is None and (row['trade'] is not None or row['snapshot'] is not None):
            counts['missing_reviews'] += 1
            if len(reviews) < limit:
                reviews.append(item)
        if row['snapshot'] is None:
            counts['missing_snapshots'] += 1
            if len(snapshots) < limit:
                snapshots.append(item)
    return {'missing_reviews': reviews, 'missing_snapshots': snapshots, 'total_count': counts, 'limit': limit,
            'method': '缺复盘按交易或已确认快照日期提示；缺快照按交易、资金流水或已保存复盘日期提示。'
                      '不推测交易日、不自动补价或生成记录。'}
