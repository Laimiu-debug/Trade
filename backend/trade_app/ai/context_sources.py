"""Bounded snapshots of explicitly selected completed research artifacts."""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from trade_app.ai.config import encode
from trade_app.platform.types import TradeError
from trade_app.research.backtest_models import BacktestRun
from trade_app.research.portfolio_service import get_portfolio, list_portfolios
from trade_app.research.valuation_models import ValuationRun

KINDS = {'valuation', 'backtest', 'portfolio'}


def catalog(session):
    rows = []
    for item in session.scalars(select(ValuationRun).order_by(ValuationRun.created_at.desc()).limit(100)):
        result = json.loads(item.result_json)
        rows.append({'type': 'valuation', 'id': item.id, 'label': result.get('symbol', '') + ' · 情景估值', 'created_at': item.created_at, 'state': 'succeeded'})
    for item in session.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(100)):
        rows.append({'type': 'backtest', 'id': item.id, 'label': item.strategy_id + ' · ' + item.dataset_id[:8], 'created_at': item.created_at, 'state': item.state})
    for item in list_portfolios(session):
        rows.append({'type': 'portfolio', 'id': item['id'], 'label': item['name'], 'created_at': item['created_at'], 'state': item['state']})
    return rows


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _tail(items, count):
    return {'items': items[-count:], 'total_count': len(items), 'omitted_count': max(0, len(items) - count), 'selection': 'last_records_in_saved_order'}


def _after_cutoff(day, decision):
    # A full daily result cannot be presented as known earlier on that day.
    return datetime.fromisoformat(day + 'T23:59:59.999999+08:00') > decision


def freeze_sources(session, raw, decision_at):
    if not isinstance(raw, list) or len(raw) > 3:
        raise TradeError('INVALID_AI_SOURCES', '额外研究产物最多选择3项')
    if raw and decision_at is None:
        raise TradeError('INVALID_AI_CONTEXT_TIME', '携带研究产物须明确数据截止时间')
    seen, result = set(), []
    decision = datetime.fromisoformat(decision_at).astimezone(timezone.utc) if decision_at else None
    for source in raw:
        if (not isinstance(source, dict) or set(source) != {'type', 'id'} or not isinstance(source['type'], str)
                or source['type'] not in KINDS or not isinstance(source['id'], str)
                or not source['id'] or len(source['id']) > 128):
            raise TradeError('INVALID_AI_SOURCES', '研究来源须为支持的type和明确id')
        identity = (source['type'], source['id'])
        if identity in seen:
            raise TradeError('INVALID_AI_SOURCES', '不可重复选择同一研究产物')
        seen.add(identity)
        kind, run_id = identity
        if kind == 'valuation':
            row = session.get(ValuationRun, run_id)
            if row is None:
                raise TradeError('AI_SOURCE_NOT_FOUND', '所选情景估值不存在', 404)
            if datetime.fromisoformat(row.created_at).astimezone(timezone.utc) > decision:
                raise TradeError('AI_SOURCE_AFTER_CUTOFF', '估值假设记录创建时间晚于截止时间；没有可验证的更早假设来源')
            value = {'type': kind, 'id': run_id, 'code_sha256': row.code_sha256, 'created_at': row.created_at,
                     'request': json.loads(row.request_json), 'result': json.loads(row.result_json),
                     'source_date': None, 'availability': 'record_creation_only; assumptions_not_historical_fundamentals',
                     'result_sha256': _hash(row.result_json)}
        elif kind == 'backtest':
            row = session.get(BacktestRun, run_id)
            if row is None:
                raise TradeError('AI_SOURCE_NOT_FOUND', '所选单股回测不存在', 404)
            if row.state != 'succeeded' or not row.result_json or not row.result_sha256:
                raise TradeError('AI_SOURCE_NOT_COMPLETE', '只允许携带已完成的单股回测', 409)
            if _hash(row.result_json) != row.result_sha256:
                raise TradeError('AI_SOURCE_CORRUPT', '单股回测结果摘要不匹配', 409)
            calculated = json.loads(row.result_json)
            dates = [item['date'] for item in calculated.get('equity', [])]
            if not dates or _after_cutoff(max(dates), decision):
                raise TradeError('AI_SOURCE_AFTER_CUTOFF', '回测覆盖区间晚于数据截止日或没有可验证日期')
            value = {'type': kind, 'id': run_id, 'dataset_id': row.dataset_id, 'strategy_id': row.strategy_id,
                     'strategy_version': row.strategy_version, 'code_sha256': row.code_sha256,
                     'calculation_version': row.calculation_version, 'execution_version': row.execution_version,
                     'created_at': row.created_at, 'completed_at': row.updated_at, 'result_sha256': row.result_sha256,
                     'params': json.loads(row.params_json), 'config': json.loads(row.config_json),
                     'source_date': max(dates), 'start_date': min(dates),
                     'result': {key: val for key, val in calculated.items() if key not in ('trades', 'equity', 'decisions')},
                     'trades': _tail(calculated.get('trades', []), 20), 'equity': _tail(calculated.get('equity', []), 10),
                     'omitted_decision_count': len(calculated.get('decisions', []))}
        else:
            item = get_portfolio(session, run_id, full=True)
            if item['state'] != 'succeeded' or not item['result_sha256']:
                raise TradeError('AI_SOURCE_NOT_COMPLETE', '只允许携带已完成的组合回测', 409)
            context = item['frozen_context']
            if _after_cutoff(context['end_date'], decision):
                raise TradeError('AI_SOURCE_AFTER_CUTOFF', '组合区间晚于数据截止日')
            value = {key: item[key] for key in ('id', 'name', 'mode', 'strategy_id', 'input_sha256', 'code_sha256', 'result_sha256', 'summary', 'limitations', 'created_at', 'updated_at')}
            value.update(type=kind, source_date=context['end_date'], frozen_context=context,
                         trades=_tail(item['result']['trades'], 20), equity=_tail(item['result']['equity'], 10),
                         omitted_decision_count=len(item['result']['decisions']), omitted_pool_day_count=len(item['result']['pool_history']))
        if kind in {'backtest', 'portfolio'}:
            value['availability'] = 'retrospective_saved_calculation; source_date_is_not_original_publication_time'
            value['historical_selection_knowledge_verified'] = False
        value['frozen_source_sha256'] = _hash(encode(value))
        result.append(value)
    return result
