"""Application-level composition of existing, individually versioned settings.

Cross-domain coordination belongs here rather than introducing dependencies from
platform into business domains. Defaults never reset unrelated fields or facts.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json

from sqlalchemy import select
from trade_app.ai import config as ai
from trade_app.market.calendar_models import LocalTradingCalendar
from trade_app.market.calendar_service import get_calendar, save_calendar
from trade_app.platform.models import AuditEvent
from trade_app.platform.settings_models import ApplicationSetting
from trade_app.platform.types import TradeError, decimal_value, new_id, utc_now
from trade_app.trading.domain import DEFAULT_FEE_CONFIG
from trade_app.trading.real_fees import fee_settings, normalize_fee_config, update_fee_settings
from trade_app.trading.service import account_or_error
from trade_app.trading import simulation
from trade_app.trading.targets import DEFAULT_TARGET_CONFIG, target_config, update_target_config
from trade_app.reviews import print_settings


MARKET_DEFAULTS = {'provider': 'auto', 'provider_order': ['baostock', 'akshare']}
CALENDAR_DEFAULTS = {'source': None, 'start_date': None, 'end_date': None, 'days': []}
GROUPS = {'fees', 'targets', 'ai', 'market_sources', 'calendar', 'print'}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _account(session, group, account_id):
    if group not in GROUPS:
        raise TradeError('SETTINGS_GROUP_NOT_FOUND', '该设置组不存在', 404)
    if group in {'fees', 'targets'}:
        if not account_id:
            raise TradeError('SETTINGS_ACCOUNT_REQUIRED', '请选择此设置所属账户')
        return account_or_error(session, account_id, real=group == 'targets')
    if account_id is not None:
        raise TradeError('SETTINGS_SCOPE_MISMATCH', '全局设置不接受账户范围，不能将其误认为账户私有设置')
    return None


def get_group(session, group, account_id=None):
    account = _account(session, group, account_id)
    notes, extra = [], {}
    if group == 'fees':
        if account.kind == 'real':
            current = fee_settings(session, account_id)
        else:
            _, wallet = simulation.sim_account(session, account_id)
            current = {'version': wallet.config_version, 'config': json.loads(wallet.config_json)}
            extra['readonly'] = bool(wallet.frozen)
        revision = current['version']
        value = {key: current['config'][key] for key in DEFAULT_FEE_CONFIG}
        notes = ['只影响此账户后续新增或修改的交易/委托；已保存费用与旧委托快照不重写。',
                 '模拟账户现金缓冲、滑点及资金保持原值。']
    elif group == 'targets':
        current = target_config(session, account_id)
        revision, value = current['version'], {key: current[key] for key in ('multiplier', 'node_count')}
        notes = ['保存或恢复默认将以新版本重新计算此实盘账户历史目标节点。']
    elif group == 'ai':
        current = ai.get_config(session)
        revision = current['revision']
        value = {key: current[key] for key in ai.DEFAULT_CONFIG}
        for channel in ('text', 'vision'):
            value[channel] = {key: value[channel][key] for key in ('base_url', 'model', 'secret_ref')}
        extra['credential_status'] = {channel: current[channel]['secret_configured'] for channel in ('text', 'vision')}
        notes = ['当前数据目录内全账户共享；只存环境变量名称，不读取或改写密钥值。',
                 '恢复默认清空模型地址与名称，不删除会话、运行或模型环境变量。保存不发起网络请求。']
    elif group == 'print':
        current = print_settings.get_settings(session)
        revision, value = current['revision'], {key: current[key] for key in print_settings.DEFAULTS}
        notes = ['署名用于后续日周月复盘、浏览器打印及统计导出，旧导出文件不重写。',
                 '导出目录仅在本机导出工具明确执行时使用；网页下载仍由浏览器选择保存位置。设置目录不会创建文件。']
    elif group == 'calendar':
        current = get_calendar(session)
        revision, value = current['revision'], {key: current[key] for key in CALENDAR_DEFAULTS}
        notes = ['当前数据目录内共享 CN_A 本地日历。恢复默认后后续交易日推荐为未知；已保存计划日期保持不变。']
    else:
        row = session.get(ApplicationSetting, group)
        revision, value = (row.revision, json.loads(row.value_json)) if row else (0, deepcopy(MARKET_DEFAULTS))
        notes = ['当前数据目录内全局共享，完整备份包含此配置。',
                 '行情页进入时读取默认；表单临时选源仅用于本次请求，已提交任务保留原来源顺序。']
    return {'group': group, 'scope': 'account' if account else 'global_data_directory',
            'account_id': account_id, 'account_name': account.name if account else None,
            'account_kind': account.kind if account else None, 'revision': revision, 'value': value,
            'notes': notes, 'readonly': False, **extra}


def default_value(group):
    if group == 'fees': return deepcopy(DEFAULT_FEE_CONFIG)
    if group == 'targets': return {key: DEFAULT_TARGET_CONFIG[key] for key in ('multiplier', 'node_count')}
    if group == 'ai': return deepcopy(ai.DEFAULT_CONFIG)
    if group == 'calendar': return deepcopy(CALENDAR_DEFAULTS)
    if group == 'market_sources': return deepcopy(MARKET_DEFAULTS)
    if group == 'print': return deepcopy(print_settings.DEFAULTS)
    raise TradeError('SETTINGS_GROUP_NOT_FOUND', '该设置组不存在', 404)


def resolve_market_sources(session, body, explicitly_provided):
    """Freeze current defaults only for omitted fields; job retries keep this body."""
    result = deepcopy(body)
    missing = sorted({'provider', 'provider_order'} - set(explicitly_provided))
    if missing:
        current = get_group(session, 'market_sources')
        for field in missing:
            result[field] = deepcopy(current['value'][field])
        result['source_defaults'] = {'revision': current['revision'], 'fields': missing}
    return result


def normalize(group, value, *, allow_calendar_empty=False):
    if not isinstance(value, dict):
        raise TradeError('INVALID_SETTINGS', '设置须为对象')
    if group == 'fees': return normalize_fee_config(value)
    if group == 'ai': return ai.normalize_config(value)
    if group == 'print': return print_settings.normalize(value)
    if group == 'targets':
        if set(value) != {'multiplier', 'node_count'}:
            raise TradeError('INVALID_SETTINGS', '目标配置字段不完整')
        multiplier, count = decimal_value(value['multiplier'], '每级倍率'), value['node_count']
        if not Decimal('1.01') <= multiplier <= Decimal('10') or type(count) is not int or not 1 <= count <= 100 or multiplier ** count > Decimal('1000000000000'):
            raise TradeError('INVALID_SETTINGS', '目标倍率或节点数超出允许范围')
        return {'multiplier': format(multiplier, 'f'), 'node_count': count}
    if group == 'market_sources':
        if (set(value) != set(MARKET_DEFAULTS) or value['provider'] not in ('auto', 'baostock', 'akshare')
            or value['provider_order'] not in (['baostock', 'akshare'], ['akshare', 'baostock'])):
            raise TradeError('INVALID_MARKET_SOURCE_CONFIG', '行情来源仅支持 auto/baostock/akshare；回退顺序须各来源恰好一次')
        return deepcopy(value)
    if group == 'calendar':
        if allow_calendar_empty and value == CALENDAR_DEFAULTS: return deepcopy(value)
        try:
            if set(value) != set(CALENDAR_DEFAULTS) or not isinstance(value['source'], str) or not 3 <= len(value['source'].strip()) <= 2000:
                raise ValueError()
            start, end = date.fromisoformat(value['start_date']), date.fromisoformat(value['end_date'])
            if start.isoformat() != value['start_date'] or end.isoformat() != value['end_date']: raise ValueError()
            count = (end - start).days + 1
            if not 1 <= count <= 1096 or not isinstance(value['days'], list) or len(value['days']) != count: raise ValueError()
            for index, day in enumerate(value['days']):
                if not isinstance(day, dict) or set(day) != {'date', 'is_open'} or type(day['is_open']) is not bool or day['date'] != (start + timedelta(days=index)).isoformat(): raise ValueError()
        except (KeyError, ValueError, TypeError) as exc:
            raise TradeError('INVALID_CALENDAR', '日历须有来源说明，连续完整覆盖 1 至 1096 个自然日并逐日标明是否开市') from exc
        return {**deepcopy(value), 'source': value['source'].strip()}
    raise TradeError('SETTINGS_GROUP_NOT_FOUND', '该设置组不存在', 404)


def _revision(current, expected):
    if type(expected) is not int or expected != current['revision']:
        raise TradeError('SETTINGS_REVISION_CONFLICT', '此设置组已变更，草稿保留；请重新读取并核对', 409)
    if current['readonly']:
        raise TradeError('SETTINGS_READONLY', '此账户已冻结，不能修改设置', 409)


def differences(before, after, prefix=''):
    rows = []
    for key in sorted(set(before) | set(after)):
        path = prefix + key
        left, right = before.get(key), after.get(key)
        if isinstance(left, dict) and isinstance(right, dict): rows += differences(left, right, path + '.')
        elif left != right: rows.append({'path': path, 'before': left, 'after': right})
    return rows


def preview(session, group, account_id, expected_revision, value=None):
    current = get_group(session, group, account_id)
    _revision(current, expected_revision)
    defaults = value is None
    after = normalize(group, default_value(group) if defaults else value, allow_calendar_empty=defaults)
    result = {'group': group, 'scope': current['scope'], 'account_id': account_id,
              'expected_revision': current['revision'], 'operation': 'defaults' if defaults else 'update',
              'before': current['value'], 'after': after, 'notes': current['notes']}
    return {**result, 'diff': differences(current['value'], after), 'preview_sha256': digest(result)}


def save_group(session, group, account_id, expected_revision, value, *, operation='update'):
    before = get_group(session, group, account_id)
    _revision(before, expected_revision)
    value = normalize(group, value, allow_calendar_empty=operation == 'restore_defaults')
    if group == 'fees':
        if before['account_kind'] == 'real':
            update_fee_settings(session, account_id, {'expected_version': expected_revision, 'config': value})
        else:
            _, wallet = simulation.sim_account(session, account_id)
            simulation.update_config(session, account_id, {'expected_version': expected_revision,
                                      'config': {**json.loads(wallet.config_json), **value}})
    elif group == 'targets': update_target_config(session, account_id, {'expected_version': expected_revision, **value})
    elif group == 'ai': ai.update_config(session, {'expected_revision': expected_revision, **value})
    elif group == 'calendar':
        if value['days']:
            save_calendar(session, {'expected_revision': expected_revision, **value})
        else:
            row = session.get(LocalTradingCalendar, 'CN_A')
            if row is None:
                row = LocalTradingCalendar(market='CN_A')
                session.add(row)
            row.source = row.start_date = row.end_date = ''
            row.days_json = '[]'
            row.sha256, row.revision, row.updated_at = digest(CALENDAR_DEFAULTS), expected_revision + 1, utc_now()
    else:
        row = session.get(ApplicationSetting, group)
        if row is None:
            row = ApplicationSetting(group_id=group, scope='global_data_directory')
            session.add(row)
        row.revision, row.value_json, row.updated_at = expected_revision + 1, encode(value), utc_now()
    session.flush()
    after = get_group(session, group, account_id)
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='settings_group',
        entity_id=group, operation=operation, before_json=encode(before), after_json=encode(after), created_at=utc_now()))
    # Keep the existing calendar history entry point complete for the reset operation.
    if group == 'calendar' and operation == 'restore_defaults':
        session.add(AuditEvent(id=new_id(), account_id=None, entity_type='local_trading_calendar', entity_id='CN_A',
            operation='restore_defaults', before_json=encode(before['value']), after_json=encode(get_calendar(session)), created_at=utc_now()))
    session.flush()
    return after


def apply_preview(session, group, account_id, expected_revision, expected_sha256, *, value=None):
    prepared = preview(session, group, account_id, expected_revision, value)
    if not isinstance(expected_sha256, str) or prepared['preview_sha256'] != expected_sha256:
        raise TradeError('SETTINGS_PREVIEW_CHANGED', '设置预览已变化或不属于此组/账户，请重新核对', 409)
    return save_group(session, group, account_id, expected_revision, prepared['after'],
                      operation='restore_defaults' if value is None else 'legacy_import')


def history(session, group, account_id=None):
    _account(session, group, account_id)
    rows = session.scalars(select(AuditEvent).where(AuditEvent.entity_type == 'settings_group',
        AuditEvent.entity_id == group, AuditEvent.account_id == account_id).order_by(AuditEvent.created_at.desc(), AuditEvent.id).limit(100))
    return [{'id': row.id, 'operation': row.operation, 'created_at': row.created_at,
             'before': json.loads(row.before_json), 'after': json.loads(row.after_json)} for row in rows]
