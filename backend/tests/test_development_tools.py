"""Exercise launcher/build failure boundaries without importing legacy applications."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_legacy_desktop_start_preserves_unrelated_listener_and_chooses_free_port(monkeypatch):
    tree = ast.parse((ROOT / 'backend/desktop_launcher.py').read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'_is_port_available', '_find_port', 'main'}]
    started = []

    def reject_kill(*_):
        pytest.fail('A launcher must never terminate the unrelated port owner')

    scope = {'argparse': argparse, 'os': os, 'socket': socket, 'sys': sys,
             '_resolve_frontend_dist': lambda: None, '_kill_port_process': reject_kill,
             'api_app': object(), 'uvicorn': SimpleNamespace(run=lambda _, **kw: started.append(kw))}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<launcher functions>', 'exec'), scope)
    with socket.socket() as unrelated:
        unrelated.bind(('127.0.0.1', 0))
        unrelated.listen()
        occupied = unrelated.getsockname()[1]
        monkeypatch.setattr(sys, 'argv', ['launcher', '--port', str(occupied), '--no-browser'])
        scope['main']()
        assert len(started) == 1 and started[0]['port'] != occupied
        with socket.create_connection(('127.0.0.1', occupied), timeout=2):
            connection, _ = unrelated.accept()
            connection.close()


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def powershell_functions(path, names, code):
    executable = shutil.which('pwsh') or shutil.which('powershell')
    if executable is None:
        pytest.skip('PowerShell is required for native script boundary checks')
    wanted = ','.join(ps_literal(name) for name in names)
    script = f"""
$ErrorActionPreference = 'Stop'
$tokens=$null; $diagnostics=$null
$tree=[Management.Automation.Language.Parser]::ParseFile({ps_literal(path)},[ref]$tokens,[ref]$diagnostics)
if ($diagnostics.Count) {{ throw 'PowerShell syntax error' }}
$functions=$tree.FindAll({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @({wanted})}},$true)
if ($functions.Count -ne {len(names)}) {{ throw 'Expected helper functions are absent' }}
$functions | ForEach-Object {{ Invoke-Expression $_.Extent.Text }}
{code}
"""
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_development_custom_ports_and_url_validation_leave_existing_service_alive():
    with socket.socket() as unrelated:
        unrelated.bind(('127.0.0.1', 0)); unrelated.listen()
        port = unrelated.getsockname()[1]
        output = powershell_functions(ROOT / 'start-dev.ps1', ['Find-FreePort', 'Resolve-LocalUrl'], f"""
$first=Find-FreePort {port}
$second=Find-FreePort $first -Excluded $first
$valid=(Resolve-LocalUrl 'http://localhost:43219').Port
$rejected=0
foreach($url in @('https://127.0.0.1:1234','http://example.com:1234','http://127.0.0.1:1234/?x=1')) {{
  try {{ Resolve-LocalUrl $url | Out-Null }} catch {{ $rejected++ }}
}}
@{{first=$first;second=$second;valid=$valid;rejected=$rejected}} | ConvertTo-Json -Compress
""")
        values = json.loads(output.strip())
        assert values['first'] != port and values['second'] != values['first']
        assert values['valid'] == 43219 and values['rejected'] == 3
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            connection, _ = unrelated.accept(); connection.close()


def test_build_command_failure_does_not_continue_using_stale_artifact(tmp_path):
    stale = tmp_path / 'stale.exe'; stale.write_bytes(b'prior-build')
    marker = tmp_path / 'published.txt'
    powershell_functions(ROOT / 'build-exe.ps1', ['Invoke-BuildCommand'], f"""
$failed=$false
try {{
  Invoke-BuildCommand {ps_literal(sys.executable)} @('-c','import sys; sys.exit(7)')
  Set-Content -LiteralPath {ps_literal(marker)} -Value 'wrongly-published'
}} catch {{ $failed=$true }}
if (-not $failed) {{ throw 'A failed native build was accepted' }}
""")
    assert stale.read_bytes() == b'prior-build' and not marker.exists()


def test_build_cleanup_rejects_paths_outside_its_workspace(tmp_path):
    boundary = tmp_path / 'workspace'; boundary.mkdir()
    outside = tmp_path / 'unrelated.txt'; outside.write_bytes(b'preserve')
    powershell_functions(ROOT / 'build-exe.ps1', ['Remove-BuildArtifact'], f"""
$rejected=$false
try {{ Remove-BuildArtifact {ps_literal(outside.resolve())} {ps_literal(boundary.resolve())} }}
catch {{ $rejected=$true }}
if (-not $rejected) {{ throw 'An unrelated path passed build cleanup' }}
""")
    assert outside.read_bytes() == b'preserve'
