"""Revisioned signal baskets and immutable evaluation reports, separate from accounts."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.domain import eligible_bars
from trade_app.market.service import get_dataset
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.types import TradeError, new_id, price_units, utc_now
from trade_app.research.basket_domain import (
    CALCULATION_VERSION, DEFAULT_CONFIG, decision_date, evaluate_constituent,
    normalize_config, summarize_constituents, validate_day,
)
from trade_app.research.basket_models import SignalBasket, SignalBasketAudit, SignalBasketEvaluation
from trade_app.research.models import ResearchRun
from trade_app.research.scan_service import get_scan
from trade_app.research.service import run_data


def _encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def _symbol(raw: str) -> str:
    exchange, code = normalize_a_share_symbol(raw)
    return exchange + code


def _text(raw: object, name: str, maximum: int, required: bool = False) -> str:
    if not isinstance(raw, str) or len(raw.strip()) > maximum or (required and not raw.strip()):
        raise TradeError('INVALID_BASKET_TEXT', f'{name} 须为{maximum}字以内的文本' + ('且不能为空' if required else ''))
    return raw.strip()


def _basket(session: Session, basket_id: str, include_deleted=False) -> SignalBasket:
    row = session.get(SignalBasket, basket_id)
    if row is None or (row.deleted and not include_deleted):
        raise TradeError('SIGNAL_BASKET_NOT_FOUND', '信号篮子不存在或已删除', 404)
    return row


def _check_revision(row: SignalBasket, revision: object) -> None:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision != row.revision:
        raise TradeError('REVISION_CONFLICT', '信号篮子已修改，请刷新后重试', 409)


def _view(row: SignalBasket, detail=True) -> dict:
    snapshot = json.loads(row.snapshot_json)
    constituents = snapshot['constituents']
    strategies = sorted({signal['strategy_id'] for member in constituents for signal in member['signals']})
    return {'id': row.id, 'record_id': row.id, 'revision': row.revision, 'deleted': bool(row.deleted),
            'name': snapshot['name'], 'notes': snapshot['notes'], 'source': snapshot['source'],
            'config': snapshot['config'], 'total_constituents': len(constituents),
            'strategy_ids': strategies, 'strategy_id': strategies[0] if len(strategies) == 1 else 'multi_strategy',
            'signal_date': max(member['source_date'] for member in constituents),
            'created_at': row.created_at, 'updated_at': row.updated_at,
            **({'constituents': constituents} if detail else {})}


def _audit(session: Session, row: SignalBasket, action: str, snapshot: dict) -> None:
    session.add(SignalBasketAudit(id=new_id(), basket_id=row.id, revision=row.revision,
                                  action=action, snapshot_json=_encode(snapshot), created_at=utc_now()))


def _freeze_members(session: Session, data_dir: Path, run_ids: list[str], *, merge=False) -> list[dict]:
    if not isinstance(run_ids, list) or not 1 <= len(run_ids) <= (1000 if merge else 100):
        raise TradeError('INVALID_BASKET_MEMBERS', '手动篮子需要 1 至 100 个正信号研究记录')
    if any(not isinstance(value, str) for value in run_ids) or len(run_ids) != len(set(run_ids)):
        raise TradeError('DUPLICATE_BASKET_SIGNAL', '成分信号记录不能重复')
    grouped = defaultdict(list)
    datasets = {}
    for run_id in sorted(run_ids):
        row = session.get(ResearchRun, run_id)
        if row is None:
            raise TradeError('RESEARCH_RUN_NOT_FOUND', '成分研究记录不存在', 404)
        signal = run_data(row)
        result = signal['result']
        if result.get('status') != 'computed' or result.get('signal') is not True:
            raise TradeError('BASKET_SIGNAL_NOT_POSITIVE', '篮子成分必须来自已计算且触发的研究信号', 409)
        source_date = validate_day(result.get('source_date'))
        resolved_decision = decision_date(signal['decision_at'])
        if row.dataset_id not in datasets:
            datasets[row.dataset_id] = get_dataset(session, data_dir, row.dataset_id)
        dataset = datasets[row.dataset_id]
        canonical = _symbol(dataset['symbol'])
        candidate = result.get('candidate') or {}
        if not candidate.get('symbol') or _symbol(candidate['symbol']) != canonical:
            raise TradeError('BASKET_SIGNAL_SYMBOL_MISMATCH', '信号与冻结行情的证券不一致', 409)
        if canonical in grouped and not merge:
            raise TradeError('DUPLICATE_BASKET_SYMBOL', '每个证券只能手动选择一条信号，别名也视为重复')
        grouped[canonical].append((signal, source_date, resolved_decision))
    if len(grouped) > 100:
        raise TradeError('INVALID_BASKET_MEMBERS', '一个篮子最多支持 100 个证券')
    members = []
    for symbol, inputs in sorted(grouped.items()):
        dataset_ids = {entry[0]['dataset_id'] for entry in inputs}
        if len(dataset_ids) != 1 or (merge and len({entry[2] for entry in inputs}) != 1):
            raise TradeError('BASKET_SOURCE_CONFLICT', '合并证据必须来自同一冻结样本和同一决策日')
        source_date = max(entry[1] for entry in inputs)
        resolved_decision = max(entry[2] for entry in inputs)
        flags = {flag for entry in inputs for flag in entry[0]['result'].get('quality_flags', [])}
        if any(entry[0]['result'].get('draft_eligible') is False for entry in inputs):
            flags.add('OBSERVATION_NOT_ORDER_ELIGIBLE')
        members.append({'symbol': symbol, 'source_date': source_date,
                        'decision_date': resolved_decision, 'anchor_date': max(source_date, resolved_decision),
                        'run_ids': [entry[0]['id'] for entry in inputs], 'dataset_id': next(iter(dataset_ids)),
                        'signals': [entry[0] for entry in inputs], 'quality_flags': sorted(flags)})
    return members


def _members_and_source(session: Session, data_dir: Path, body: dict) -> tuple[list, dict]:
    if body.get('scan_id') is None:
        return _freeze_members(session, data_dir, body.get('run_ids')), {'kind': 'manual'}
    if body.get('run_ids') is not None:
        raise TradeError('BASKET_SOURCE_CONFLICT', '手动信号与扫描来源不能同时指定')
    selection = body.get('selection', 'union')
    if selection not in ('union', 'intersection'):
        raise TradeError('INVALID_BASKET_SELECTION', '扫描来源须为 union 或 intersection')
    scan = get_scan(session, body['scan_id'])
    groups = scan['result'][selection]
    scan_date = body.get('scan_date')
    dates = sorted({item['decision_date'] for item in groups})
    if scan_date is None:
        if len(dates) != 1:
            raise TradeError('BASKET_SCAN_DATE_REQUIRED', '扫描包含零个或多个命中日期，请明确选择扫描日期')
        scan_date = dates[0]
    scan_date = validate_day(scan_date)
    groups = [item for item in groups if item['decision_date'] == scan_date]
    if not groups:
        raise TradeError('BASKET_SCAN_EMPTY', '所选日期与集合没有可用信号', 409)
    ids = [run_id for item in groups for run_id in item['run_ids']]
    members = _freeze_members(session, data_dir, ids, merge=True)
    return members, {'kind': 'scan', 'scan_id': scan['id'], 'scan_code_sha256': scan['code_sha256'],
                     'selection': selection, 'scan_date': scan_date, 'groups': groups}


def create_basket(session: Session, data_dir: Path, body: dict) -> dict:
    members, source = _members_and_source(session, data_dir, body)
    snapshot = {'name': _text(body.get('name'), '篮子名称', 128, True),
                'notes': _text(body.get('notes', ''), '备注', 1000), 'source': source,
                'config': normalize_config({key: body[key] for key in DEFAULT_CONFIG if key in body}),
                'constituents': members}
    now = utc_now()
    row = SignalBasket(id=new_id(), revision=1, snapshot_json=_encode(snapshot), deleted=0,
                        created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    _audit(session, row, 'create', _view(row))
    return _view(row)


def update_basket(session: Session, data_dir: Path, basket_id: str, body: dict) -> dict:
    row = _basket(session, basket_id)
    _check_revision(row, body.get('expected_revision'))
    if set(body) - {'expected_revision', 'name', 'notes', 'run_ids', *DEFAULT_CONFIG}:
        raise TradeError('INVALID_BASKET_UPDATE', '包含不支持的篮子编辑字段')
    snapshot = json.loads(row.snapshot_json)
    if 'name' in body:
        snapshot['name'] = _text(body['name'], '篮子名称', 128, True)
    if 'notes' in body:
        snapshot['notes'] = _text(body['notes'], '备注', 1000)
    if 'run_ids' in body:
        snapshot['constituents'] = _freeze_members(session, data_dir, body['run_ids'])
        snapshot['source'] = {'kind': 'manual'}
    snapshot['config'] = normalize_config({key: body[key] for key in DEFAULT_CONFIG if key in body}, snapshot['config'])
    row.snapshot_json = _encode(snapshot)
    row.revision += 1
    row.updated_at = utc_now()
    _audit(session, row, 'update', _view(row))
    session.flush()
    return _view(row)


def get_basket(session: Session, basket_id: str, include_deleted=False) -> dict:
    return _view(_basket(session, basket_id, include_deleted))


def list_baskets(session: Session, *, include_deleted=False, strategy_id: str | None = None,
                  name: str | None = None) -> list[dict]:
    statement = select(SignalBasket).order_by(SignalBasket.updated_at.desc(), SignalBasket.id)
    if not include_deleted:
        statement = statement.where(SignalBasket.deleted == 0)
    result = []
    for row in session.scalars(statement):
        view = _view(row, False)
        if strategy_id and strategy_id not in view['strategy_ids']:
            continue
        if name and name.lower() not in view['name'].lower():
            continue
        result.append(view)
        if len(result) == 100:
            break
    return result


def delete_baskets(session: Session, body: dict) -> dict:
    items = body.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise TradeError('INVALID_BASKET_DELETE', '批量删除需要 1 至 100 个篮子及其版本')
    rows, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] in seen:
            raise TradeError('INVALID_BASKET_DELETE', '批量删除的篮子 ID 无效或重复')
        seen.add(item['id'])
        row = _basket(session, item['id'])
        _check_revision(row, item.get('expected_revision'))
        rows.append(row)
    for row in rows:
        row.deleted = 1
        row.revision += 1
        row.updated_at = utc_now()
        _audit(session, row, 'delete', _view(row))
    session.flush()
    return {'deleted': True, 'ids': [row.id for row in rows], 'count': len(rows),
            'note': '原始研究、审计及历史评估报告仍然保留'}


def list_audit(session: Session, basket_id: str) -> list[dict]:
    _basket(session, basket_id, True)
    return [{'id': row.id, 'basket_id': row.basket_id, 'revision': row.revision,
             'action': row.action, 'snapshot': json.loads(row.snapshot_json), 'created_at': row.created_at}
            for row in session.scalars(select(SignalBasketAudit).where(
                SignalBasketAudit.basket_id == basket_id).order_by(SignalBasketAudit.created_at, SignalBasketAudit.id))]


def _frozen_history(bars: list[dict]) -> list:
    def available_at(bar: dict) -> str | None:
        value = bar.get('available_at')
        return (datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
                if value is not None else None)

    return [[bar['event_date'], *[price_units(bar[field]) for field in ('open', 'high', 'low', 'close')],
             int(bar['volume']), Decimal(str(bar['amount'])) if bar.get('amount') is not None else None,
             available_at(bar)] for bar in bars]


def _evaluation_view(row: SignalBasketEvaluation, detail=True) -> dict:
    request, result = json.loads(row.request_json), json.loads(row.result_json)
    return {'id': row.id, 'basket_id': row.basket_id, 'basket_revision': row.basket_revision,
            'created_at': row.created_at, 'code_sha256': row.code_sha256,
            'as_of_date': request['as_of_date'], 'summary': result['summary'],
            **({'request': request, 'result': result} if detail else {})}


def evaluate_basket(session: Session, data_dir: Path, basket_id: str, body: dict) -> dict:
    row = _basket(session, basket_id)
    _check_revision(row, body.get('expected_revision'))
    basket = _view(row)
    as_of_date = validate_day(body.get('as_of_date'))
    if as_of_date < max(member['anchor_date'] for member in basket['constituents']):
        raise TradeError('BASKET_EVALUATION_BEFORE_SIGNAL', '评估日期不得早于任一成分的信号或上海决策日期')
    strict = body.get('strict', True)
    if not isinstance(strict, bool):
        raise TradeError('INVALID_BASKET_STRICT', 'strict 须为 true 或 false')
    overrides = body.get('forward_datasets', {})
    if not isinstance(overrides, dict):
        raise TradeError('INVALID_BASKET_DATASETS', '后续行情须为证券到冻结样本 ID 的映射')
    canonical_overrides = {}
    member_symbols = {member['symbol'] for member in basket['constituents']}
    for symbol, dataset_id in overrides.items():
        canonical = _symbol(symbol)
        if canonical not in member_symbols or canonical in canonical_overrides or not isinstance(dataset_id, str):
            raise TradeError('INVALID_BASKET_DATASETS', '后续行情包含非成分证券或重复别名')
        canonical_overrides[canonical] = dataset_id
    frames, datasets = [], []
    for member in basket['constituents']:
        original = get_dataset(session, data_dir, member['dataset_id'])
        selected_id = canonical_overrides.get(member['symbol'], member['dataset_id'])
        selected = get_dataset(session, data_dir, selected_id)
        if _symbol(selected['symbol']) != member['symbol'] or selected['adjustment'] != original['adjustment']:
            raise TradeError('BASKET_DATASET_SYMBOL_MISMATCH', '后续样本的证券或复权口径不匹配', 409)
        for signal in member['signals']:
            old_prefix, _ = eligible_bars(original['bars'], signal['decision_at'], signal['strict'])
            selected_prefix, _ = eligible_bars(selected['bars'], signal['decision_at'], signal['strict'])
            if _frozen_history(old_prefix) != _frozen_history(selected_prefix):
                raise TradeError('BASKET_HISTORY_CHANGED', '后续样本改变了信号决策时已可得的 OHLCV、成交额或可得时间历史', 409)
        datasets.append({'symbol': member['symbol'], 'dataset_id': selected_id,
                         'content_sha256': selected_id, 'original_dataset_id': original['id'],
                         'provider': selected['provider'], 'adjustment': selected['adjustment']})
        frames.append(evaluate_constituent(member, selected['bars'], as_of_date=as_of_date,
                                           strict=strict, config=basket['config'], dataset_id=selected_id))
    request = {'basket_snapshot': basket, 'as_of_date': as_of_date, 'strict': strict,
               'datasets': datasets, 'decision_timezone': 'Asia/Shanghai', 'as_of_time': '23:59:59'}
    result = {'calculation_version': CALCULATION_VERSION, 'constituents': frames,
              **summarize_constituents(frames),
              'benchmark_available': False, 'benchmark_return': None,
              'quality_flags': ['OBSERVED_BAR_CALENDAR', 'LIQUIDITY_AND_LIMIT_EXECUTION_UNVERIFIED'],
              'notes': ['这是研究信号篮子，不是真实发行的 ETF，也不会修改账户或提交委托',
                        'T+1/T+2 从源日期与上海决策日期的较晚日之后，按该证券实际样本日开盘价计算',
                        'legacy_signal_anchor 的持有目标为锚点后第 N 根日线；同日卖出仅展示原始收益，费用后执行结果为空',
                        'complete_overnights 的退出为入场后 N 根日线的收盘价；费用按冻结股数及共享规则估算',
                        '完整持有期缺失显示待观察；原始与费用后收益使用各自有效样本等权平均，并展示覆盖数量',
                        '旧版涨停时会将买价压到涨停价；本版保留真实样本开盘价，不声称能在涨停成交',
                        '价格观察曲线及当前收益会继续记录持有目标日之后的价格；固定持有期收益单独列示',
                        '没有基准样本时，基准与超额收益不填零；曲线是每日有价格成分的等权原始收益，不是组合净值']}
    root = Path(__file__).parent
    dependencies = [Path(__file__), root / 'basket_domain.py', root.parent / 'trading/domain.py',
                    root.parent / 'market/domain.py', root.parent / 'market/symbols.py',
                    root.parent / 'platform/symbols.py']
    code_sha256 = hashlib.sha256(b''.join(path.read_bytes() for path in dependencies)).hexdigest()
    evaluation_id = _digest({'request': request, 'result': result, 'code_sha256': code_sha256})
    existing = session.get(SignalBasketEvaluation, evaluation_id)
    if existing is None:
        existing = SignalBasketEvaluation(id=evaluation_id, basket_id=row.id, basket_revision=row.revision,
                                           request_json=_encode(request), result_json=_encode(result),
                                           code_sha256=code_sha256, created_at=utc_now())
        session.add(existing)
        session.flush()
        _audit(session, row, 'evaluate', {'evaluation_id': evaluation_id, 'basket_revision': row.revision,
                                        'as_of_date': as_of_date, 'summary': result['summary']})
    return _evaluation_view(existing)


def list_evaluations(session: Session, basket_id: str) -> list[dict]:
    _basket(session, basket_id, True)
    return [_evaluation_view(row, False) for row in session.scalars(select(SignalBasketEvaluation).where(
        SignalBasketEvaluation.basket_id == basket_id).order_by(SignalBasketEvaluation.created_at.desc(),
                                                               SignalBasketEvaluation.id).limit(100))]


def get_evaluation(session: Session, basket_id: str, evaluation_id: str) -> dict:
    _basket(session, basket_id, True)
    row = session.get(SignalBasketEvaluation, evaluation_id)
    if row is None or row.basket_id != basket_id:
        raise TradeError('BASKET_EVALUATION_NOT_FOUND', '篮子评估报告不存在', 404)
    return _evaluation_view(row)
