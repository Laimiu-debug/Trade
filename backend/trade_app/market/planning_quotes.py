"""Read selected frozen prices for a historical review, without online fetching."""
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from trade_app.market.domain import eligible_bars
from trade_app.market.service import get_dataset
from trade_app.market.symbols import market_symbol_key
from trade_app.platform.types import TradeError


def planning_quotes(session: Session, data_dir: Path, day: str, dataset_ids: list[str]) -> dict:
    cutoff = datetime.combine(date.fromisoformat(day), time(23, 59, 59), ZoneInfo('Asia/Shanghai')).isoformat()
    if len(dataset_ids) > 100 or len(dataset_ids) != len(set(dataset_ids)):
        raise TradeError('INVALID_PLANNING_DATASETS', '行情样本重复或超过 100 个')
    result = {}
    for dataset_id in dataset_ids:
        dataset = get_dataset(session, data_dir, dataset_id)
        key = market_symbol_key(dataset['symbol'])
        if key in result:
            raise TradeError('DUPLICATE_SYMBOL_DATASET', '同一标的只能选择一个行情样本')
        bars, quality = eligible_bars(dataset['bars'], cutoff, True)
        quote = next((bar for bar in reversed(bars) if bar['event_date'] <= day), None)
        result[key] = {'price': quote['close'] if quote else None, 'source': 'frozen_dataset',
            'dataset_id': dataset_id, 'quote_date': quote['event_date'] if quote else None,
            'available_at': quote.get('available_at') if quote else None, 'decision_at': cutoff,
            'quality_flags': quality + (['quote_unavailable_at_decision'] if quote is None
                                       else ['stale_quote'] if quote['event_date'] < day else [])}
    return result
