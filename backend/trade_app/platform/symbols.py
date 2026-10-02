"""Pure A-share identity parsing for stock strategy board rules.

The canonical identity is used at calculation boundaries. Imported symbols and
immutable dataset manifests retain their original spelling.
"""
from __future__ import annotations

import re

from trade_app.platform.types import TradeError


def normalize_a_share_symbol(raw: str) -> tuple[str, str]:
    """Return (exchange, code) for a plain, prefixed, or suffixed stock code."""
    value = str(raw).strip().lower()
    match = re.fullmatch(r'(?:(sh|sz|bj))?([0-9]{6})(?:\.(sh|sz|bj))?', value)
    if match is None:
        raise TradeError('INVALID_SYMBOL', '证券代码须为 6 位数字，或带 sh/sz/bj 前缀或后缀')
    prefix, code, suffix = match.groups()
    if code.startswith(('4', '8', '920')):
        exchange = 'bj'
    elif code.startswith(('600', '601', '603', '605', '688', '689')):
        exchange = 'sh'
    elif code.startswith(('000', '001', '002', '003', '300', '301')):
        exchange = 'sz'
    else:
        raise TradeError('INVALID_SYMBOL', '该代码不在当前支持的 A 股证券代码范围内')
    if any(marker and marker != exchange for marker in (prefix, suffix)):
        raise TradeError('SYMBOL_EXCHANGE_MISMATCH', '证券代码与交易所标记不一致')
    return exchange, code


def normalize_market_symbol(raw: str) -> tuple[str, str]:
    """Parse market instruments without confusing index and stock identities.

    Stock rules remain centralized above. Explicit SH 000xxx indices and TDX
    SH/SZ 88xxxx sector indices retain their exchange; funds and other existing
    six-digit instruments keep the previous TDX fallback convention.
    """
    value = str(raw).strip().lower()
    match = re.fullmatch(r'(?:(sh|sz|bj))?([0-9]{6})(?:\.(sh|sz|bj))?', value)
    if match is None:
        raise TradeError('INVALID_SYMBOL', '证券代码须为 6 位数字，或带 sh/sz/bj 前缀或后缀')
    prefix, code, suffix = match.groups()
    if prefix and suffix and prefix != suffix:
        raise TradeError('SYMBOL_EXCHANGE_MISMATCH', '证券代码的交易所前缀与后缀不一致')
    marker = prefix or suffix
    if (marker == 'sh' and code.startswith('000')) or (marker in ('sh', 'sz') and code.startswith('88')):
        return marker, code
    try:
        return normalize_a_share_symbol(value)
    except TradeError as exc:
        if exc.code != 'INVALID_SYMBOL':
            raise
    inferred = 'sh' if code.startswith(('5', '6', '9')) else 'bj' if code.startswith(('4', '8')) else 'sz'
    return marker or inferred, code


def validated_market_symbol_key(raw: str) -> str:
    """Validate new facts while allowing existing custom ticker formats."""
    value = str(raw).strip().upper()
    if not re.fullmatch(r'(?:(?:SH|SZ|BJ))?[0-9]{6}(?:\.(?:SH|SZ|BJ))?', value):
        return 'raw:' + value
    exchange, code = normalize_market_symbol(value)
    return exchange + code


def market_symbol_key(raw: str) -> str:
    """Read historic facts without rewriting or merging inconsistent markers.

    Older ledger APIs allowed any nonempty ticker. A mismatched exchange marker
    must keep its own identity rather than blocking the whole account upgrade.
    New facts use validated_market_symbol_key at their write boundary.
    """
    try:
        return validated_market_symbol_key(raw)
    except TradeError:
        return 'raw:' + str(raw).strip().upper()


def market_symbol_aliases(raw: str) -> tuple[str, ...]:
    """Uppercase spellings for SQL comparisons, retaining exchange identity.

    A bare code is included only when it identifies the same instrument. In
    particular, SH 000001 must never match the SZ stock with the bare 000001.
    """
    value = str(raw).strip().upper()
    key = market_symbol_key(value)
    if key.startswith('raw:'):
        return (value,)
    exchange, code = normalize_market_symbol(value)
    marker = exchange.upper()
    candidates = (code, marker + code, code + '.' + marker, marker + code + '.' + marker)
    return tuple(candidate for candidate in candidates if market_symbol_key(candidate) == key)


def storage_market_symbol(exchange: str, code: str) -> str:
    """Keep existing plain stock IDs, preserving exchange when plain is ambiguous."""
    return code if normalize_market_symbol(code) == (exchange, code) else exchange + code


def a_share_board(raw: str) -> str:
    exchange, code = normalize_a_share_symbol(raw)
    if exchange == 'bj':
        return 'beijing'
    if code.startswith(('688', '689')):
        return 'star'
    if code.startswith(('300', '301')):
        return 'gem'
    return 'main'


def standard_limit_up_ratio(raw: str) -> float:
    """Return the existing standard board rule; ST and special sessions are separate."""
    board = a_share_board(raw)
    return 0.30 if board == 'beijing' else 0.20 if board in ('star', 'gem') else 0.10
