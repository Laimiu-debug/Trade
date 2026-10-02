"""Portable supported parameters; proposals only mutate presets after explicit apply.

Presets and share codes intentionally exclude event templates, accounts, bars,
provider configuration, AI context and execution settings. They never select a
global strategy or launch research, a backtest or an order.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.matrix_domain import MATRIX_ID, normalize_matrix_params
from trade_app.research.preset_models import StrategyParameterProposal, StrategyPreset, StrategyPresetRevision
from trade_app.research.runtime import SINGLE_SYMBOL_STRATEGIES
from trade_app.research.service import normalize_strategy_params, strategy_catalog


SHARE_PREFIX = 'TRADE-PRESET-1'
MAX_SHARE_BYTES = 64 * 1024
SHARE_KEYS = {'format_version', 'strategy_id', 'strategy_version', 'params', 'name'}
SUPPORTED_PRESET_STRATEGIES = (*SINGLE_SYMBOL_STRATEGIES, MATRIX_ID, 'b1_mtf_v1')


def _encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value) -> str:
    return hashlib.sha256(_encode(value).encode('utf-8')).hexdigest()


def _fields(body, allowed, required=()):
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
        raise TradeError('INVALID_PRESET_FIELDS', '预设字段不完整或包含不支持的内容')


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(ch) < 32 for ch in value):
        raise TradeError('INVALID_PRESET_NAME', '预设名称须为 1 至 80 个字符且不能包含控制字符')
    return value.strip()


def _revision(value, actual):
    if type(value) is not int or value != actual:
        raise TradeError('PRESET_VERSION_CONFLICT', '预设已修改，请刷新并重新预览', 409)


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    raise TradeError('INVALID_PRESET_FAVORITE', '收藏标记须为 true 或 false')


def _descriptor(strategy_id: str) -> dict:
    if strategy_id not in SUPPORTED_PRESET_STRATEGIES:
        raise TradeError('STRATEGY_PRESET_UNSUPPORTED', '该策略尚未提供可执行参数预设', 409)
    descriptor = next(item for item in strategy_catalog() if item['id'] == strategy_id)
    defaults = descriptor['pool_params'] if strategy_id == MATRIX_ID else descriptor['scanner_params'] if strategy_id == 'b1_mtf_v1' else descriptor['signal_params']
    schema = {key: descriptor['params_schema'].get(key, {}) for key in defaults}
    metadata = {'strategy_id': strategy_id, 'strategy_version': descriptor['version'],
                'calculation_version': descriptor['calculation_version'], 'schema': schema, 'defaults': defaults}
    folder = Path(__file__).parent
    normalizers = ('preset_service.py', 'service.py', 'domain.py', 'matrix_domain.py',
                   'wyckoff_strategy.py', 'wulong_universe.py', 'b1_params.py')
    code_hash = hashlib.sha256(b''.join((folder / filename).read_bytes() for filename in normalizers)).hexdigest()
    return {**metadata, 'schema_sha256': _sha(schema), 'catalog_sha256': _sha(metadata),
            'contract_sha256': _sha({'catalog': metadata, 'normalizer_code': code_hash})}


def normalize_preset_params(strategy_id: str, raw: dict) -> dict[str, str]:
    descriptor = _descriptor(strategy_id)
    schema = descriptor['schema']
    if not isinstance(raw, dict) or len(raw) > 128 or set(raw) - set(schema):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '包含未实现或只读策略参数')
    prepared = {}
    for key, value in raw.items():
        spec = schema[key]
        if spec.get('readonly') or spec.get('readOnly'):
            raise TradeError('READONLY_STRATEGY_PARAM', f'{key} 为只读参数')
        if type(value) not in (str, int, float, bool) or isinstance(value, str) and len(value) > 256:
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为短文本、数值或布尔值')
        if isinstance(value, bool) and spec.get('type') != 'boolean':
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为数值')
        prepared[key] = str(value).lower() if isinstance(value, bool) else value
    from trade_app.research.b1_params import normalize_b1_params
    normalized = normalize_matrix_params(prepared) if strategy_id == MATRIX_ID else normalize_b1_params(prepared) if strategy_id == 'b1_mtf_v1' else normalize_strategy_params(strategy_id, prepared)
    result = {}
    for key, value in normalized.items():
        if key not in schema:
            # Runtime-derived fields (for example trend-king mode) are read-only.
            continue
        if isinstance(value, bool):
            result[key] = str(value).lower()
        elif schema[key].get('type') in ('number', 'integer'):
            number = Decimal(str(value))
            result[key] = '0' if not number else format(number.normalize(), 'f')
        else:
            result[key] = str(value)
    return result


def _metadata(strategy_id):
    return {key: value for key, value in _descriptor(strategy_id).items() if key not in ('schema', 'defaults')}


def _snapshot(row):
    return {**json.loads(row.snapshot_json), 'id': row.id, 'revision': row.revision,
            'favorite': bool(row.favorite), 'deleted': bool(row.deleted),
            'created_at': row.created_at, 'updated_at': row.updated_at}


def _row(session, preset_id, include_deleted=False):
    row = session.get(StrategyPreset, preset_id)
    if row is None or row.deleted and not include_deleted:
        raise TradeError('STRATEGY_PRESET_NOT_FOUND', '策略预设不存在或已删除', 404)
    return row


def _data(row):
    snapshot = _snapshot(row)
    current = _metadata(row.strategy_id)
    return {**snapshot, 'compatible': all(snapshot.get(key) == value for key, value in current.items()),
            'current_contract': current,
            'scope': 'parameters_only', 'event_profile_included': False}


def get_preset(session: Session, preset_id: str) -> dict:
    return _data(_row(session, preset_id))


def list_presets(session: Session, strategy_id: str | None = None, favorites_only: bool = False) -> list[dict]:
    query = select(StrategyPreset).where(StrategyPreset.deleted == 0)
    if strategy_id is not None:
        _descriptor(strategy_id)
        query = query.where(StrategyPreset.strategy_id == strategy_id)
    if favorites_only:
        query = query.where(StrategyPreset.favorite == 1)
    return [_data(row) for row in session.scalars(query.order_by(
        StrategyPreset.favorite.desc(), StrategyPreset.updated_at.desc(), StrategyPreset.id))]


def _audit(session, row, action, extra=None):
    session.add(StrategyPresetRevision(id=new_id(), preset_id=row.id, revision=row.revision,
                                      action=action, snapshot_json=_encode({**_snapshot(row), **(extra or {})}),
                                      created_at=utc_now()))


def save_preset(session: Session, body: dict, preset_id: str | None = None) -> dict:
    _fields(body, {'name', 'strategy_id', 'params', 'favorite', 'expected_revision'}, {'name', 'strategy_id', 'params'})
    row = _row(session, preset_id) if preset_id else None
    if row is not None:
        _revision(body.get('expected_revision'), row.revision)
        if row.strategy_id != body['strategy_id']:
            raise TradeError('PRESET_STRATEGY_CONFLICT', '更新预设不能改变所属策略，请另存新预设', 409)
    elif body.get('expected_revision') is not None:
        raise TradeError('PRESET_VERSION_CONFLICT', '新预设不能指定已有版本', 409)
    snapshot = {'name': _name(body['name']), **_metadata(body['strategy_id']),
                'params': normalize_preset_params(body['strategy_id'], body['params'])}
    favorite = _boolean(body.get('favorite', bool(row.favorite) if row else False))
    now = utc_now()
    if row is None:
        row = StrategyPreset(id=new_id(), strategy_id=body['strategy_id'], revision=1,
                             snapshot_json=_encode(snapshot), favorite=int(favorite), deleted=0,
                             created_at=now, updated_at=now)
        session.add(row)
        action = 'create'
        session.flush()
    else:
        row.revision += 1
        row.snapshot_json, row.favorite, row.updated_at = _encode(snapshot), int(favorite), now
        action = 'update'
    _audit(session, row, action)
    session.flush()
    return _data(row)


def delete_preset(session: Session, preset_id: str, body: dict) -> dict:
    _fields(body, {'expected_revision'}, {'expected_revision'})
    row = _row(session, preset_id)
    _revision(body['expected_revision'], row.revision)
    row.deleted, row.revision, row.updated_at = 1, row.revision + 1, utc_now()
    _audit(session, row, 'delete')
    session.flush()
    return {'deleted': True, 'id': row.id, 'revision': row.revision}


def preset_history(session: Session, preset_id: str) -> list[dict]:
    _row(session, preset_id, include_deleted=True)
    return [{'id': row.id, 'revision': row.revision, 'action': row.action,
             'snapshot': json.loads(row.snapshot_json), 'created_at': row.created_at}
            for row in session.scalars(select(StrategyPresetRevision).where(
                StrategyPresetRevision.preset_id == preset_id).order_by(StrategyPresetRevision.revision.desc()))]


def _ensure_compatible(snapshot):
    current = _metadata(snapshot['strategy_id'])
    if any(snapshot.get(key) != value for key, value in current.items()):
        raise TradeError('PRESET_CONTRACT_CHANGED', '策略参数规范已更新，请检查并重新保存预设', 409)


def export_preset(session: Session, preset_id: str) -> dict:
    snapshot = get_preset(session, preset_id)
    _ensure_compatible(snapshot)
    payload = {'format_version': 1, **{key: snapshot[key] for key in SHARE_KEYS - {'format_version'}}}
    raw = _encode(payload).encode('utf-8')
    code = SHARE_PREFIX + '.' + base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=') + '.' + hashlib.sha256(raw).hexdigest()
    if len(code.encode()) > MAX_SHARE_BYTES:
        raise TradeError('PRESET_SHARE_TOO_LARGE', '分享码超过 64 KiB 限制')
    return {'share_code': code, 'format_version': 1, 'scope': 'parameters_only', 'event_profile_included': False}


def _json_object(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError('Nonfinite JSON number')

    def depth(value, level=0):
        if level > 8:
            raise ValueError('JSON nesting limit')
        if isinstance(value, dict):
            for child in value.values():
                depth(child, level + 1)
        elif isinstance(value, list):
            for child in value:
                depth(child, level + 1)

    try:
        if len(raw) > MAX_SHARE_BYTES:
            raise ValueError('JSON size limit')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=reject_constant)
        depth(value)
        if not isinstance(value, dict):
            raise ValueError('Object required')
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TradeError('INVALID_PRESET_JSON', '预设须为有效且层级受限的 JSON 对象') from exc


def _decode_share(code) -> dict:
    if not isinstance(code, str) or len(code) > MAX_SHARE_BYTES or len(code.encode('utf-8')) > MAX_SHARE_BYTES:
        raise TradeError('PRESET_SHARE_TOO_LARGE', '分享码超过 64 KiB 限制')
    parts = code.split('.')
    if len(parts) != 3 or parts[0] != SHARE_PREFIX or not re.fullmatch('[A-Za-z0-9_-]+', parts[1]) or not re.fullmatch('[0-9a-f]{64}', parts[2]):
        raise TradeError('INVALID_PRESET_SHARE', '分享码格式或版本不受支持')
    try:
        raw = base64.b64decode(parts[1] + '=' * (-len(parts[1]) % 4), altchars=b'-_', validate=True)
    except (ValueError, binascii.Error) as exc:
        raise TradeError('INVALID_PRESET_SHARE', '分享码编码无效') from exc
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), parts[2]):
        raise TradeError('PRESET_SHARE_CHECKSUM', '分享码校验失败，请重新复制')
    payload = _json_object(raw)
    _fields(payload, SHARE_KEYS, SHARE_KEYS)
    if type(payload['format_version']) is not int or payload['format_version'] != 1:
        raise TradeError('PRESET_SHARE_VERSION', '分享格式版本不受支持', 409)
    descriptor = _descriptor(payload['strategy_id'])
    if payload['strategy_version'] != descriptor['strategy_version']:
        raise TradeError('PRESET_STRATEGY_VERSION', '分享码策略版本与当前版本不同，不能自动导入', 409)
    if _encode(payload).encode('utf-8') != raw:
        raise TradeError('INVALID_PRESET_SHARE', '分享码须使用规范 JSON 编码')
    return {'name': _name(payload['name']), **_metadata(payload['strategy_id']),
            'params': normalize_preset_params(payload['strategy_id'], payload['params'])}


def _diff(before, after):
    return [{'parameter': key, 'before': before.get(key), 'after': after.get(key)}
            for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]


def preview_import(session: Session, body: dict) -> dict:
    _fields(body, {'share_code', 'current_params'}, {'share_code'})
    snapshot = _decode_share(body['share_code'])
    current = normalize_preset_params(snapshot['strategy_id'], body.get('current_params', {}))
    return {**snapshot, 'diff': _diff(current, snapshot['params']), 'preview_sha256': _sha(snapshot),
            'scope': 'parameters_only', 'event_profile_included': False, 'saved': False}


def _proposal_data(row):
    return {**json.loads(row.snapshot_json), 'id': row.id, 'preset_id': row.preset_id,
            'base_revision': row.base_revision, 'proposal_sha256': row.proposal_sha256,
            'status': row.status, 'applied_revision': row.applied_revision,
            'created_at': row.created_at, 'applied_at': row.applied_at}


def get_parameter_proposal(session: Session, proposal_id: str) -> dict:
    row = session.get(StrategyParameterProposal, proposal_id)
    if row is None:
        raise TradeError('PARAMETER_PROPOSAL_NOT_FOUND', '参数提案不存在', 404)
    return _proposal_data(row)


def _ai_candidate(body, strategy_id, ai_source):
    # The API layer obtains this through ai.service.get_run with account scope.
    # Research consumes a narrow verified DTO and never reads another domain's DB.
    account_id = body.get('account_id')
    if not isinstance(account_id, str) or not account_id:
        raise TradeError('AI_PROPOSAL_ACCOUNT_REQUIRED', 'AI 参数提案须指定所属账户')
    _fields(ai_source, {'id', 'account_id', 'status', 'kind', 'output'},
            {'id', 'account_id', 'status', 'kind', 'output'})
    if ai_source['id'] != body['source_ai_run_id'] or ai_source['account_id'] != account_id:
        raise TradeError('AI_CALL_NOT_FOUND', '该账户下不存在此 AI 调用', 404)
    if ai_source['status'] != 'completed' or ai_source['kind'] != 'chat':
        raise TradeError('AI_PROPOSAL_NOT_READY', '仅成功完成的 AI 调用可以生成参数提案', 409)
    output = ai_source['output']
    if not isinstance(output, str) or len(output) > MAX_SHARE_BYTES:
        raise TradeError('INVALID_PRESET_JSON', 'AI 参数输出格式无效或超过 64 KiB 限制')
    candidate = _json_object(output.encode('utf-8'))
    _fields(candidate, {'strategy_id', 'strategy_version', 'params'}, {'strategy_id', 'params'})
    if candidate['strategy_id'] != strategy_id:
        raise TradeError('PRESET_STRATEGY_CONFLICT', 'AI 提案所属策略与目标预设不同', 409)
    if candidate.get('strategy_version', _metadata(strategy_id)['strategy_version']) != _metadata(strategy_id)['strategy_version']:
        raise TradeError('PRESET_STRATEGY_VERSION', 'AI 提案策略版本已失效', 409)
    return candidate['params'], {'kind': 'ai', 'source_ai_run_id': ai_source['id'], 'account_id': account_id,
                                 'output_sha256': hashlib.sha256(output.encode()).hexdigest()}


def create_parameter_proposal(session: Session, body: dict, *, ai_source: dict | None = None) -> dict:
    _fields(body, {'preset_id', 'expected_revision', 'params', 'source_ai_run_id', 'account_id'},
            {'preset_id', 'expected_revision'})
    preset = get_preset(session, body['preset_id'])
    _revision(body['expected_revision'], preset['revision'])
    _ensure_compatible(preset)
    if bool(body.get('source_ai_run_id')) == ('params' in body):
        raise TradeError('INVALID_PARAMETER_PROPOSAL', '须且只能提供参数或已完成 AI 调用')
    if body.get('source_ai_run_id'):
        raw, source = _ai_candidate(body, preset['strategy_id'], ai_source)
    else:
        if body.get('account_id') is not None or ai_source is not None:
            raise TradeError('INVALID_PARAMETER_PROPOSAL', '手动参数提案不接受账户上下文')
        raw, source = body['params'], {'kind': 'manual'}
    if not isinstance(raw, dict):
        raise TradeError('INVALID_PARAMETER_PROPOSAL', '提案参数须为 JSON 对象')
    params = normalize_preset_params(preset['strategy_id'], {**preset['params'], **raw})
    snapshot = {**_metadata(preset['strategy_id']), 'params': params, 'base_params': preset['params'],
                'submitted_params': {key: params[key] for key in raw},
                'base_snapshot_sha256': _sha({key: preset[key] for key in ('name', 'params', 'favorite', 'revision')}),
                'diff': _diff(preset['params'], params), 'source': source}
    digest = _sha({'preset_id': preset['id'], 'base_revision': preset['revision'], 'snapshot': snapshot})
    row = StrategyParameterProposal(id=new_id(), preset_id=preset['id'], base_revision=preset['revision'],
                                    proposal_sha256=digest, snapshot_json=_encode(snapshot), status='pending',
                                    applied_revision=None, created_at=utc_now(), applied_at=None)
    session.add(row)
    session.flush()
    return _proposal_data(row)


def apply_parameter_proposal(session: Session, proposal_id: str, body: dict) -> dict:
    _fields(body, {'expected_revision', 'expected_proposal_hash'}, {'expected_revision', 'expected_proposal_hash'})
    proposal = get_parameter_proposal(session, proposal_id)
    row = session.get(StrategyParameterProposal, proposal_id)
    digest = _sha({'preset_id': row.preset_id, 'base_revision': row.base_revision,
                   'snapshot': json.loads(row.snapshot_json)})
    if not hmac.compare_digest(digest, row.proposal_sha256):
        raise TradeError('PARAMETER_PROPOSAL_CONFLICT', '参数提案内容校验失败，请重新预览', 409)
    if not isinstance(body['expected_proposal_hash'], str) or not hmac.compare_digest(body['expected_proposal_hash'], proposal['proposal_sha256']):
        raise TradeError('PARAMETER_PROPOSAL_CONFLICT', '参数提案校验不一致，请重新预览', 409)
    _revision(body['expected_revision'], proposal['base_revision'])
    if row.status == 'applied':
        revision = session.scalar(select(StrategyPresetRevision).where(
            StrategyPresetRevision.preset_id == row.preset_id, StrategyPresetRevision.revision == row.applied_revision))
        return {'proposal': proposal, 'preset': json.loads(revision.snapshot_json), 'already_applied': True,
                'current_revision': _row(session, row.preset_id, include_deleted=True).revision}
    preset_row = _row(session, row.preset_id)
    preset = _snapshot(preset_row)
    _revision(row.base_revision, preset_row.revision)
    _ensure_compatible(proposal)
    base_hash = _sha({key: preset[key] for key in ('name', 'params', 'favorite', 'revision')})
    if base_hash != proposal['base_snapshot_sha256']:
        raise TradeError('PARAMETER_PROPOSAL_CONFLICT', '预设基准已改变，请重新预览', 409)
    # Revalidate before mutation; a proposal is never an execution instruction.
    params = normalize_preset_params(preset_row.strategy_id, proposal['params'])
    snapshot = json.loads(preset_row.snapshot_json)
    snapshot['params'] = params
    preset_row.snapshot_json, preset_row.revision, preset_row.updated_at = _encode(snapshot), preset_row.revision + 1, utc_now()
    _audit(session, preset_row, 'apply_proposal', {'proposal_id': row.id, 'proposal_sha256': row.proposal_sha256})
    row.status, row.applied_revision, row.applied_at = 'applied', preset_row.revision, utc_now()
    session.flush()
    return {'proposal': _proposal_data(row), 'preset': _data(preset_row), 'already_applied': False,
            'current_revision': preset_row.revision}
