"""Versioned real-account fee settings using the shared execution fee rules."""
from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy.orm import Session

from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, decimal_value, money_minor, money_text, new_id, price_units, utc_now
from trade_app.trading.domain import DEFAULT_FEE_CONFIG, FeeRule, calculate_fees
from trade_app.trading.models import Account, RealFeeConfig


def _account(session: Session, account_id: str) -> Account:
    account = session.get(Account, account_id)
    if account is None or account.kind != 'real':
        raise TradeError('ACCOUNT_NOT_FOUND', '实盘账户不存在', 404)
    return account


def fee_settings(session: Session, account_id: str) -> dict:
    _account(session, account_id)
    row = session.get(RealFeeConfig, account_id)
    return {'config': json.loads(row.config_json) if row else dict(DEFAULT_FEE_CONFIG),
            'version': row.version if row else 1}


def normalize_fee_config(raw: dict) -> dict[str, str]:
    if set(raw) != set(DEFAULT_FEE_CONFIG):
        raise TradeError('INVALID_FEE_CONFIG', '费用规则字段不完整')
    normalized = {}
    for key in ('commission_rate', 'sell_stamp_rate', 'transfer_rate'):
        value = decimal_value(raw[key], key)
        if value < 0 or value > Decimal('0.01'):
            raise TradeError('INVALID_FEE_CONFIG', f'{key} 必须位于 0 至 1%')
        normalized[key] = format(value, 'f')
    normalized['minimum_commission'] = money_text(money_minor(
        raw['minimum_commission'], '最低佣金', allow_zero=True))
    return normalized


def update_fee_settings(session: Session, account_id: str, payload: dict) -> dict:
    account = _account(session, account_id)
    current = fee_settings(session, account_id)
    if payload['expected_version'] != current['version']:
        raise TradeError('REVISION_CONFLICT', '费用规则已被修改，请刷新后核对', 409)
    config = normalize_fee_config(payload['config'])
    row = session.get(RealFeeConfig, account_id)
    if row:
        row.config_json = json.dumps(config, sort_keys=True)
        row.version += 1
        row.updated_at = utc_now()
    else:
        row = RealFeeConfig(account_id=account_id, config_json=json.dumps(config, sort_keys=True),
                            version=2, updated_at=utc_now())
        session.add(row)
    result = {'config': config, 'version': row.version}
    session.add(AuditEvent(id=new_id(), account_id=account.id, entity_type='real_fee_config',
                           entity_id=account.id, operation='update',
                           before_json=json.dumps(current, ensure_ascii=False),
                           after_json=json.dumps(result, ensure_ascii=False), created_at=utc_now()))
    return result


def resolve_trade_fee(session: Session, account_id: str, payload: dict) -> dict:
    """Store both calculated fees and the exact amount used in the ledger."""
    mode = payload.get('fee_mode', 'manual')
    if mode == 'snapshot':
        snapshot = payload['fee_snapshot']
        return dict(fee_minor=snapshot['fee_minor'],
                    calculated_fee_minor=snapshot['calculated_fee_minor'],
                    fee_source=snapshot['fee_source'],
                    fee_rule_version=snapshot['fee_rule_version'],
                    fee_breakdown_json=snapshot['fee_breakdown_json'])
    settings = fee_settings(session, account_id)
    config = settings['config']
    rule = FeeRule(commission_rate=Decimal(config['commission_rate']),
                   minimum_commission=Decimal(config['minimum_commission']),
                   sell_stamp_rate=Decimal(config['sell_stamp_rate']),
                   transfer_rate=Decimal(config['transfer_rate']))
    price = Decimal(price_units(payload['price'])) / Decimal(10_000)
    fees = calculate_fees(price, payload['quantity'], payload['side'], rule)
    calculated = money_minor(str(fees.total), '计算费用', allow_zero=True)
    if mode == 'auto':
        charged = calculated
    elif mode == 'manual':
        charged = money_minor(payload.get('fee', '0'), '实付费用', allow_zero=True)
    else:
        raise TradeError('INVALID_FEE_MODE', '费用方式无效')
    breakdown = {'commission': str(fees.commission), 'stamp': str(fees.stamp),
                 'transfer': str(fees.transfer), 'rule': config}
    return dict(fee_minor=charged, calculated_fee_minor=calculated,
                fee_source=mode, fee_rule_version=settings['version'],
                fee_breakdown_json=json.dumps(breakdown, sort_keys=True))


def fee_preview(session: Session, account_id: str, payload: dict) -> dict:
    resolved = resolve_trade_fee(session, account_id, payload)
    return {'fee': money_text(resolved['fee_minor']),
            'calculated_fee': money_text(resolved['calculated_fee_minor']),
            'fee_source': resolved['fee_source'],
            'fee_rule_version': resolved['fee_rule_version'],
            'breakdown': json.loads(resolved['fee_breakdown_json'])}
