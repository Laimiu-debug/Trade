"""Frozen outputs of the retired final-trade / LaimiuTrade implementations.

Parity tests used to import the original ``app`` package and compare it with the
rebuilt ``trade_app`` code at runtime. The original code was removed after tag
``legacy-final``; its outputs for the exact same test inputs were recorded into
``tests/fixtures/legacy_oracle/<test module>.json`` first.

``oracle(key, compute)`` returns the recorded, JSON-normalised value. Set
``TRADE_RECORD_LEGACY_ORACLE=1`` (only possible on a checkout that still has the
original package, e.g. ``git checkout legacy-final``) to recompute and rewrite
the fixtures. Keys must be unique per test module and stable across runs.
"""
from __future__ import annotations

import atexit
import dataclasses
import datetime as dt
import decimal
import enum
import inspect
import json
import math
import os
from pathlib import Path
from typing import Any, Callable

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'legacy_oracle'
RECORDING = os.environ.get('TRADE_RECORD_LEGACY_ORACLE') == '1'
_loaded: dict[str, dict[str, Any]] = {}
_recorded: dict[str, dict[str, Any]] = {}


def _default(value: Any) -> Any:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)
    if hasattr(value, 'tolist'):
        return value.tolist()
    if hasattr(value, 'item'):
        return value.item()
    raise TypeError(f'Cannot record {type(value).__name__} in the legacy oracle')


def _floats(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return {'__float__': repr(value)}
    if isinstance(value, dict):
        return {key: _floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_floats(item) for item in value]
    return value


def _unfloats(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {'__float__'}:
            return float(value['__float__'])
        return {key: _unfloats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unfloats(item) for item in value]
    return value


def plain(value: Any) -> Any:
    """Normalise a rebuilt-code value the same way recorded values were stored."""
    encoded = json.dumps(value, default=_default, ensure_ascii=False, allow_nan=True)
    return _unfloats(_floats(json.loads(encoded)))


def _module_name() -> str:
    for frame in inspect.stack()[2:]:
        name = Path(frame.filename).stem
        if name.startswith('test_'):
            return name
    raise RuntimeError('oracle() must be called from a test module')


def _path(module: str) -> Path:
    return FIXTURES / f'{module}.json'


def oracle(key: str, compute: Callable[[], Any] | None = None) -> Any:
    module = _module_name()
    if RECORDING:
        if compute is None:
            raise RuntimeError(f'{module}:{key} has no legacy computation to record')
        value = _floats(json.loads(json.dumps(compute(), default=_default, ensure_ascii=False, allow_nan=True)))
        store = _recorded.setdefault(module, {})
        if key in store and store[key] != value:
            raise RuntimeError(f'{module}:{key} recorded twice with different values')
        store[key] = value
        return _unfloats(value)
    if module not in _loaded:
        path = _path(module)
        if not path.exists():
            raise FileNotFoundError(f'Missing legacy oracle fixture {path.name}')
        _loaded[module] = json.loads(path.read_text(encoding='utf-8'))
    try:
        return _unfloats(_loaded[module][key])
    except KeyError as exc:
        raise KeyError(f'Legacy oracle {module}.json has no key {key!r}') from exc


def _flush() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for module, values in _recorded.items():
        path = _path(module)
        existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        existing.update(values)
        path.write_text(json.dumps(dict(sorted(existing.items())), ensure_ascii=False, indent=1, sort_keys=True) + '\n',
                        encoding='utf-8', newline='\n')


if RECORDING:
    atexit.register(_flush)
