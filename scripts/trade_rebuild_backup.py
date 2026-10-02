"""Offline backup validation and safe restore into a new data directory."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from trade_app.platform.backup import create_backup, restore_to_new_directory, verify_backup


parser = argparse.ArgumentParser(description=__doc__)
commands = parser.add_subparsers(dest='command', required=True)
export = commands.add_parser('export')
export.add_argument('data_dir', type=Path)
export.add_argument('archive', type=Path)
verify = commands.add_parser('verify')
verify.add_argument('archive', type=Path)
restore = commands.add_parser('restore')
restore.add_argument('archive', type=Path)
restore.add_argument('new_data_dir', type=Path)
args = parser.parse_args()
if args.command == 'export':
    if args.archive.exists():
        parser.error('archive already exists')
    args.archive.write_bytes(create_backup(args.data_dir))
    print('Created:', args.archive)
elif args.command == 'verify':
    print(verify_backup(args.archive.read_bytes()))
else:
    print(restore_to_new_directory(args.archive.read_bytes(), args.new_data_dir))
