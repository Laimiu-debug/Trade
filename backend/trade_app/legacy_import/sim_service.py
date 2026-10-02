"""Explicit one-time reconstruction into a new simulated account, never the ledger."""
import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from trade_app.legacy_import.reader import canonical, digest
from trade_app.legacy_import.service import _row
from trade_app.legacy_import.sim_models import LegacySimPromotion
from trade_app.legacy_import.sim_normalizer import normalize
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, money_text, new_id, utc_now
from trade_app.trading.models import Account
from trade_app.trading.sim_models import SimWallet, SimOrder, SimFill, SimLot


def preview(session: Session, import_id: str, body: dict):
    row = _row(session, import_id)
    if row.source_kind != 'final_sim_json':
        raise TradeError('LEGACY_SIM_SOURCE_REQUIRED', '仅 final-trade sim_state.json 可核对模拟账户迁入')
    previous = session.get(LegacySimPromotion, import_id)
    if previous:
        frozen = json.loads(previous.preview_json)
        if body.get('expected_preview_sha256') == previous.preview_sha256 and body.get('expected_revision') == frozen['expected_revision'] and body.get('account_name', '').strip() == frozen['account_name']:
            return frozen
        raise TradeError('LEGACY_SIM_ALREADY_PROMOTED', '此模拟档案已迁入，不重复建立账户', 409)
    if row.revision != body['expected_revision']:
        raise TradeError('LEGACY_REVISION_CONFLICT', '档案版本已变化，请重新预览', 409)
    name = body['account_name'].strip()
    if not name or len(name) > 80:
        raise TradeError('LEGACY_ACCOUNT_NAME_REQUIRED', '新模拟账户名称须为 1–80 字符')
    payload = json.loads(row.archive_json)
    if digest(payload) != row.logical_sha256:
        raise TradeError('LEGACY_ARCHIVE_HASH_MISMATCH', '已保存逻辑档案摘要校验失败', 409)
    value = {**normalize(payload), 'import_id': import_id, 'expected_revision': row.revision,
             'source_sha256': row.source_sha256, 'logical_sha256': row.logical_sha256, 'account_name': name}
    value['preview_sha256'] = digest(value)
    return value


def apply(session: Session, import_id: str, body: dict):
    if not body.get('acknowledge_limitations'):
        raise TradeError('LEGACY_ACK_REQUIRED', '请核对缺失历史信息和逐笔核验结果后确认')
    frozen = preview(session, import_id, body)
    if frozen['preview_sha256'] != body['expected_preview_sha256']:
        raise TradeError('LEGACY_PREVIEW_CHANGED', '名称、档案或校验版本已变化，请重新预览', 409)
    previous = session.get(LegacySimPromotion, import_id)
    if previous:
        return {'import_id': import_id, 'account_id': previous.account_id, 'preview_sha256': previous.preview_sha256}
    if not frozen['can_import']:
        raise TradeError('LEGACY_SIM_VALIDATION_FAILED', '模拟账本存在未解决的核验问题；不会创建账户')
    source = _row(session, import_id)
    values = frozen['records']
    account_id, now = new_id(), utc_now()
    session.add(Account(id=account_id, name=frozen['account_name'], kind='sim', currency='CNY', input_revision=1, created_at=now))
    session.flush()
    session.add(SimWallet(account_id=account_id, initial_minor=values['initial_minor'], cash_minor=values['cash_minor'],
                          as_of_date=values['as_of_date'], config_json=canonical(values['config']), config_version=1,
                          revision=1, frozen=0, reset_group_id=account_id))
    order_ids = {item['source_id']: new_id() for item in values['fills']}
    fill_ids = {identity: new_id() for identity in order_ids}
    lot_ids = {item['source_buy_order_id']: new_id() for item in values['lots']}
    mappings = []
    # Current order/fee semantics are not retroactively invented. The actual
    # historical fill and its fee components remain the accounting authority.
    for index, item in enumerate(values['fills']):
        identity = item['source_id']
        stamp = (datetime.fromisoformat(now) + timedelta(microseconds=index)).isoformat()
        origin = {'import_id': import_id, 'source_order_id': identity, 'source_sha256': source.source_sha256,
                  'raw_symbol': item['raw_symbol'], 'price_is_fill_reference': True, 'historical_config_unknown': True,
                  'source_price_kind': item['price_source'], 'estimated_price': item['estimated_price'],
                  'status_reason': item['status_reason'], 'warning': item['warning']}
        session.add(SimOrder(id=order_ids[identity], account_id=account_id, symbol=item['symbol'].upper(), side=item['side'],
                            quantity=item['quantity'], limit_price_units=item['price_units'], signal_date=item['signal_date'],
                            submit_date=item['submit_date'], status='filled', reserve_minor=0, config_json='{}', config_version=0,
                            revision=1, created_at=stamp, updated_at=stamp, legacy_origin_json=canonical(origin)))
        session.flush()
        allocations = None if item['allocations'] is None else [
            {'lot_id': lot_ids[part['source_buy_order_id']], 'buy_fill_id': fill_ids[part['source_buy_order_id']],
             'buy_date': part['buy_date'], 'quantity': part['quantity'], 'cost_basis': money_text(part['cost_minor']),
             'sell_gross': money_text(part['sell_gross_minor']), 'sell_fees': money_text(part['sell_fees_minor']),
             'realized_pnl': money_text(part['pnl_minor'])} for part in item['allocations']]
        session.add(SimFill(id=fill_ids[identity], order_id=order_ids[identity], account_id=account_id,
                           fill_date=item['fill_date'], price_units=item['price_units'], gross_minor=item['gross_minor'],
                           commission_minor=item['commission_minor'], stamp_minor=item['stamp_minor'], transfer_minor=item['transfer_minor'],
                           realized_pnl_minor=item['realized_pnl_minor'], price_source='legacy:' + item['price_source'],
                           allocations_json=canonical(allocations) if allocations is not None else None, created_at=stamp))
        mappings.append({'source_order_id': identity, 'order_id': order_ids[identity], 'fill_id': fill_ids[identity]})
    session.flush()
    for index, item in enumerate(values['lots']):
        stamp = (datetime.fromisoformat(now) + timedelta(microseconds=index)).isoformat()
        identity = item['source_buy_order_id']
        session.add(SimLot(id=lot_ids[identity], account_id=account_id, symbol=item['symbol'].upper(), acquired_date=item['acquired_date'],
                          quantity=item['quantity'], remaining_qty=item['remaining_qty'], cost_minor=item['cost_minor'],
                          buy_fill_id=fill_ids[identity], created_at=stamp))
        mappings.append({'source_buy_order_id': identity, 'source_lot_id': item.get('source_lot_id'), 'lot_id': lot_ids[identity]})
    session.add(LegacySimPromotion(import_id=import_id, account_id=account_id, preview_sha256=frozen['preview_sha256'],
                                   preview_json=canonical(frozen), mappings_json=canonical(mappings), created_at=now))
    source.revision += 1
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='legacy_sim_import', entity_id=import_id,
                           operation='create', before_json=None,
                           after_json=canonical({'account_id': account_id, 'source_sha256': source.source_sha256,
                                                 'preview_sha256': frozen['preview_sha256'], 'initial_date': None,
                                                 'summary': frozen['summary'], 'notes': frozen['notes']}), created_at=now))
    session.flush()
    return {'import_id': import_id, 'account_id': account_id, 'preview_sha256': frozen['preview_sha256']}
