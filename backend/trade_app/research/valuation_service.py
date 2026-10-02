"""Five-factor scenario runs and source-labelled live quote hints."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.market.symbols import market_symbol_key, normalize_market_symbol
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.valuation_domain import (
    ValuationInputs, calc_implied_sentiment, calc_theoretical_cap,
    count_limit_ups, growth_rate_to_coef, index_points_to_coef,
    parse_eastmoney_quote, sentiment_label, suggest_base_pe,
    suggest_sentiment_from_activity, symbol_to_secid,
)
from trade_app.research.valuation_models import ValuationRun


QUOTE_URL = 'https://push2.eastmoney.com/api/qt/stock/get'
QUOTE_FIELDS = 'f43,f58,f116,f127,f162,f163,f164'


def prepare_valuation_run(body: dict) -> dict:
    scenarios = []
    for scenario in body['scenarios']:
        earnings = float(scenario['earnings_yi'])
        growth = growth_rate_to_coef(scenario['growth_rate_pct'])
        index = index_points_to_coef(scenario['index_points'])
        result = {'label': scenario['label'], 'earnings_yi': earnings,
                  'growth_rate_pct': scenario['growth_rate_pct'], 'growth_coef': growth,
                  'base_pe': scenario['base_pe'], 'index_points': scenario['index_points'],
                  'index_coef': index, 'sentiment_coef': scenario['sentiment_coef'],
                  'actual_cap_yi': float(scenario['actual_cap_yi'])
                  if scenario['actual_cap_yi'] is not None else None}
        if earnings <= 0 or growth <= 0:
            result.update({'status': 'not_applicable_nonpositive_earnings_or_growth',
                           'theoretical_cap_yi': None, 'implied_sentiment_coef': None,
                           'gap_pct': None})
        else:
            calculated = calc_theoretical_cap(ValuationInputs(
                earnings_yi=earnings, growth_coef=growth, base_pe=scenario['base_pe'],
                index_coef=index, sentiment_coef=scenario['sentiment_coef']))
            theoretical = calculated.theoretical_cap_yi
            actual = result['actual_cap_yi']
            implied = calc_implied_sentiment(actual, earnings_yi=earnings,
                                             growth_coef=growth, base_pe=scenario['base_pe'],
                                             index_coef=index) if actual is not None else None
            result.update({'status': 'calculated',
                           'theoretical_cap_yi': round(theoretical, 4),
                           'implied_sentiment_coef': round(implied, 4)
                           if implied is not None else None,
                           'implied_sentiment_label': sentiment_label(implied)
                           if implied is not None else None,
                           'gap_pct': round((theoretical / actual - 1) * 100, 2)
                           if actual is not None and actual > 0 else None,
                           'components': asdict(calculated)})
        scenarios.append(result)
    output = {'symbol': body['symbol'].lower().strip(), 'scenarios': scenarios,
              'formula': '市值（亿元）= 当期盈利（亿元）× 复合增速系数 × 基准 PE × 大盘水位系数 × 情绪溢价',
              'quality_flags': ['SCENARIOS_ARE_ASSUMPTIONS_NOT_FORECASTS']}
    code_hash = hashlib.sha256(Path(__file__).read_bytes() +
                               (Path(__file__).parent / 'valuation_domain.py').read_bytes()).hexdigest()
    identity = json.dumps({'request': body, 'code_sha256': code_hash},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return {'id': hashlib.sha256(identity).hexdigest(), 'request': body,
            'result': output, 'code_sha256': code_hash}


def _view(row: ValuationRun, detail: bool = False) -> dict:
    result = json.loads(row.result_json)
    return {'id': row.id, 'created_at': row.created_at, 'symbol': result['symbol'],
            'scenario_count': len(result['scenarios']),
            **({'request': json.loads(row.request_json), 'result': result} if detail else {})}


def save_valuation_run(session: Session, prepared: dict) -> dict:
    row = session.get(ValuationRun, prepared['id'])
    if row is None:
        row = ValuationRun(id=prepared['id'],
                           request_json=json.dumps(prepared['request'], ensure_ascii=False),
                           result_json=json.dumps(prepared['result'], ensure_ascii=False),
                           code_sha256=prepared['code_sha256'], created_at=utc_now())
        session.add(row)
        session.flush()
    return _view(row, True)


def list_valuation_runs(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(ValuationRun).order_by(
        ValuationRun.created_at.desc(), ValuationRun.id.desc()).limit(100))]


def get_valuation_run(session: Session, run_id: str) -> dict:
    row = session.get(ValuationRun, run_id)
    if row is None:
        raise TradeError('VALUATION_RUN_NOT_FOUND', '情绪估值记录不存在', 404)
    return _view(row, True)


def fetch_valuation_quote(session: Session, data_dir: Path, symbol: str,
                          dataset_id: str | None = None) -> dict:
    normalized = ''.join(normalize_market_symbol(symbol))
    secid = symbol_to_secid(normalized)
    if secid is None:
        raise TradeError('INVALID_SYMBOL', '请输入有效的六位证券代码或带市场前缀的代码')
    quality: list[str] = []
    quote: dict = {'name': None, 'industry': None, 'price': None,
                   'market_cap_yi': None, 'pe_ttm': None, 'pe_dynamic': None,
                   'pe_static': None, 'implied_earnings_yi': None}
    index_close = None
    headers = {'User-Agent': 'TradeRebuild/1.0'}
    with httpx.Client(timeout=6.0, follow_redirects=True) as client:
        try:
            response = client.get(QUOTE_URL, params={'secid': secid, 'fields': QUOTE_FIELDS},
                                  headers=headers)
            response.raise_for_status()
            payload = response.json()
            data = payload.get('data') if isinstance(payload, dict) else None
            if isinstance(data, dict):
                quote = parse_eastmoney_quote(data)
            else:
                quality.append('QUOTE_DATA_UNAVAILABLE')
        except (httpx.HTTPError, ValueError, TypeError):
            quality.append('QUOTE_FETCH_FAILED')
        try:
            response = client.get(QUOTE_URL, params={'secid': '1.000001', 'fields': 'f43'},
                                  headers=headers)
            response.raise_for_status()
            data = response.json().get('data')
            if isinstance(data, dict) and data.get('f43') is not None:
                value = float(data['f43']) / 100
                index_close = value if math.isfinite(value) else None
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            quality.append('INDEX_QUOTE_FETCH_FAILED')
    if index_close is None or index_close <= 0:
        quality.append('INDEX_POINTS_UNKNOWN')
        index_close = None
    industry = str(quote.get('industry') or '').strip()
    if not industry:
        quality.append('INDUSTRY_UNKNOWN_DEFAULT_PE')
    suggested_pe, tier, label = suggest_base_pe(industry)
    activity = None
    activity_last_date = None
    if dataset_id:
        dataset = get_dataset(session, data_dir, dataset_id)
        if market_symbol_key(dataset['symbol']) != market_symbol_key(normalized):
            raise TradeError('DATASET_SYMBOL_MISMATCH', '所选冻结行情与证券代码不匹配', 409)
        bars = [bar for bar in dataset['bars'] if bar['event_date'] <= utc_now()[:10]]
        activity = count_limit_ups(bars, normalized, str(quote.get('name') or ''), window=60)
        activity_last_date = bars[-1]['event_date'] if bars else None
        if dataset['availability_quality'] == 'historical_availability_unknown':
            quality.append('ACTIVITY_HISTORICAL_AVAILABILITY_UNKNOWN')
    else:
        quality.append('ACTIVITY_DATASET_NOT_SELECTED')
    if quote.get('pe_ttm') is None:
        quality.append('TTM_PE_NOT_AVAILABLE_EARNINGS_NOT_INFERRED')
    return {'symbol': normalized, 'name': str(quote.get('name') or ''),
            'industry': industry, 'price': quote.get('price'),
            'market_cap_yi': quote.get('market_cap_yi'),
            'pe_ttm': quote.get('pe_ttm'), 'pe_dynamic': quote.get('pe_dynamic'),
            'pe_static': quote.get('pe_static'),
            'implied_earnings_yi': quote.get('implied_earnings_yi'),
            'suggested_pe': suggested_pe, 'suggested_pe_tier': tier,
            'suggested_pe_label': label,
            'index_symbol': 'sh000001', 'index_close': index_close,
            'index_coef': round(index_points_to_coef(index_close), 4)
            if index_close is not None else None,
            'limit_up_count_60d': activity['limit_up_count'] if activity else None,
            'big_gain_days_60d': activity['big_gain_days'] if activity else None,
            'suggested_sentiment_coef': suggest_sentiment_from_activity(
                activity['limit_up_count'], activity['big_gain_days']) if activity else None,
            'activity_dataset_id': dataset_id, 'activity_last_date': activity_last_date,
            'source': 'eastmoney_stock_get', 'fetched_at': utc_now(),
            'quality_flags': quality}
