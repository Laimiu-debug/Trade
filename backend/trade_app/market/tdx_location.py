"""Bounded, read-only discovery of local TDX installations; paths are never saved."""
from __future__ import annotations

import os
import hashlib
from pathlib import Path
import time

from trade_app.platform.types import TradeError

MAX_FOLDERS = 512
SCAN_SECONDS = 3


def source_fingerprint(root: Path | None) -> str | None:
    if root is None:
        return None
    vipdoc = root / 'vipdoc' if (root / 'vipdoc').is_dir() else root
    return hashlib.sha256(os.path.normcase(str(vipdoc.resolve())).encode('utf-8')).hexdigest()


def inspect_location(raw: str) -> dict:
    if not raw.strip() or '\x00' in raw:
        raise TradeError('INVALID_TDX_DIRECTORY', '请选择通达信安装目录或 vipdoc 目录')
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise TradeError('INVALID_TDX_DIRECTORY', '请输入完整目录路径，或点击选择目录')
    try:
        path = path.resolve()
        if not path.is_dir():
            raise TradeError('TDX_DIRECTORY_NOT_FOUND', '目录不存在或无法访问，请重新选择')
        vipdoc = path / 'vipdoc' if (path / 'vipdoc').is_dir() else path
        markets = [market for market in ('sh', 'sz', 'bj') if (vipdoc / market / 'lday').is_dir()]
        if not markets:
            raise TradeError('INVALID_TDX_DIRECTORY', '未找到 sh/lday、sz/lday 或 bj/lday；请选择安装目录或 vipdoc 目录')
        return {'path': str(path), 'vipdoc': str(vipdoc), 'markets': markets}
    except (OSError, ValueError) as exc:
        raise TradeError('TDX_DIRECTORY_UNREADABLE', '无法读取所选目录，请检查路径和访问权限') from exc


def local_search_roots() -> list[Path]:
    if os.name != 'nt':
        return []  # Manual selection remains available on other platforms.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    drives = os.listdrives() if hasattr(os, 'listdrives') else [os.environ.get('SystemDrive', 'C:') + '\\']
    roots = [Path(drive) for drive in drives if kernel.GetDriveTypeW(drive) == 3]
    roots.extend(Path(value) for key in ('ProgramFiles', 'ProgramFiles(x86)') if (value := os.environ.get(key)))
    return list(dict.fromkeys(roots))


def scan_locations(roots: list[Path] | None = None) -> dict:
    """Inspect only fixed-drive top-level folders and Program Files, without recursion."""
    roots = local_search_roots() if roots is None else roots
    started = time.monotonic()
    found, checked, limited = {}, 0, False
    for root in roots:
        try:
            with os.scandir(root) as entries:
                # Common names first; the shallow fallback also finds broker-branded installs.
                candidates = [root / name for name in ('TDX', 'tdx', 'new_tdx', '通达信')]
                for entry in entries:
                    if len(candidates) >= MAX_FOLDERS:
                        limited = True
                        break
                    if entry.is_dir(follow_symlinks=False):
                        candidates.append(Path(entry.path))
            for candidate in candidates:
                if checked >= MAX_FOLDERS or time.monotonic() - started > SCAN_SECONDS:
                    limited = True
                    break
                checked += 1
                try:
                    if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
                        continue
                    item = inspect_location(str(candidate))
                    # Installation root and vipdoc refer to the same source.
                    found.setdefault(os.path.normcase(item['vipdoc']), item)
                except (TradeError, OSError):
                    continue
        except OSError:
            continue
        if checked >= MAX_FOLDERS or time.monotonic() - started > SCAN_SECONDS:
            limited = True
            break
    return {'candidates': sorted(found.values(), key=lambda item: item['path'].casefold()),
            'limited': limited, 'checked_folders': checked,
            'scope': '仅检查本机固定磁盘顶层目录和 Program Files；更深目录请手动选择。'}
