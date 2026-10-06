"""Build an isolated folder or single-file application, without touching user data."""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-frontend', action='store_true', help='Use an already verified dist-rebuild build')
    parser.add_argument('--onefile', action='store_true', help='Build a single executable (Windows/Linux)')
    args = parser.parse_args()
    if args.onefile and sys.platform == 'darwin':
        parser.error('Use the default app bundle on macOS')
    if importlib.util.find_spec('PyInstaller') is None:
        parser.error('PyInstaller is required in the build Python environment')
    if not (ROOT / 'frontend/public/data/stock-database.slim.json').is_file():
        parser.error('Missing checked-in offline stock library: frontend/public/data/stock-database.slim.json')
    if not args.skip_frontend:
        subprocess.run(['npm.cmd' if sys.platform == 'win32' else 'npm', 'run', 'build:rebuild'], cwd=ROOT / 'frontend', check=True)
    if not (ROOT / 'frontend/dist-rebuild/rebuild.html').is_file():
        parser.error('Missing rebuilt frontend')
    if not (ROOT / 'frontend/dist-rebuild/data/stock-database.slim.json').is_file():
        parser.error('Missing offline stock library in dist-rebuild; rebuild the frontend before packaging')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = ROOT / '.release-dist' / stamp
    output.mkdir(parents=True, exist_ok=False)
    work = ROOT / '.release-build' / stamp
    source = work / 'source'
    # Build one captured revision even when development continues during analysis.
    # Only these application paths enter the snapshot; databases and local settings
    # are never scanned or copied.
    for path in ('backend/trade_app', 'frontend/dist-rebuild'):
        shutil.copytree(ROOT / path, source / path, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for path in ('scripts/run_trade_rebuild.py', 'packaging/TradeRebuild.spec'):
        destination = source / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, destination)
    source_files = {path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(source.rglob('*')) if path.is_file()}
    (work / 'source-manifest.json').write_text(json.dumps(source_files, indent=2), encoding='utf-8')
    print('Captured build source:', source, flush=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(output), '--workpath', str(work / 'pyinstaller'),
                    str(source / 'packaging/TradeRebuild.spec')], cwd=source, check=True,
                    env={**os.environ, 'TRADE_REBUILD_BUNDLE_MODE': 'onefile' if args.onefile else 'onedir'})
    package_name = ('TradeRebuild.exe' if sys.platform == 'win32' else 'TradeRebuild') if args.onefile else (
        'TradeRebuild.app' if sys.platform == 'darwin' else 'TradeRebuild')
    package = output / package_name
    executable = package if args.onefile else package / (
        'Contents/MacOS/TradeRebuild' if sys.platform == 'darwin' else 'TradeRebuild.exe' if sys.platform == 'win32' else 'TradeRebuild')
    manifest = {'product': 'Trade rebuild', 'format': 'trade-rebuild-release-v1', 'built_at': stamp,
                'platform': sys.platform, 'python': sys.version, 'package_path': package.name,
                'bundle_mode': 'onefile' if args.onefile else 'onedir',
                'executable_path': executable.relative_to(output).as_posix(),
                'source_files': source_files, 'files': {}}
    for path in ([package] if args.onefile else sorted(package.rglob('*'))):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            name = path.name if args.onefile else path.relative_to(package).as_posix()
            manifest['files'][name] = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
    (output / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Built package:', package)
    print('Run TradeRebuild with --data-dir and --state-file pointing to isolated paths for release smoke tests.')


if __name__ == '__main__':
    main()
