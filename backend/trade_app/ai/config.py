"""Versioned provider metadata. Credentials remain environment references only."""
from __future__ import annotations

from copy import deepcopy
import ipaddress
import math
import os
import re
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from trade_app.ai.models import AIConfig
from trade_app.platform.types import TradeError, utc_now
import json


DEFAULT_CONFIG = {
    'text': {'base_url': '', 'model': '', 'secret_ref': 'TRADE_AI_TEXT_KEY'},
    'vision': {'base_url': '', 'model': '', 'secret_ref': 'TRADE_AI_VISION_KEY'},
    'temperature': 0.3, 'max_tokens': 2048, 'timeout_seconds': 60,
}


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _loopback(host: str) -> bool:
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_config(body: dict) -> dict:
    if set(body) != set(DEFAULT_CONFIG):
        raise TradeError('INVALID_AI_CONFIG', 'AI 配置字段不完整或包含不支持的字段；只能提供密钥环境变量名称')
    normalized = {}
    for channel in ('text', 'vision'):
        value = body[channel]
        if not isinstance(value, dict) or set(value) != {'base_url', 'model', 'secret_ref'}:
            raise TradeError('INVALID_AI_CONFIG', '模型配置只接受 base_url、model、secret_ref')
        if any(not isinstance(item, str) for item in value.values()):
            raise TradeError('INVALID_AI_CONFIG', '模型配置字段须为文本')
        url, model, ref = (value[key].strip() for key in ('base_url', 'model', 'secret_ref'))
        if len(url) > 2048 or len(model) > 128 or any(ord(char) < 32 for char in url + model):
            raise TradeError('INVALID_AI_CONFIG', '模型地址或名称无效')
        local = False
        if url:
            try:
                parsed = urlsplit(url)
                _port = parsed.port
                if (parsed.scheme not in ('https', 'http') or not parsed.hostname or
                        parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
                    raise ValueError()
                local = _loopback(parsed.hostname)
                if parsed.scheme == 'http' and not local:
                    raise ValueError()
                url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))
            except ValueError as exc:
                raise TradeError('INVALID_AI_BASE_URL', '模型地址须为 HTTPS，或显式 localhost HTTP；不得携带凭据、查询串或片段') from exc
        if ref and not re.fullmatch(r'TRADE_AI_[A-Z0-9_]{1,80}', ref):
            raise TradeError('INVALID_AI_SECRET_REF', '密钥引用须为 TRADE_AI_ 开头的环境变量名称')
        if url and not local and not ref:
            raise TradeError('INVALID_AI_SECRET_REF', '远程模型需要密钥环境变量引用')
        if bool(url) != bool(model):
            raise TradeError('INVALID_AI_CONFIG', '模型地址和名称须同时填写或同时留空')
        normalized[channel] = {'base_url': url, 'model': model, 'secret_ref': ref}
    for field, minimum, maximum, integer in (
        ('temperature', 0, 2, False), ('max_tokens', 16, 8192, True), ('timeout_seconds', 5, 120, True),
    ):
        value = body[field]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or not minimum <= value <= maximum or (integer and not isinstance(value, int))):
            raise TradeError('INVALID_AI_CONFIG', f'{field} 超出允许范围')
        normalized[field] = value
    return normalized


def freeze_config(session, expected_revision: int | None = None) -> dict:
    row = session.scalar(select(AIConfig).order_by(AIConfig.revision.desc()).limit(1))
    result = {'revision': row.revision if row else 0,
              **(json.loads(row.config_json) if row else deepcopy(DEFAULT_CONFIG)),
              'updated_at': row.created_at if row else None}
    if expected_revision is not None and (isinstance(expected_revision, bool) or expected_revision != result['revision']):
        raise TradeError('AI_CONFIG_CONFLICT', '模型配置已变更，请重新预览后发送', 409)
    return result


def get_config(session) -> dict:
    result = freeze_config(session)
    for channel in ('text', 'vision'):
        setting = result[channel]
        result[channel] = {**setting, 'configured': bool(setting['base_url'] and setting['model']),
                           'secret_configured': bool(os.environ.get(setting['secret_ref'], '')) if setting['secret_ref'] else False}
    return result


def update_config(session, body: dict) -> dict:
    expected = body.get('expected_revision')
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise TradeError('AI_CONFIG_CONFLICT', '须提供当前配置版本', 409)
    current = freeze_config(session, expected)
    normalized = normalize_config({key: value for key, value in body.items() if key != 'expected_revision'})
    session.add(AIConfig(revision=current['revision'] + 1, config_json=encode(normalized), created_at=utc_now()))
    session.flush()
    return get_config(session)


def resolve_credentials(config: dict, channel: str) -> tuple[dict, str]:
    setting = config[channel]
    if not setting['base_url'] or not setting['model']:
        raise TradeError('AI_NOT_CONFIGURED', '尚未配置该类模型', 409)
    ref = setting['secret_ref']
    key = os.environ.get(ref, '') if ref else ''
    if ref and not key:
        raise TradeError('AI_SECRET_MISSING', '模型密钥环境变量尚未配置', 409)
    if key and (len(key) > 4096 or '\r' in key or '\n' in key):
        raise TradeError('AI_SECRET_INVALID', '模型密钥环境变量格式无效', 409)
    return setting, key
