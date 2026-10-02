"""Revisioned admission policy for NEW user strategy runs.

Workers must execute their already frozen inputs without reading this policy.
Metadata/presets/history remain readable when admission is disabled.
"""
import hashlib
import json

from sqlalchemy import select
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.registry_models import StrategyRegistryRevision, StrategyRegistrySettings


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def _catalog():
    # Local import keeps the pure strategy catalog usable by runtime workers.
    from trade_app.research.service import strategy_catalog
    from trade_app.research.runtime import SINGLE_SYMBOL_STRATEGIES
    from trade_app.research.signal_context import context_catalog
    from trade_app.research.catalog_presentation import present_strategy
    context_ids = {row['id'] for row in context_catalog()}
    return [present_strategy(row, single_ids=SINGLE_SYMBOL_STRATEGIES, context_ids=context_ids)
            for row in strategy_catalog()]


def _defaults(catalog):
    enabled = sorted(row['id'] for row in catalog if row.get('enabled_by_default', row['enabled_in_legacy'])
                     and row['availability'] == 'available')
    default = next((row['id'] for row in catalog if row['is_legacy_default'] and row['id'] in enabled), None)
    return {'enabled_ids': enabled, 'default_strategy_id': default or next(iter(enabled), None)}


def _metadata(catalog):
    # Preview approval binds defaults and executable paths as well as schemas.
    keys = ('id', 'version', 'params_schema', 'calculation_version', 'capabilities', 'status',
            'default_params', 'signal_params', 'scanner_params', 'pool_params', 'execution_paths',
            'origin', 'enabled_by_default', 'enabled_in_legacy', 'is_legacy_default', 'source_sha256')
    return _digest([{key: row.get(key) for key in keys} for row in catalog])


def get_registry(session):
    catalog = _catalog()
    row = session.get(StrategyRegistrySettings, 'global')
    settings = json.loads(row.settings_json) if row else _defaults(catalog)
    return {'revision': row.revision if row else 0, 'updated_at': row.updated_at if row else None,
        'catalog_sha256': _metadata(catalog), **settings, 'defaults': _defaults(catalog),
        'strategies': [{**item, 'enabled_in_rebuild': item['id'] in settings['enabled_ids'],
            'default_in_rebuild': item['id'] == settings['default_strategy_id']} for item in catalog],
        'catalog_counts': {'total': len(catalog), 'legacy': sum(item['origin'] == 'final_trade' for item in catalog),
                           'classic': sum(item['origin'] == 'classic_reference' for item in catalog)},
        'scope': 'new_user_strategy_submissions',
        'notes': ['停用会拒绝新的研究判断及策略任务提交；已冻结任务继续按提交时输入执行。',
                  '历史、参数预设及原有观察信号仍可读取；重试、恢复、取消已有任务不改变冻结参数。',
                  '四步漏斗、趋势池、事件仓和原始矩阵/事件矩阵是独立数据功能，分别使用专用入口。',
                  '策略族只组织共享公式的变体，保留各自 ID、参数、评分与历史结果。',
                  '已有启用设置不会因目录扩充而自动开启新增策略；恢复默认须预览后确认。']}


def catalog_with_registry(session):
    return get_registry(session)['strategies']


def _normalize(body, catalog):
    if not isinstance(body, dict) or set(body) - {'enabled_ids', 'default_strategy_id', 'expected_revision', 'expected_preview_sha256'}:
        raise TradeError('INVALID_STRATEGY_REGISTRY', '策略设置包含不支持的字段')
    known = {row['id'] for row in catalog}
    ids = body.get('enabled_ids')
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids) or len(ids) != len(set(ids)) or set(ids) - known:
        raise TradeError('INVALID_STRATEGY_REGISTRY', '启用策略必须为目录中不重复的策略 ID')
    default = body.get('default_strategy_id')
    if ids and (not isinstance(default, str) or default not in ids) or not ids and default is not None:
        raise TradeError('INVALID_STRATEGY_DEFAULT', '默认策略必须已启用；全部停用时默认策略须为空')
    return {'enabled_ids': sorted(ids), 'default_strategy_id': default}


def preview_registry(session, body):
    if not isinstance(body, dict):
        raise TradeError('INVALID_STRATEGY_REGISTRY', '策略设置须为对象')
    current = get_registry(session)
    if type(body.get('expected_revision')) is not int or body['expected_revision'] != current['revision']:
        raise TradeError('STRATEGY_REGISTRY_CONFLICT', '策略设置版本已变化，请重新读取并预览', 409)
    proposed = _normalize(body, current['strategies'])
    before = {key: current[key] for key in ('enabled_ids', 'default_strategy_id')}
    diff = [{'strategy_id': row['id'], 'name': row['name'], 'before_enabled': row['id'] in before['enabled_ids'],
             'after_enabled': row['id'] in proposed['enabled_ids']} for row in current['strategies']
            if (row['id'] in before['enabled_ids']) != (row['id'] in proposed['enabled_ids'])]
    value = {'revision': current['revision'], 'catalog_sha256': current['catalog_sha256'],
        'before': before, 'after': proposed, 'diff': diff,
        'default_changed': before['default_strategy_id'] != proposed['default_strategy_id']}
    return {**value, 'preview_sha256': _digest(value), 'notes': current['notes']}


def apply_registry(session, body):
    preview = preview_registry(session, body)
    if body.get('expected_preview_sha256') != preview['preview_sha256']:
        raise TradeError('STRATEGY_REGISTRY_PREVIEW_CHANGED', '请先确认当前策略设置差异', 409)
    now = utc_now()
    row = session.get(StrategyRegistrySettings, 'global')
    revision = preview['revision'] + 1
    if row is None:
        row = StrategyRegistrySettings(id='global', revision=revision, settings_json=_encode(preview['after']), updated_at=now)
        session.add(row)
    else:
        row.revision, row.settings_json, row.updated_at = revision, _encode(preview['after']), now
    session.add(StrategyRegistryRevision(revision=revision, snapshot_json=_encode(preview), created_at=now))
    session.flush()
    return get_registry(session)


def registry_history(session):
    return [{'revision': row.revision, 'created_at': row.created_at, 'snapshot': json.loads(row.snapshot_json)}
        for row in session.scalars(select(StrategyRegistryRevision).order_by(StrategyRegistryRevision.revision.desc()).limit(100))]


def require_enabled(session, strategy_ids, *, expected_revision=None):
    """Use only in user submission boundaries, never in pure/frozen workers."""
    current = get_registry(session)
    known = {row['id'] for row in current['strategies']}
    if not isinstance(strategy_ids, (list, tuple)) or any(not isinstance(item, str) or item not in known for item in strategy_ids):
        raise TradeError('STRATEGY_REGISTRY_UNKNOWN', '所选策略不在当前注册目录中', 409)
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision != current['revision']):
        raise TradeError('STRATEGY_REGISTRY_CONFLICT', '预览后策略启用设置已变化，请重新提交', 409)
    disabled = sorted(set(strategy_ids) - set(current['enabled_ids']))
    if disabled:
        raise TradeError('STRATEGY_DISABLED', '以下策略已停用，不能新建执行任务：' + '、'.join(disabled), 409)
    return {'revision': current['revision'], 'catalog_sha256': current['catalog_sha256'],
            'strategy_ids': sorted(set(strategy_ids)), 'scope': 'new_user_strategy_submissions'}
