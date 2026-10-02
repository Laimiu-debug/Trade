"""Generate CSS variables from the single design-token source."""
from __future__ import annotations

import json
from pathlib import Path


root = Path(__file__).resolve().parents[1]
source = root / 'docs' / 'design-tokens.json'
target = root / 'frontend' / 'src' / 'rebuild' / 'tokens.generated.css'
tokens = json.loads(source.read_text(encoding='utf-8'))


def theme_variables(theme: dict) -> list[str]:
    lines: list[str] = []
    for key, item in theme['color'].items():
        lines.append(f"  --{key.replace('.', '-')}: {item};")
    for key, item in theme['shadow'].items():
        lines.append(f'  --shadow-{key}: {item};')
    return lines


def dimension_variables() -> list[str]:
    lines: list[str] = []
    for group in ('spacePx', 'radiusPx', 'sizePx', 'breakpointPx'):
        for key, item in tokens[group].items():
            lines.append(f'  --{group}-{key}: {item}px;')
    return lines


def supporting_variables(value: dict, prefix: str = '') -> list[str]:
    lines: list[str] = []
    for key, item in value.items():
        name = f'{prefix}-{key}' if prefix else key
        if isinstance(item, dict):
            lines.extend(supporting_variables(item, name))
        else:
            unit = ('px' if 'sizePx' in name or 'SizePx' in name else
                    'ms' if name.endswith('Ms') else
                    'mm' if name.endswith('Mm') else
                    'pt' if name.endswith('Pt') else '')
            lines.append(f'  --{name}: {item}{unit};')
    return lines


parts = ['/* Generated from docs/design-tokens.json. Do not edit directly. */']
for theme, selector in [('light', ':root, [data-theme="light"]'), ('dark', '[data-theme="dark"]')]:
    parts += [selector + ' {', *theme_variables(tokens['themes'][theme]), '}']
parts += [':root {', *dimension_variables(),
          *supporting_variables({key: tokens[key] for key in ('font', 'motion', 'layer', 'print')}), '}']
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text('\n'.join(parts) + '\n', encoding='utf-8')
print(target)
