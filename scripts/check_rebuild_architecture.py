"""Static import boundary check for the new application; no application imports."""
from __future__ import annotations

import ast
import sys
from pathlib import Path


root = Path(__file__).resolve().parents[1] / 'backend' / 'trade_app'
domains = {'market', 'research', 'trading', 'reviews', 'analytics', 'ai', 'platform'}
allowed = {
    'platform': set(),
    'trading': {'platform'},
    'market': {'platform'},
    'research': {'platform', 'market', 'trading'},
    'reviews': {'platform', 'trading', 'analytics'},
    'analytics': {'platform', 'trading'},
    'ai': {'platform', 'trading', 'reviews', 'analytics', 'market', 'research'},
}
pure_modules = {root / 'trading' / 'domain.py', root / 'trading' / 'nav.py',
                root / 'platform' / 'symbols.py',
                root / 'analytics' / 'domain.py', root / 'market' / 'domain.py',
                root / 'market' / 'symbols.py', root / 'research' / 'domain.py',
                root / 'research' / 'wulong_universe.py'}
pure_modules.update((root / 'research').glob('*_domain.py'))
errors: list[str] = []
edges: dict[str, set[str]] = {domain: set() for domain in domains}
for file in root.rglob('*.py'):
    if file.name == '__init__.py':
        continue
    relative = file.relative_to(root)
    source_domain = relative.parts[0] if relative.parts[0] in domains else None
    tree = ast.parse(file.read_text(encoding='utf-8'), filename=str(file))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if file in pure_modules and name.split('.')[0] in {'fastapi', 'sqlalchemy', 'httpx', 'requests'}:
                errors.append(f'{relative}:{node.lineno}: pure module imports {name}')
            if not name.startswith('trade_app.'):
                continue
            parts = name.split('.')
            target = parts[1] if len(parts) > 1 else ''
            if source_domain and target in domains and target != source_domain:
                edges[source_domain].add(target)
                if target not in allowed[source_domain]:
                    errors.append(f'{relative}:{node.lineno}: {source_domain} cannot import {target}')


def visit(domain: str, stack: list[str], seen: set[str]) -> None:
    if domain in stack:
        errors.append('domain cycle: ' + ' -> '.join(stack + [domain]))
        return
    if domain in seen:
        return
    seen.add(domain)
    for neighbor in edges[domain]:
        visit(neighbor, stack + [domain], seen)


seen: set[str] = set()
for domain in domains:
    visit(domain, [], seen)
if errors:
    print('\n'.join(errors), file=sys.stderr)
    sys.exit(1)
print('Trade rebuild architecture boundaries: OK')
