"""Parse every versioned/local Python source and JSON file without importing apps."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__',
            '.release-build', '.release-dist', 'dist', 'dist-rebuild', 'build',
            'tmp', 'runtime-logs', '.pytest_cache'}


def inspect_sources() -> dict:
    names = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=ROOT).decode('utf-8').split('\0')
    counts: Counter[str] = Counter()
    failures = []
    checked = []
    for name in sorted(set(filter(None, names))):
        path = Path(name)
        if any(part in EXCLUDED or part.startswith('.tmp-') for part in path.parts):
            continue
        if path.suffix not in {'.py', '.spec', '.json'} or not (ROOT / path).is_file():
            continue
        # TypeScript configuration is JSONC; tsc validates comments/trailing commas.
        if path.name.startswith('tsconfig') and path.suffix == '.json':
            continue
        try:
            source = (ROOT / path).read_text(encoding='utf-8-sig')
            if path.suffix == '.json':
                json.loads(source)
            else:
                ast.parse(source, filename=name)
        except (SyntaxError, UnicodeError, ValueError, OSError) as exc:
            failures.append({'file': name, 'error': str(exc)})
        checked.append(name)
        counts[path.suffix] += 1
    return {'counts': dict(counts), 'checked': checked, 'failures': failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, help='Save the file inventory and diagnostics')
    args = parser.parse_args()
    result = inspect_sources()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Project source syntax:', json.dumps(result['counts']),
          'OK' if not result['failures'] else 'FAILED')
    for failure in result['failures']:
        print(f"{failure['file']}: {failure['error']}")
    return int(bool(result['failures']))


if __name__ == '__main__':
    raise SystemExit(main())
