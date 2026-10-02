"""Standalone transport fixtures only; no SSH connection or user's TDX directory."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'trade_rebuild_tdx_bundle.py'
spec = importlib.util.spec_from_file_location('tdx_bundle_tool', SCRIPT)
bundle = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bundle
spec.loader.exec_module(bundle)


@pytest.fixture
def files(tmp_path):
    source = tmp_path / 'TDX'
    values = {'vipdoc/sh/lday/sh600000.day': b'a' * 64,
              'vipdoc/sz/lday/sz000001.day': b'b' * 64,
              'vipdoc/bj/lday/bj920001.day': b'c' * 32,
              'vipdoc/sh/minline/sh600000.lc1': b'm' * 320,
              'notes.txt': b'never upload unrelated files',
              **{'T0002/hq_cache/' + name: (name * 3).encode() for name in bundle.AUX}}
    for name, value in values.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return source, values


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def packed(files, tmp_path, minute=True):
    archive = tmp_path / 'bundle.zip'
    result = bundle.create_bundle(files[0], archive, include_minute=minute)
    return archive, result


def test_daily_full_zip64_roundtrip_readonly_source_known_aux_and_receipt(files, tmp_path, monkeypatch):
    # Exercise actual ZIP64 end records with small fixtures, not a 4 GiB allocation.
    monkeypatch.setattr(zipfile, 'ZIP64_LIMIT', 64)
    archive, created = packed(files, tmp_path)
    assert created['total_files'] == 8 and created['include_minute'] is True
    assert b'PK\x06\x06' in archive.read_bytes()
    assert created['archive_sha256'] == digest(archive)
    verified = bundle.verify_bundle(archive, created['archive_sha256'])
    assert verified['total_files'] == 8
    target = tmp_path / 'new-tdx'
    receipt = bundle.extract_new(archive, created['archive_sha256'], target)
    assert receipt['root_switch'] == 'manual_only'
    assert receipt['validation'] == 'transport_integrity_only'
    assert json.loads((target / bundle.RECEIPT).read_text()) == receipt
    for name, contents in files[1].items():
        assert (files[0] / name).read_bytes() == contents
        if name != 'notes.txt':
            assert (target / name.removeprefix('vipdoc/')).read_bytes() == contents
    assert not (target / 'notes.txt').exists()
    daily = bundle.create_bundle(files[0] / 'vipdoc', tmp_path / 'daily.zip')
    assert daily['total_files'] == 7 and daily['include_minute'] is False


def test_existing_archive_and_target_never_overwritten(files, tmp_path):
    archive, created = packed(files, tmp_path)
    previous = archive.read_bytes()
    with pytest.raises(bundle.BundleError): bundle.create_bundle(files[0], archive)
    assert archive.read_bytes() == previous
    existing = tmp_path / 'existing'
    existing.mkdir()
    (existing / 'keep').write_bytes(b'original')
    with pytest.raises(bundle.BundleError): bundle.extract_new(archive, created['archive_sha256'], existing)
    assert list(existing.iterdir()) == [existing / 'keep']
    assert (existing / 'keep').read_bytes() == b'original'
    with pytest.raises(bundle.BundleError): bundle.create_bundle(files[0], files[0] / 'bundle.zip')
    with pytest.raises(bundle.BundleError): bundle.create_bundle(files[0] / 'vipdoc' / '..', files[0] / 'bundle.zip')
    assert not (files[0] / 'bundle.zip').exists()


def test_source_read_change_rejects_and_leaves_no_completed_archive(files, tmp_path, monkeypatch):
    changed = files[0] / 'vipdoc/sh/lday/sh600000.day'
    original_open = Path.open
    class ConcurrentWriter:
        def __init__(self, stream): self.stream, self.changed = stream, False
        def __enter__(self): return self
        def __exit__(self, *_): self.stream.close()
        def fileno(self): return self.stream.fileno()
        def read(self, amount=-1):
            chunk = self.stream.read(amount)
            if chunk and not self.changed:
                self.changed = True
                with original_open(changed, 'ab') as writer: writer.write(b'external-change')
            return chunk
    def opening(path, mode='r', *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return ConcurrentWriter(stream) if path == changed and mode == 'rb' else stream
    monkeypatch.setattr(Path, 'open', opening)
    with pytest.raises(bundle.BundleError): bundle.create_bundle(files[0], tmp_path / 'no.zip')
    assert not (tmp_path / 'no.zip').exists()
    assert not list(tmp_path.glob('*.partial'))
    assert changed.read_bytes().endswith(b'external-change')


@pytest.mark.parametrize('budget', [bundle.Budget(max_file_bytes=32), bundle.Budget(max_total_bytes=100), bundle.Budget(max_files=2)])
def test_limits_enforced_before_output(files, tmp_path, budget):
    with pytest.raises(bundle.BundleError): bundle.create_bundle(files[0], tmp_path / 'no.zip', budget=budget)
    assert not (tmp_path / 'no.zip').exists()


def test_budget_hard_limits_and_links(files, tmp_path):
    for kwargs in ({'max_file_bytes': 513 * bundle.MIB}, {'max_total_bytes': 33 * bundle.GIB}, {'max_files': 100001}, {'max_files': True}):
        with pytest.raises(bundle.BundleError): bundle.Budget(**kwargs)
    original = files[0] / 'vipdoc/sh/lday/sh600000.day'
    os.link(original, tmp_path / 'hardlink.day')
    with pytest.raises(bundle.BundleError, match='non-linked'): bundle.create_bundle(files[0], tmp_path / 'no.zip')


def rewrite(archive, output, transform):
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(output, 'w') as target:
        for item in source.infolist():
            contents = source.read(item)
            for info, data in transform(item, contents):
                target.writestr(info, data)


@pytest.mark.parametrize('evil', ['../outside.day', '/sh/lday/sh600000.day', 'sh\\lday\\sh600000.day',
                                 'sh/lday/sz000001.day', 'T0002/hq_cache/secrets.txt', 'sh/lday/sh600000.day\x00bad'])
def test_untrusted_member_names_rejected_before_target(files, tmp_path, evil):
    archive, _ = packed(files, tmp_path)
    bad = tmp_path / 'bad.zip'
    rewrite(archive, bad, lambda info, content: [(evil if info.filename == 'sh/lday/sh600000.day' else info, content)])
    if '\\' in evil:
        # On Windows ZipInfo normalizes path separators at construction time.
        bad.write_bytes(bad.read_bytes().replace(b'sh/lday/sh600000.day', b'sh\\lday\\sh600000.day'))
    # ZipInfo writing strips NUL; patch the central name to retain the hidden suffix.
    if '\x00' in evil:
        # Explicitly exercise read-side orig_filename check with an equal-size raw name.
        raw = bad.read_bytes().replace(b'sh/lday/sh600000.day', b'sh/lday/sh600000\x00day')
        bad.write_bytes(raw)
    with pytest.raises((bundle.BundleError, zipfile.BadZipFile)):
        bundle.extract_new(bad, digest(bad), tmp_path / 'not-created')
    assert not (tmp_path / 'not-created').exists()


def test_duplicate_symlink_and_digest_corruption_rejected(files, tmp_path):
    archive, _ = packed(files, tmp_path)
    duplicate = tmp_path / 'duplicate.zip'
    with pytest.warns(UserWarning, match='Duplicate'):
        rewrite(archive, duplicate, lambda info, contents: [(info, contents)] * (2 if info.filename == 'sh/lday/sh600000.day' else 1))
    with pytest.raises(bundle.BundleError): bundle.verify_bundle(duplicate, digest(duplicate))
    linked = tmp_path / 'linked.zip'
    def link(info, contents):
        if info.filename == 'sh/lday/sh600000.day': info.external_attr = (stat.S_IFLNK | 0o777) << 16
        return [(info, contents)]
    rewrite(archive, linked, link)
    with pytest.raises(bundle.BundleError, match='Linked'): bundle.verify_bundle(linked, digest(linked))
    corrupt = tmp_path / 'corrupt.zip'
    rewrite(archive, corrupt, lambda info, contents: [(info, b'z' * len(contents) if info.filename.endswith('.lc1') else contents)])
    with pytest.raises(bundle.BundleError, match='digest'): bundle.verify_bundle(corrupt, digest(corrupt))
    with pytest.raises(bundle.BundleError, match='SHA-256'): bundle.verify_bundle(archive, '0' * 64)


def test_metadata_budget_checked_before_zipfile_allocation(files, tmp_path, monkeypatch):
    archive, _ = packed(files, tmp_path)
    contents = bytearray(archive.read_bytes())
    footer = len(contents) - 22
    struct.pack_into('<L', contents, footer + 12, bundle.MAX_CENTRAL + 1)
    archive.write_bytes(contents)
    expected = digest(archive)
    monkeypatch.setattr(zipfile, 'ZipFile', lambda *_args, **_kwargs: pytest.fail('oversized metadata allocated'))
    with pytest.raises(bundle.BundleError, match='central'): bundle.verify_bundle(archive, expected)


def test_failed_extract_leaves_incomplete_new_directory_without_receipt(files, tmp_path, monkeypatch):
    archive, created = packed(files, tmp_path)
    original = bundle._read_member
    count = 0
    def fail(archive, item, output=None):
        nonlocal count
        if output is not None:
            count += 1
            if count == 2: raise OSError('simulated disk full')
        return original(archive, item, output)
    monkeypatch.setattr(bundle, '_read_member', fail)
    target = tmp_path / 'incomplete'
    with pytest.raises(OSError, match='disk full'): bundle.extract_new(archive, created['archive_sha256'], target)
    assert target.exists() and not (target / bundle.RECEIPT).exists()
    with pytest.raises(bundle.BundleError): bundle.extract_new(archive, created['archive_sha256'], target)


def test_cli_json_success_and_failure_no_traceback(files, tmp_path):
    archive = tmp_path / 'cli.zip'
    created = subprocess.run([sys.executable, str(SCRIPT), 'create', '--source', str(files[0]), '--output', str(archive)], capture_output=True, text=True, encoding='utf-8')
    assert created.returncode == 0, created.stderr
    payload = json.loads(created.stdout)
    assert payload['ok'] and payload['result']['total_files'] == 7
    checked = subprocess.run([sys.executable, str(SCRIPT), 'verify', '--archive', str(archive), '--sha256', payload['result']['archive_sha256']], capture_output=True, text=True, encoding='utf-8')
    assert checked.returncode == 0 and json.loads(checked.stdout)['ok']
    failed = subprocess.run([sys.executable, str(SCRIPT), 'extract-new', '--archive', str(archive), '--sha256', '0' * 64, '--target', str(tmp_path / 'no')], capture_output=True, text=True, encoding='utf-8')
    assert failed.returncode != 0 and json.loads(failed.stderr)['ok'] is False
    assert 'Traceback' not in failed.stderr and not (tmp_path / 'no').exists()


def test_completion_publish_failure_cannot_leave_a_complete_marker(files, tmp_path, monkeypatch):
    archive, created = packed(files, tmp_path)
    target = tmp_path / 'incomplete-receipt'
    original = os.link
    def link(source, destination):
        if Path(destination).name == bundle.RECEIPT: raise OSError('simulated receipt publication failure')
        return original(source, destination)
    monkeypatch.setattr(os, 'link', link)
    with pytest.raises(OSError, match='receipt publication'): bundle.extract_new(archive, created['archive_sha256'], target)
    assert (target / 'sh/lday/sh600000.day').exists()
    assert not (target / bundle.RECEIPT).exists()
    assert not list(target.glob('*.partial'))


@pytest.mark.parametrize('mode', ['success', 'upload_failure', 'invalid_host'])
def test_powershell_wrapper_mocked_ssh_only_checks_exit_and_host_keys(files, tmp_path, mode):
    pwsh = shutil.which('pwsh')
    if pwsh is None: pytest.skip('PowerShell not installed')
    script = ROOT / 'deploy/scripts/sync-trade-rebuild-tdx.ps1'
    log = tmp_path / 'commands.jsonl'
    def ps(value): return "'" + str(value).replace("'", "''") + "'"
    # PowerShell functions replace ssh/scp before executing the wrapper. No network.
    harness = f"""
