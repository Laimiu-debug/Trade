"""Explicit simulation equity reports and version-bound asset-percent sizing."""
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.market.symbols import market_symbol_key
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, decimal_value, money_minor, money_text, utc_now
from trade_app.research.drafts import create_draft, size_draft
from trade_app.research.draft_models import SimOrderDraft
from trade_app.research.scan_job_worker import digest, encode
from trade_app.research.sim_equity_domain import VERSION, calculate_equity, replay_ledger
from trade_app.research.sim_equity_models import SimEquityDraftEvidence, SimEquityReport
from trade_app.trading.sim_models import SimFill, SimLot, SimOrder
from trade_app.trading.simulation import sim_account

MAX_SYMBOLS = 64
MAX_BARS = 60_000
MAX_FILLS = 50_000
MAX_BYTES = 24 * 1024 * 1024


def _day(value):
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError(value)
        return value
    except (ValueError, TypeError) as exc:
        raise TradeError('INVALID_EQUITY_DATE', '日期须为 YYYY-MM-DD') from exc


def _code_hash():
    root = Path(__file__).resolve().parents[1]
    paths = ('research/sim_equity_domain.py', 'research/sim_equity_service.py',
             'market/symbols.py', 'platform/symbols.py', 'platform/types.py')
    return hashlib.sha256(b''.join((root / path).read_bytes() for path in paths)).hexdigest()


def _initial_date(session, account_id, wallet):
    creates = list(session.scalars(select(AuditEvent).where(AuditEvent.account_id == account_id,
        AuditEvent.entity_type == 'sim_account', AuditEvent.entity_id == account_id,
        AuditEvent.operation == 'create').order_by(AuditEvent.created_at, AuditEvent.id)))
    initial_date = None
    for row in creates:
        after = json.loads(row.after_json or '{}')
        if after.get('start_date'):
            initial_date = _day(after['start_date'])
            if money_minor(after['initial_capital'], '初始资金') != wallet.initial_minor:
                raise TradeError('SIM_EQUITY_INITIAL_CONFLICT', '初始资金审计与模拟钱包不一致', 409)
            break
    if initial_date is None:
        raise TradeError('SIM_EQUITY_INITIAL_DATE_UNKNOWN', '该旧模拟账户缺少创建审计中的初始日期，无法可信重放完整资金历史；不会猜测起始日', 409)
    return initial_date


def account_snapshot(session: Session, account_id: str) -> dict:
    account, wallet = sim_account(session, account_id)
    initial_date = _initial_date(session, account_id, wallet)
    fills = []
    for fill, order in session.execute(select(SimFill, SimOrder).join(SimOrder, SimOrder.id == SimFill.order_id).where(
            SimFill.account_id == account_id, SimOrder.account_id == account_id).order_by(
                SimFill.fill_date, SimFill.created_at, SimFill.id).limit(MAX_FILLS + 1)):
        if not initial_date <= fill.fill_date <= wallet.as_of_date:
            raise TradeError('SIM_EQUITY_FILL_DATE_CONFLICT', '成交日期超出账户初始日或模拟时钟，不能静默忽略', 409)
        fills.append({'id': fill.id, 'order_id': order.id, 'symbol': order.symbol,
            'symbol_key': market_symbol_key(order.symbol), 'side': order.side, 'quantity': order.quantity,
            'fill_date': fill.fill_date, 'price_units': fill.price_units, 'gross_minor': fill.gross_minor,
            'commission_minor': fill.commission_minor, 'stamp_minor': fill.stamp_minor,
            'transfer_minor': fill.transfer_minor, 'created_at': fill.created_at})
    if len(fills) > MAX_FILLS:
        raise TradeError('SIM_EQUITY_FILL_LIMIT', f'单次最多重放 {MAX_FILLS} 笔成交')
    symbols = sorted({row['symbol_key'] for row in fills})
    if len(symbols) > MAX_SYMBOLS:
        raise TradeError('SIM_EQUITY_SYMBOL_LIMIT', f'账户成交涉及超过 {MAX_SYMBOLS} 个证券，超出当前完整重放预算')
    cash, quantities = replay_ledger(wallet.initial_minor, fills)
    lots = {}
    for lot in session.scalars(select(SimLot).where(SimLot.account_id == account_id, SimLot.remaining_qty > 0)):
        key = market_symbol_key(lot.symbol)
        lots[key] = lots.get(key, 0) + lot.remaining_qty
    if cash != wallet.cash_minor or quantities != lots:
        raise TradeError('SIM_EQUITY_LEDGER_MISMATCH', '成交重放与当前钱包现金或持仓数量不一致，需先核对来源', 409)
    pending = [{'id': row.id, 'revision': row.revision, 'symbol': row.symbol, 'side': row.side,
                'reserve_minor': row.reserve_minor, 'quantity': row.quantity}
               for row in session.scalars(select(SimOrder).where(SimOrder.account_id == account_id,
                    SimOrder.status == 'pending').order_by(SimOrder.id))]
    return {'account_id': account_id, 'account_name': account.name,
            'initial_date': initial_date, 'initial_date_source': 'sim_account_create_audit',
            'initial_minor': wallet.initial_minor, 'cash_minor': wallet.cash_minor,
            'wallet_date': wallet.as_of_date, 'wallet_revision': wallet.revision,
            'config_version': wallet.config_version, 'config': json.loads(wallet.config_json),
            'frozen': bool(wallet.frozen), 'fills': fills, 'current_quantities': quantities,
            'pending_orders': pending, 'symbols': symbols}


