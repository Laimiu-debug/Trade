"""Generate CSS variables from the single design-token source."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from design_tokens import load_resolved, root  # noqa: E402

target = root / 'frontend' / 'src' / 'rebuild' / 'tokens.generated.css'
tokens = load_resolved()


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


def palette_variables() -> list[str]:
    return [f'  --palette-{family}-{step}: {value};'
            for family, steps in tokens['palette'].items() for step, value in steps.items()]


parts = ['/* Generated from docs/design-tokens.json. Do not edit directly. */', ':root {', *palette_variables(), '}']
for theme, selector in [('light', ':root, [data-theme="light"]'), ('dark', '[data-theme="dark"]')]:
    parts += [selector + ' {', *theme_variables(tokens['themes'][theme]), '}']
parts += [':root {', *dimension_variables(),
          *supporting_variables({key: tokens[key] for key in ('font', 'motion', 'layer', 'print')}), '}']
css = '\n'.join(parts) + '\n'
typescript = '// Generated from docs/design-tokens.json. Do not edit.\nexport const designTokens = ' + json.dumps(tokens, ensure_ascii=False, indent=2) + ' as const\n'
outputs = {
    target: css,
    root / 'frontend/src/rebuild/design-tokens.generated.ts': typescript,
}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
stale = []
for path, contents in outputs.items():
    if args.check:
        if not path.exists() or path.read_text(encoding='utf-8') != contents:
            stale.append(str(path.relative_to(root)))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding='utf-8', newline='\n')
if stale:
    parser.exit(1, 'Stale design tokens: ' + ', '.join(stale) + '\n')
print('Trade shared CSS / TypeScript tokens: ' + ('current' if args.check else 'generated'))
