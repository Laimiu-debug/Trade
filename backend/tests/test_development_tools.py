"""Release builder and offline stock library boundaries."""
import hashlib
import json
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('public_library', [False, True])
def test_rebuild_package_rejects_missing_offline_stock_library_before_capture(
        tmp_path, monkeypatch, capsys, public_library):
    spec = importlib.util.spec_from_file_location('test_release_builder', ROOT / 'scripts/build_trade_rebuild.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['build_trade_rebuild.py', '--skip-frontend'])
    monkeypatch.setattr(builder.importlib.util, 'find_spec', lambda _name: object())
    monkeypatch.setattr(builder.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('Incomplete resources must not start a build'))
    if public_library:
        public = tmp_path / 'frontend/public/data'
        public.mkdir(parents=True)
        (public / 'stock-database.slim.json').write_text('[]', encoding='utf-8')
        built = tmp_path / 'frontend/dist-rebuild'
        built.mkdir()
        (built / 'rebuild.html').write_text('<html></html>', encoding='utf-8')
    with pytest.raises(SystemExit) as failure:
        builder.main()
    assert failure.value.code == 2
    assert 'offline stock library' in capsys.readouterr().err
    assert not (tmp_path / '.release-dist').exists()


def test_checked_in_offline_stock_library_has_verified_source_and_search_fields():
    public = ROOT / 'frontend/public/data'
    raw = (public / 'stock-database.slim.json').read_bytes()
    rows = json.loads(raw)
    source = json.loads((public / 'stock-library-source.json').read_text(encoding='utf-8'))
    assert len(rows) == source['count'] and len(rows) > 1000
    assert hashlib.sha256(raw).hexdigest() == source['library_sha256']
    assert len({row['ts_code'] for row in rows}) == len(rows)
    for row in rows:
        assert all(isinstance(row[key], str) for key in (
            'ts_code', 'symbol', 'name', 'industry', 'cnspell', 'exchange', 'list_status'))
        assert len(row['symbol']) == 6 and row['symbol'].isdigit()
        assert row['ts_code'] == row['symbol'] + '.' + {'SSE': 'SH', 'SZSE': 'SZ'}[row['exchange']]
        assert row['name'] and row['cnspell'] and row['list_status'] == 'L'
    example = next(row for row in rows if row['ts_code'] == '600000.SH')
    assert example['name'] == '浦发银行' and example['cnspell'] == 'pfyh'