def inputs(session: Session, account_id: str) -> dict:
    account, wallet = sim_account(session, account_id)
    symbols = sorted({market_symbol_key(symbol) for symbol in session.scalars(select(SimOrder.symbol).where(
        SimOrder.account_id == account_id, SimOrder.status == 'filled').distinct())})
    current = {}
    for lot in session.scalars(select(SimLot).where(SimLot.account_id == account_id, SimLot.remaining_qty > 0)):
        key = market_symbol_key(lot.symbol)
        current[key] = current.get(key, 0) + lot.remaining_qty
    return {'account_id': account_id, 'account_name': account.name,
            'initial_date': _initial_date(session, account_id, wallet), 'initial_date_source': 'sim_account_create_audit',
            'wallet_date': wallet.as_of_date, 'wallet_revision': wallet.revision,
            'config_version': wallet.config_version, 'frozen': bool(wallet.frozen),
            'current_quantities': current, 'symbols': symbols,
            'initial_capital': money_text(wallet.initial_minor),
            'fill_count': session.scalar(select(func.count()).select_from(SimFill).where(SimFill.account_id == account_id)),
            'limits': {'symbols': MAX_SYMBOLS, 'bars': MAX_BARS, 'calendar_days': 366, 'fills': MAX_FILLS}}


def prepare_report(session: Session, data_dir: Path, account_id: str, body: dict) -> dict:
    if set(body) - {'date_from', 'date_to', 'dataset_ids', 'strict'}:
        raise TradeError('INVALID_EQUITY_REQUEST', '资产报告包含不支持的字段')
    start, end = _day(body.get('date_from')), _day(body.get('date_to'))
    if not 0 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 365:
        raise TradeError('SIM_EQUITY_DATE_LIMIT', '日期区间须有序且最多 366 个自然日')
    ids = body.get('dataset_ids', [])
    if not isinstance(ids, list) or len(ids) > MAX_SYMBOLS or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in ids) or len(ids) != len(set(ids)):
        raise TradeError('INVALID_EQUITY_DATASETS', '行情样本须为最多 64 个不重复 ID')
    strict = body.get('strict', True)
    if type(strict) is not bool:
        raise TradeError('INVALID_EQUITY_STRICT', 'strict 须为布尔值')
    account = account_snapshot(session, account_id)
    if start < account['initial_date'] or end > account['wallet_date']:
        raise TradeError('SIM_EQUITY_CLOCK_RANGE', '资产曲线范围须在初始日与当前模拟日期之间')
    datasets, seen, bar_count = [], set(), 0
    for dataset_id in sorted(ids):
        dataset = get_dataset(session, data_dir, dataset_id)
        key = market_symbol_key(dataset['symbol'])
        if key in seen:
            raise TradeError('SIM_EQUITY_DUPLICATE_SYMBOL', '同一证券及其别名只能选择一个行情样本')
        if key not in account['symbols']:
            raise TradeError('SIM_EQUITY_UNRELATED_SYMBOL', '所选行情不属于该账户曾成交的证券')
        if dataset['adjustment'] != 'none':
            raise TradeError('SIM_EQUITY_ADJUSTMENT', '实际持仓资产估值只接受不复权价格，不能与复权价格混用')
        seen.add(key)
        bar_count += len(dataset['bars'])
        if bar_count > MAX_BARS:
            raise TradeError('SIM_EQUITY_BAR_LIMIT', f'所选样本超过 {MAX_BARS} 根日线预算')
        datasets.append({**dataset, 'symbol_key': key})
    frozen = {**account, 'date_from': start, 'date_to': end, 'strict': strict}
    references = [{key: dataset[key] for key in ('id', 'symbol', 'symbol_key', 'provider', 'adjustment', 'first_date', 'last_date', 'availability_quality')} for dataset in datasets]
    request = {'account': account, 'date_from': start, 'date_to': end, 'strict': strict,
               'datasets': references, 'code_sha256': _code_hash(), 'calculation_version': VERSION}
    result = calculate_equity(frozen, datasets)
    if len(encode(result).encode()) > MAX_BYTES:
        raise TradeError('SIM_EQUITY_RESULT_LIMIT', '资产报告超过 24 MiB 保存预算')
    return {'id': digest({'request': request, 'result': result}), 'account_id': account_id,
            'request': request, 'result': result, 'input_sha256': digest(request)}