$global:LASTEXITCODE=0
$global:Uploaded=''
function global:scp {{
  @{{tool='scp';args=$args}} | ConvertTo-Json -Compress | Add-Content -LiteralPath {ps(log)}
  $global:Uploaded=$args[-2]
  $global:LASTEXITCODE={'9' if mode == 'upload_failure' else '0'}
}}
function global:ssh {{
  @{{tool='ssh';args=$args}} | ConvertTo-Json -Compress | Add-Content -LiteralPath {ps(log)}
  $global:LASTEXITCODE=0
  $command=$args[-1]
  if ($command.StartsWith('umask')) {{ '/tmp/trade-rebuild-tdx-abcdefgh1234'; return }}
  if ($command.StartsWith('rm')) {{ return }}
  $hash=[regex]::Match($command,"--sha256 '([a-f0-9]{{64}})'").Groups[1].Value
  $result=(& {ps(sys.executable)} {ps(SCRIPT)} verify --archive $global:Uploaded --sha256 $hash | ConvertFrom-Json)
  $result.result | Add-Member -NotePropertyName target -NotePropertyValue '/srv/trade/new-tdx'
  $result.result | Add-Member -NotePropertyName root_switch -NotePropertyValue 'manual_only'
  $result.result | Add-Member -NotePropertyName validation -NotePropertyValue 'transport_integrity_only'
  $result.result | Add-Member -NotePropertyName completed_at -NotePropertyValue '2026-09-26T00:00:00Z'
  $result | ConvertTo-Json -Depth 8 -Compress
}}
& {ps(script)} -Source {ps(files[0])} -LocalPython {ps(sys.executable)} -HostAlias {ps('-oProxyCommand=bad' if mode == 'invalid_host' else 'test-host')} -RemoteProject '/srv/project with space' -RemoteTarget '/srv/trade/new-tdx'
"""
    env = {**os.environ, 'TEMP': str(tmp_path), 'TMP': str(tmp_path)}
    result = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-Command', harness], capture_output=True, text=True, encoding='utf-8', env=env)
    assert result.returncode == (0 if mode == 'success' else 1), result.stdout + result.stderr
    commands = [json.loads(line) for line in log.read_text(encoding='utf-8-sig').splitlines()] if log.exists() else []
    if mode == 'invalid_host': assert commands == []
    for command in commands:
        assert 'StrictHostKeyChecking=yes' in command['args'] and 'BatchMode=yes' in command['args']
    if mode == 'success':
        assert 'Verified extraction completed' in result.stdout
        assert any("'/srv/project with space/scripts/trade_rebuild_tdx_bundle.py' extract-new" in str(command['args']) for command in commands)
    else:
        assert 'Verified extraction completed' not in result.stdout
        assert not any('extract-new' in str(command['args']) for command in commands)
