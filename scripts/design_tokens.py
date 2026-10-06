"""Load docs/design-tokens.json and resolve palette references.

Semantic and component values may reference primitives as ``{palette.blue.600}``
or other semantic roles as ``{color.bg.surface}`` (same theme). Consumers always
receive concrete values, so CSS, TypeScript and contrast checks agree.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / 'docs' / 'design-tokens.json'
REFERENCE = re.compile(r'\{([a-zA-Z0-9_.-]+)\}')


def _lookup(path: str, palette: dict, colors: dict) -> str:
    head, _, rest = path.partition('.')
    if head == 'palette':
        family, _, step = rest.partition('.')
        try:
            return palette[family][step]
        except KeyError as exc:
            raise KeyError(f'Unknown palette reference {{{path}}}') from exc
    if head == 'color':
        if rest not in colors:
            raise KeyError(f'Unknown color reference {{{path}}}')
        return colors[rest]
    raise KeyError(f'Unsupported token reference {{{path}}}')


def _resolve(value, palette: dict, colors: dict, depth: int = 0):
    if isinstance(value, dict):
        return {key: _resolve(item, palette, colors, depth) for key, item in value.items()}
    if not isinstance(value, str) or '{' not in value:
        return value
    if depth > 8:
        raise ValueError(f'Token reference cycle near {value!r}')
    resolved = REFERENCE.sub(lambda match: str(_lookup(match.group(1), palette, colors)), value)
    return _resolve(resolved, palette, colors, depth + 1)


def load_raw() -> dict:
    return json.loads(source.read_text(encoding='utf-8'))


def load_resolved() -> dict:
    tokens = load_raw()
    palette = tokens['palette']
    for theme in tokens['themes'].values():
        colors = theme['color']
        theme['color'] = {key: _resolve(item, palette, colors) for key, item in colors.items()}
        theme['shadow'] = _resolve(theme['shadow'], palette, theme['color'])
        if 'component' in theme:
            theme['component'] = _resolve(theme['component'], palette, theme['color'])
    return tokens
