from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, decimal_value, utc_now
from trade_app.trading.service import account_or_error, mark_changed
from trade_app.trading.target_models import TargetConfig


DEFAULT_TARGET_CONFIG = {'version': 1, 'multiplier': '1.30', 'node_count': 50}


def target_config(session: Session, account_id: str) -> dict:
    account_or_error(session, account_id, real=True)
    latest = session.scalar(select(TargetConfig).where(TargetConfig.account_id == account_id)
                            .order_by(TargetConfig.version.desc()).limit(1))
    if latest is None:
        return dict(DEFAULT_TARGET_CONFIG)
    return {'version': latest.version, 'multiplier': latest.multiplier,
            'node_count': latest.node_count}


def update_target_config(session: Session, account_id: str, payload: dict) -> dict:
    account = account_or_error(session, account_id, real=True)
    before = target_config(session, account_id)
    if payload['expected_version'] != before['version']:
        raise TradeError('REVISION_CONFLICT', '目标节点配置已变化，请刷新后核对', 409)
    multiplier = decimal_value(payload['multiplier'], '目标倍率')
    if multiplier < Decimal('1.01') or multiplier > Decimal('10'):
        raise TradeError('INVALID_TARGET_MULTIPLIER', '目标倍率必须在 1.01 至 10 之间')
    count = payload['node_count']
    if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > 100:
        raise TradeError('INVALID_TARGET_COUNT', '节点数量必须在 1 至 100 之间')
    if multiplier ** count > Decimal('1000000000000'):
        raise TradeError('TARGET_RANGE_TOO_LARGE', '最高级节点倍率不能超过一万亿')
    config = {'version': before['version'] + 1, 'multiplier': format(multiplier, 'f'),
              'node_count': count}
    session.add(TargetConfig(account_id=account_id, **config, created_at=utc_now()))
    mark_changed(session, account, '0001-01-01', 'target_config_update',
                 'target_config', f"{account_id}:{config['version']}", before, config)
    return config
