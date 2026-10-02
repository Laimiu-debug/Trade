"""Explicit local desktop directory selection; selecting never mutates a directory."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from trade_app.platform.runtime import backend_root, child_environment, module_command
from trade_app.platform.types import TradeError

_dialog_lock = threading.Lock()


def supported(managed: bool) -> bool:
    desktop = sys.platform in ('win32', 'darwin') or bool(os.environ.get('DISPLAY'))
    return managed and desktop and importlib.util.find_spec('tkinter') is not None


def choose_directory(*, managed: bool, initial: Path) -> dict:
    if not supported(managed):
        raise TradeError('DIRECTORY_PICKER_UNAVAILABLE', '目录选择需在桌面启动器中使用；当前可填写完整路径', 409)
    if not _dialog_lock.acquire(blocking=False):
        raise TradeError('DIRECTORY_PICKER_BUSY', '已有目录选择窗口，请先完成或取消它', 409)
    try:
        environment = child_environment(extra_allowed=('DISPLAY', 'XAUTHORITY', 'HOME', 'USERPROFILE'))
        environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        response = subprocess.run(module_command('trade_app.platform.directory_picker'),
            input=json.dumps({'initial': str(initial)}).encode('utf-8'), capture_output=True,
            timeout=180, cwd=backend_root(), env=environment,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if response.returncode or len(response.stdout) > 32 * 1024:
            raise ValueError('dialog did not return a valid selection')
        value = json.loads(response.stdout)
        if value == {'cancelled': True, 'path': None}:
            return value
        path = value.get('path')
        if value.get('cancelled') is not False or not isinstance(path, str) or '\0' in path:
            raise ValueError('invalid selection')
        chosen = Path(path)
        if not chosen.is_absolute() or not chosen.is_dir():
            raise ValueError('selected directory no longer exists')
        return {'path': str(chosen.resolve()), 'cancelled': False}
    except subprocess.TimeoutExpired as exc:
        raise TradeError('DIRECTORY_PICKER_TIMEOUT', '目录选择窗口已超时关闭，可以重试或填写完整路径', 408) from exc
    except (OSError, ValueError, TypeError) as exc:
        raise TradeError('DIRECTORY_PICKER_FAILED', '无法读取桌面目录选择结果，请填写完整路径', 503) from exc
    finally:
        _dialog_lock.release()


def main():
    import tkinter as tk
    from tkinter import filedialog
    body = json.loads(sys.stdin.buffer.read(32 * 1024))
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(parent=root, title='选择 Trade 数据目录（此操作不写入数据）',
                                           initialdir=body['initial'], mustexist=True)
        print(json.dumps({'path': str(Path(selected).resolve()) if selected else None,
                          'cancelled': not bool(selected)}, ensure_ascii=False))
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
