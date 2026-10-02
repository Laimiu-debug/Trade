"""Freeze legacy strategy descriptors for the independent rebuild catalog."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend'))
from app.core.strategy_registry import StrategyRegistry  # noqa: E402


def main() -> None:
    registry_path = root / 'backend/app/core/strategy_registry.py'
    plugins_path = root / 'backend/app/core/strategy_plugins.py'
    digest = hashlib.sha256(registry_path.read_bytes() + plugins_path.read_bytes()).hexdigest()
    strategies = [asdict(row) for row in StrategyRegistry().list()]
    if len(strategies) != 16 or len({row['strategy_id'] for row in strategies}) != 16:
        raise RuntimeError('Expected exactly 16 distinct legacy strategies')
    snapshot = {'schema_version': 1, 'source_sha256': digest, 'strategies': strategies}
    target = root / 'backend/trade_app/research/legacy_catalog.json'
    contents = json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n'
    if '--check' in sys.argv:
        if not target.exists() or target.read_text(encoding='utf-8') != contents:
            raise SystemExit('Legacy strategy catalog is stale; rerun export_rebuild_strategy_catalog.py')
    else:
        target.write_text(contents, encoding='utf-8')
    print(f'{target}: {len(strategies)} strategies, source {digest[:12]}')


if __name__ == '__main__':
    main()