def _verify_wallet(session, account_id, request):
    _account, wallet = sim_account(session, account_id)
    frozen = request['account']
    if wallet.revision != frozen['wallet_revision'] or wallet.config_version != frozen['config_version'] or wallet.as_of_date != frozen['wallet_date']:
        raise TradeError('SIM_EQUITY_PREVIEW_STALE', '模拟钱包、时钟或费用已变化，请重新生成估值预览', 409)


def save_report(session: Session, account_id: str, prepared: dict) -> dict:
    if prepared['account_id'] != account_id:
        raise TradeError('SIM_EQUITY_ACCOUNT_SCOPE', '估值不属于当前模拟账户', 404)
    _verify_wallet(session, account_id, prepared['request'])
    row = session.get(SimEquityReport, prepared['id'])
    if row is None:
        row = SimEquityReport(id=prepared['id'], account_id=account_id,
            request_json=encode(prepared['request']), result_json=encode(prepared['result']), created_at=utc_now())
        session.add(row)
        session.flush()
    return _view(row)


def _view(row, detail=True):
    request, result = json.loads(row.request_json), json.loads(row.result_json)
    if digest({'request': request, 'result': result}) != row.id:
        raise TradeError('SIM_EQUITY_REPORT_CORRUPT', '已保存资产报告摘要不一致', 409)
    return {'id': row.id, 'account_id': row.account_id, 'created_at': row.created_at,
            'input_sha256': digest(request), 'date_from': request['date_from'], 'date_to': request['date_to'],
            'wallet_revision': request['account']['wallet_revision'], 'summary': result['summary'],
            **({'request': request, 'result': result} if detail else {})}


def get_report(session, account_id, report_id):
    sim_account(session, account_id)
    row = session.get(SimEquityReport, report_id)
    if row is None or row.account_id != account_id:
        raise TradeError('SIM_EQUITY_NOT_FOUND', '该模拟账户没有此资产报告', 404)
    return _view(row)


def list_reports(session, account_id):
    sim_account(session, account_id)
    # History reads project compact metadata in SQLite; never deserialize up to
    # 100 complete curves. Detail reads still verify the immutable content hash.
    rows = session.execute(select(SimEquityReport.id, SimEquityReport.created_at,
        func.json_extract(SimEquityReport.request_json, '$.date_from'),
        func.json_extract(SimEquityReport.request_json, '$.date_to'),
        func.json_extract(SimEquityReport.request_json, '$.account.wallet_revision'),
        func.json_extract(SimEquityReport.result_json, '$.summary')).where(
            SimEquityReport.account_id == account_id).order_by(
                SimEquityReport.created_at.desc(), SimEquityReport.id).limit(100))
    return [{'id': row[0], 'account_id': account_id, 'created_at': row[1],
             'date_from': row[2], 'date_to': row[3], 'wallet_revision': row[4],
             'summary': json.loads(row[5])} for row in rows]


def delete_report(session, account_id, report_id):
    get_report(session, account_id, report_id)
    session.delete(session.get(SimEquityReport, report_id))
    session.flush()
    return {'id': report_id, 'deleted': True, 'fills_and_drafts_preserved': True}


