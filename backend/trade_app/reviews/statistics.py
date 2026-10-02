"""One read-only source contract for statistics PDF and tabular exports."""
from trade_app.analytics.performance import performance
from trade_app.reviews.sim_performance import sim_performance
from trade_app.trading.service import account_or_error
from trade_app.trading.sim_models import SimWallet
from trade_app.platform.types import utc_now
from trade_app.platform.types import TradeError
from trade_app.reviews.print_settings import get_settings


def statistics_source(session, account_id, *, kind='monthly', limit=24, date_basis='sell', date_from=None, date_to=None):
    account = account_or_error(session, account_id)
    if account.kind != 'sim' and (date_from is not None or date_to is not None):
        raise TradeError('INVALID_PERFORMANCE_RANGE', '实盘统计按所选周期与数量导出；自定义成交日期范围适用于模拟统计')
    value = sim_performance(session, account_id, date_basis, date_from, date_to) if account.kind == 'sim' else performance(session, account_id, kind, limit)
    if account.kind == 'sim':
        value['wallet_revision'] = session.get(SimWallet, account_id).revision
    meta = {'account_id': account_id, 'name': account.name, 'account_kind': account.kind,
            'input_revision': account.input_revision, 'generated_at': utc_now(), 'author': get_settings(session)['author']}
    return value, meta
