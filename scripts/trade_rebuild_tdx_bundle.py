#!/usr/bin/env python3
"""Streaming, standalone TDX transport. No app imports, network, or source writes.

Only integrity is certified; a valid bundle is not proof of market-data quality.
extract-new never selects TRADE_TDX_ROOT or overwrites an existing directory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import uuid
import zipfile

MIB = 1024 ** 2
GIB = 1024 ** 3
CHUNK = 256 * 1024
HARD_TOTAL = 32 * GIB
HARD_FILE = 512 * MIB
HARD_FILES = 100000
MAX_CENTRAL = 64 * MIB
MAX_MANIFEST = 32 * MIB
MANIFEST = 'tdx-bundle-manifest.json'
RECEIPT = 'tdx-bundle-complete.json'
FORMAT = 'trade-tdx-bundle-v1'
AUX = ('shs.tnf', 'szs.tnf', 'bjs.tnf', 'base.dbf')


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class Budget:
    max_total_bytes: int = 16 * GIB
    max_file_bytes: int = 100 * MIB
    max_files: int = 30000

    def __post_init__(self):
        for value, maximum, name in ((self.max_total_bytes, HARD_TOTAL, 'total bytes'),
                                     (self.max_file_bytes, HARD_FILE, 'file bytes'),
                                     (self.max_files, HARD_FILES, 'file count')):
            if type(value) is not int or not 1 <= value <= maximum:
                raise BundleError(f'Invalid {name} budget (hard limit {maximum})')


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _signature(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _opened_matches(opened, path_stat):
    # CPython/Windows stat and fstat can expose different ctime meanings. Keep
    # identity/length/mtime checks on handles; path-to-path checks retain ctime.
    return _signature(opened)[:4 if os.name == 'nt' else 5] == _signature(path_stat)[:4 if os.name == 'nt' else 5]


def _no_links(path: Path):
    """Do not resolve away symlinks or Windows junctions before checking them."""
    path = path.absolute()
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BundleError(f'Links and reparse points are forbidden: {part}')
    return path.resolve(strict=True)


def _regular(path: Path):
    _no_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BundleError(f'Expected one regular non-linked file: {path}')
    return info


def _unchanged(path, before):
    if _signature(_regular(path)) != _signature(before):
        raise BundleError(f'File changed while reading: {path}')


def _allowed(name: str, include_minute: bool = True):
    if not isinstance(name, str) or len(name) > 96 or '\\' in name:
        return False
    if name in {'T0002/hq_cache/' + value for value in AUX}:
        return True
    match = re.fullmatch(r'(sh|sz|bj)/(lday|minline)/((?:sh|sz|bj)?[0-9]{6})\.(day|lc1)', name)
    if not match:
        return False
    market, folder, code, suffix = match.groups()
    if not code[0].isdigit() and not code.startswith(market):
        return False
    return folder == 'lday' and suffix == 'day' or include_minute and folder == 'minline' and suffix == 'lc1'


def _sources(source: Path, include_minute: bool, budget: Budget):
    source = _no_links(source)
    if not source.is_dir():
        raise BundleError('TDX source must be a directory')
    vipdoc = source / 'vipdoc' if (source / 'vipdoc').exists() or (source / 'vipdoc').is_symlink() else source
    _no_links(vipdoc)
    aux_base = source.parent if source.name.lower() == 'vipdoc' else source
    rows, total = [], 0
    for market in ('sh', 'sz', 'bj'):
        market_root = vipdoc / market
        if market_root.exists() or market_root.is_symlink():
            _no_links(market_root)
        for folder, extension in (('lday', '.day'), ('minline', '.lc1')):
            if folder == 'minline' and not include_minute:
                continue
            directory = vipdoc / market / folder
            if not directory.exists() and not directory.is_symlink():
                continue
            _no_links(directory)
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() != extension:
                    continue
                relative = f'{market}/{folder}/{path.name}'
                if not _allowed(relative, include_minute):
                    raise BundleError(f'Unrecognized TDX filename: {relative}')
                info = _regular(path)
                rows.append((relative, path, info))
                total += info.st_size
                if info.st_size > budget.max_file_bytes or total > budget.max_total_bytes or len(rows) > budget.max_files:
                    raise BundleError('Source exceeds configured file/count/total budget')
    for parent in (aux_base / 'T0002', aux_base / 'T0002' / 'hq_cache'):
        if parent.exists() or parent.is_symlink():
            _no_links(parent)
    for name in AUX:
        path = aux_base / 'T0002' / 'hq_cache' / name
        if path.exists() or path.is_symlink():
            info = _regular(path)
            rows.append(('T0002/hq_cache/' + name, path, info))
            total += info.st_size
            if info.st_size > budget.max_file_bytes or total > budget.max_total_bytes or len(rows) > budget.max_files:
                raise BundleError('Source exceeds configured file/count/total budget')
    if not rows or not any('/lday/' in row[0] for row in rows):
        raise BundleError('Source has no selected daily TDX files')
    return rows


def _hash_stream(stream):
    digest, count = hashlib.sha256(), 0
    while chunk := stream.read(CHUNK):
        count += len(chunk)
        digest.update(chunk)
    return digest.hexdigest(), count


def _footer(stream, size: int, budget: Budget):
    """Bound central metadata before ZipFile allocates its in-memory index."""
    if size < 22 or size > HARD_TOTAL + 2 * MAX_CENTRAL:
        raise BundleError('Archive length exceeds hard budget or is invalid')
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    index = tail.rfind(b'PK\x05\x06')
    if index < 0 or len(tail) - index != 22:
        raise BundleError('Missing ZIP footer, comments, or trailing bytes')
    _, disk, cd_disk, disk_count, count, central_size, central_offset, comment = struct.unpack('<4s4H2LH', tail[index:])
    footer_at = size - len(tail) + index
    if disk or cd_disk or disk_count != count or comment:
        raise BundleError('Multi-volume ZIP is not supported')
    boundary = footer_at
    stream.seek(max(0, footer_at - 20))
    locator = stream.read(20)
    has_zip64 = len(locator) == 20 and locator[:4] == b'PK\x06\x07'
    requires_zip64 = count == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF
    if requires_zip64 and not has_zip64:
        raise BundleError('Missing ZIP64 locator')
    if has_zip64:
        legacy = count, central_size, central_offset
        stream.seek(footer_at - 20)
        locator = stream.read(20)
        if len(locator) != 20:
            raise BundleError('Invalid ZIP64 locator')
        sig, disk, offset, disks = struct.unpack('<4sLQL', locator)
        if sig != b'PK\x06\x07' or disk or disks != 1 or offset + 56 != footer_at - 20:
            raise BundleError('Invalid ZIP64 locator')
        stream.seek(offset)
        record = stream.read(56)
        if len(record) != 56:
            raise BundleError('Invalid ZIP64 footer')
        sig, record_size, _version, _needed, disk, cd_disk, disk_count, count, central_size, central_offset = struct.unpack('<4sQ2H2L4Q', record)
        if sig != b'PK\x06\x06' or record_size != 44 or disk or cd_disk or disk_count != count:
            raise BundleError('Invalid ZIP64 footer')
        if any(old != sentinel and old != current for old, current, sentinel in zip(legacy, (count, central_size, central_offset), (0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF))):
            raise BundleError('ZIP64 footer disagrees with legacy footer')
        boundary = offset
    if not 2 <= count <= budget.max_files + 1 or central_size > MAX_CENTRAL or central_offset + central_size != boundary:
        raise BundleError('ZIP central directory exceeds budget or has invalid bounds')
    return count


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError('Duplicate manifest JSON key')
        result[key] = value
    return result


def _metadata(archive: zipfile.ZipFile, expected_count: int, budget: Budget):
    infos = archive.infolist()
    if len(infos) != expected_count:
        raise BundleError('ZIP entry count does not match footer')
    names, total = set(), 0
    for item in infos:
        name = item.filename
        if item.orig_filename != name or name.casefold() in names or (name != MANIFEST and not _allowed(name)):
            raise BundleError('Duplicate or unrecognized archive path')
        names.add(name.casefold())
        kind = stat.S_IFMT(item.external_attr >> 16)
        if item.is_dir() or kind not in (0, stat.S_IFREG) or item.external_attr & 0x400 or item.flag_bits & 1:
            raise BundleError('Linked, special, encrypted, or directory ZIP entries are forbidden')
        if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise BundleError('Unsupported ZIP compression')
        if item.file_size > (MAX_MANIFEST if name == MANIFEST else budget.max_file_bytes):
            raise BundleError('Archive file exceeds configured budget')
        if name != MANIFEST:
            total += item.file_size
    if MANIFEST not in names or total > budget.max_total_bytes:
        raise BundleError('Missing manifest or total bytes exceed budget')
    with archive.open(MANIFEST) as source:
        raw = source.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise BundleError('Manifest exceeds budget')
    try:
        manifest = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BundleError('Invalid manifest JSON') from exc
    if not isinstance(manifest, dict) or set(manifest) != {'format', 'created_at', 'include_minute', 'files', 'total_files', 'total_bytes'}:
        raise BundleError('Invalid manifest fields')
    if manifest['format'] != FORMAT or type(manifest['include_minute']) is not bool or not isinstance(manifest['files'], list):
        raise BundleError('Unsupported manifest format')
    try:
        if not isinstance(manifest['created_at'], str) or len(manifest['created_at']) > 64 or datetime.fromisoformat(manifest['created_at']).tzinfo is None:
            raise ValueError()
    except ValueError as exc:
        raise BundleError('Invalid manifest creation time') from exc
    if type(manifest['total_files']) is not int or manifest['total_files'] != len(infos) - 1 or len(manifest['files']) != manifest['total_files']:
        raise BundleError('Manifest file count mismatch')
    if type(manifest['total_bytes']) is not int or manifest['total_bytes'] != total:
        raise BundleError('Manifest total length mismatch')
    declared = set()
    for item in manifest['files']:
        if not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256'}:
            raise BundleError('Invalid manifest file fields')
        name = item['path']
        if not _allowed(name, manifest['include_minute']) or name in declared or name.casefold() not in names:
            raise BundleError('Manifest contains invalid or duplicate paths')
        declared.add(name)
        if type(item['bytes']) is not int or item['bytes'] < 0 or archive.getinfo(name).file_size != item['bytes']:
            raise BundleError('Manifest file length mismatch')
        if not isinstance(item['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', item['sha256']):
            raise BundleError('Invalid manifest file digest')
    if not any('/lday/' in name for name in declared):
        raise BundleError('Manifest has no daily TDX files')
    return manifest, raw


def _read_member(archive, item, output=None):
    digest, count = hashlib.sha256(), 0
    with archive.open(item['path']) as source:
        while chunk := source.read(CHUNK):
            count += len(chunk)
            if count > item['bytes']:
                raise BundleError('Member expanded beyond declared length')
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
    if count != item['bytes'] or digest.hexdigest() != item['sha256']:
        raise BundleError(f'Member content digest mismatch: {item["path"]}')


@contextmanager
def _validated(path: Path, expected_sha256: str, budget: Budget):
    if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
        raise BundleError('A trusted archive SHA-256 is required')
    before = _regular(path)
    with path.open('rb') as source:
        if not _opened_matches(os.fstat(source.fileno()), before):
            raise BundleError('Archive changed before opening')
        count = _footer(source, before.st_size, budget)
        with zipfile.ZipFile(source) as archive:
            manifest, raw = _metadata(archive, count, budget)
            source.seek(0)
            digest, archive_size = _hash_stream(source)
            if digest != expected_sha256 or archive_size != before.st_size:
                raise BundleError('Archive SHA-256 mismatch')
            for item in manifest['files']:
                _read_member(archive, item)
            summary = {'format': FORMAT, 'archive_sha256': digest, 'archive_bytes': archive_size,
                       'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'total_files': manifest['total_files'],
                       'total_bytes': manifest['total_bytes'], 'include_minute': manifest['include_minute'],
                       'verified_at': _now()}
            yield archive, manifest, raw, summary
        if not _opened_matches(os.fstat(source.fileno()), before):
            raise BundleError('Archive changed while reading')
    _unchanged(path, before)


def verify_bundle(path: Path, expected_sha256: str, budget: Budget = Budget()):
    with _validated(path.absolute(), expected_sha256, budget) as (_archive, _manifest, _raw, summary):
        result = summary
    return result


def create_bundle(source: Path, output: Path, *, include_minute=False, budget: Budget = Budget()):
    source = _no_links(source)
    output = _no_links(output.absolute().parent) / output.name
    rows = _sources(source, include_minute, budget)
    _no_links(output.parent)
    if output.exists() or output.is_symlink():
        raise BundleError('Output archive already exists')
    if output.is_relative_to(source) or any(output.is_relative_to(path.parent) for _, path, _ in rows):
        raise BundleError('Output archive must be outside source directories')
    temporary = output.with_name('.' + output.name + '.' + uuid.uuid4().hex + '.partial')
    manifest = {'format': FORMAT, 'created_at': _now(), 'include_minute': bool(include_minute),
                'total_files': len(rows), 'total_bytes': sum(info.st_size for _, _, info in rows), 'files': []}
    try:
        with temporary.open('xb') as target:
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
                for relative, path, before in rows:
                    _unchanged(path, before)
                    digest, count = hashlib.sha256(), 0
                    info = zipfile.ZipInfo(relative)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    with path.open('rb') as content, archive.open(info, 'w', force_zip64=True) as entry:
                        if not _opened_matches(os.fstat(content.fileno()), before):
                            raise BundleError('Source replaced before opening')
                        while chunk := content.read(CHUNK):
                            count += len(chunk)
                            if count > before.st_size:
                                raise BundleError('Source grew while reading')
                            digest.update(chunk); entry.write(chunk)
                        if count != before.st_size or not _opened_matches(os.fstat(content.fileno()), before):
                            raise BundleError('Source changed while reading')
                    _unchanged(path, before)
                    manifest['files'].append({'path': relative, 'bytes': count, 'sha256': digest.hexdigest()})
                archive.writestr(MANIFEST, _json(manifest))
            target.flush(); os.fsync(target.fileno())
        with temporary.open('rb') as content:
            digest, _ = _hash_stream(content)
        result = verify_bundle(temporary, digest, budget)
        for _, path, before in rows:
            _unchanged(path, before)
        # Hard-link publication is atomic and cannot replace a pre-existing file.
        os.link(temporary, output)
        temporary.unlink()
        return {**result, 'archive': str(output)}
    finally:
        if temporary.exists():
            temporary.unlink()


def extract_new(path: Path, expected_sha256: str, target: Path, budget: Budget = Budget()):
    target = _no_links(target.absolute().parent) / target.name
    if target.exists() or target.is_symlink():
        raise BundleError('Extraction target must be a new directory')
    with _validated(path.absolute(), expected_sha256, budget) as (archive, manifest, raw, summary):
        # Only claim the new target after all archive bytes and file digests pass.
        # Failure after this point intentionally leaves an incomplete directory,
        # without a receipt; never recursively delete something the user may inspect.
        target.mkdir(mode=0o700)
        for item in manifest['files']:
            destination = target / item['path']
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _no_links(destination.parent)
            with destination.open('xb') as output:
                _read_member(archive, item, output)
                output.flush(); os.fsync(output.fileno())
        with (target / MANIFEST).open('xb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
    # This file is the sole completion marker, after archive stability checks.
    receipt = {**summary, 'target': str(target), 'completed_at': _now(),
               'root_switch': 'manual_only', 'validation': 'transport_integrity_only'}
    temporary = target / ('.completion-' + uuid.uuid4().hex + '.partial')
    try:
        with temporary.open('xb') as output:
            output.write(_json(receipt)); output.flush(); os.fsync(output.fileno())
        os.link(temporary, target / RECEIPT)
    finally:
        if temporary.exists():
            temporary.unlink()
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('create', 'verify', 'extract-new'):
        command = commands.add_parser(name)
        command.add_argument('--max-total-gib', type=int, default=16)
        command.add_argument('--max-file-mib', type=int, default=100)
        command.add_argument('--max-files', type=int, default=30000)
        if name == 'create':
            command.add_argument('--source', type=Path, required=True)
            command.add_argument('--output', type=Path, required=True)
            command.add_argument('--include-minute', action='store_true')
        else:
            command.add_argument('--archive', type=Path, required=True)
            command.add_argument('--sha256', required=True)
            if name == 'extract-new':
                command.add_argument('--target', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        budget = Budget(args.max_total_gib * GIB, args.max_file_mib * MIB, args.max_files)
        if args.command == 'create':
            result = create_bundle(args.source, args.output, include_minute=args.include_minute, budget=budget)
        elif args.command == 'verify':
            result = verify_bundle(args.archive, args.sha256, budget)
        else:
            result = extract_new(args.archive, args.sha256, args.target, budget)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError, EOFError, struct.error, OverflowError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    # Machine-readable receipts must survive non-UTF-8 Windows shell defaults.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