def size_from_assets(session: Session, account_id: str, body: dict) -> dict:
    if set(body) != {'report_id', 'percent', 'limit_price'}:
        raise TradeError('INVALID_EQUITY_SIZING', '总资产比例换算包含不支持的字段')
    report_id = body.get('report_id')
    if not isinstance(report_id, str) or len(report_id) != 64:
        raise TradeError('INVALID_EQUITY_SIZING', '请选择已保存资产报告')
    report = get_report(session, account_id, report_id)
    _verify_wallet(session, account_id, report['request'])
    _account, wallet = sim_account(session, account_id, mutable=True)
    endpoint = report['result']['points'][-1]
    if report['date_to'] != wallet.as_of_date or endpoint['date'] != wallet.as_of_date:
        raise TradeError('SIM_EQUITY_SIZING_DATE', '总资产分母须为当前模拟日期的已保存估值', 409)
    if endpoint['quality'] != 'complete' or endpoint['total_assets_minor'] is None:
        raise TradeError('SIM_EQUITY_SIZING_INCOMPLETE', '持仓缺价、旧报价或可得时间未知，不能作为当前总资产分母', 409)
    percent = decimal_value(body.get('percent'), '总资产比例')
    if not Decimal(0) < percent <= Decimal(100):
        raise TradeError('INVALID_EQUITY_SIZING', '总资产比例须大于 0 且不超过 100')
    budget = int(Decimal(endpoint['total_assets_minor']) * percent / 100)
    if budget < 1:
        raise TradeError('INVALID_EQUITY_SIZING', '换算预算不足 0.01 元')
    quote = size_draft(session, account_id, {'mode': 'amount', 'value': money_text(budget), 'limit_price': body['limit_price']})
    result = {**quote, 'mode': 'asset_percent', 'percent': str(percent), 'report_id': report_id,
        'valuation_input_sha256': report['input_sha256'], 'valuation_date': endpoint['date'],
        'denominator_assets': endpoint['total_assets'], 'denominator_basis': 'cash_plus_fresh_held_market_value',
        'requested_budget': money_text(budget),
        'note': '本次买入预算占当前完整总资产比例，预算含预估费用；按实际可支配现金封顶并向下取整手，不代表调整至目标持仓比例。保存草稿时再次核对钱包及估值版本。'}
    return {**result, 'quote_sha256': digest(result)}


def create_asset_sized_draft(session: Session, account_id: str, body: dict) -> dict:
    if set(body) != {'report_id', 'percent', 'limit_price', 'source_run_id', 'expected_quote_sha256'}:
        raise TradeError('INVALID_EQUITY_SIZING', '请提交完整的总资产比例估算证据')
    if not isinstance(body['source_run_id'], str) or not 1 <= len(body['source_run_id']) <= 128 or not isinstance(body['expected_quote_sha256'], str) or len(body['expected_quote_sha256']) != 64:
        raise TradeError('INVALID_EQUITY_SIZING', '研究运行 ID 或估算摘要格式不正确')
    quote = size_from_assets(session, account_id, {key: body[key] for key in ('report_id', 'percent', 'limit_price')})
    if quote['quote_sha256'] != body['expected_quote_sha256']:
        raise TradeError('SIM_EQUITY_SIZE_STALE', '数量、账户或费用版本发生变化，请重新估算', 409)
    if not quote['can_create'] or quote['quantity'] <= 0:
        raise TradeError('SIM_EQUITY_SIZE_INSUFFICIENT', '可用资金或比例预算不足以创建整手草稿', 409)
    existing = session.scalar(select(SimOrderDraft).where(SimOrderDraft.account_id == account_id,
        SimOrderDraft.source_run_id == body['source_run_id'], SimOrderDraft.status == 'draft'))
    from trade_app.platform.types import price_units
    if existing is not None and (existing.quantity != quote['quantity'] or existing.limit_price_units != price_units(body['limit_price'])):
        raise TradeError('SIM_EQUITY_DRAFT_EXISTS', '该信号已有不同数量或价格的草稿，请在模拟账户显式编辑；未覆盖原草稿', 409)
    draft = create_draft(session, account_id, {'source_run_id': body['source_run_id'],
        'quantity': quote['quantity'], 'limit_price': body['limit_price']})
    evidence = {'draft_id': draft['id'], 'draft_revision': draft['revision'], 'quote': quote,
                'source_run_id': body['source_run_id']}
    evidence_id = digest(evidence)
    if session.get(SimEquityDraftEvidence, evidence_id) is None:
        session.add(SimEquityDraftEvidence(id=evidence_id, account_id=account_id, draft_id=draft['id'],
            report_id=body['report_id'], evidence_json=encode(evidence), created_at=utc_now()))
        session.flush()
    return {'draft': draft, 'sizing': quote, 'evidence_id': evidence_id}
