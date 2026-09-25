"""本地文件夹选择（Windows）。

API 工作线程中不可直接调 GUI；通过独立子进程弹出对话框。
"""

import base64
import ctypes
import ctypes.wintypes as wintypes
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_PATH_RE = re.compile(r"^[A-Za-z]:[\\/].+")


def is_plausible_folder_path(value: str | None) -> bool:
    return _is_plausible_path(value)


def _is_plausible_path(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    if text.lower() in {"true", "false", "none"}:
        return False
    return bool(_PATH_RE.match(text) or text.startswith("\\\\"))


def _extract_path_from_text(text: str) -> str | None:
    """从可能混入 PowerShell 布尔输出的文本中提取文件夹路径。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for line in reversed(lines):
        if _is_plausible_path(line):
            return line.strip()
    joined = (text or "").strip()
    if _is_plausible_path(joined):
        return joined
    return None


def _powershell_encoded(script: str, *, hidden: bool = False) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = ["powershell.exe", "-NoProfile", "-STA"]
    if hidden:
        cmd += ["-WindowStyle", "Hidden"]
    cmd += ["-EncodedCommand", encoded]
    return cmd


def _subprocess_hide_window(*, hide: bool = True) -> dict:
    """Windows 下避免弹出黑色命令行窗口。"""
    if sys.platform != "win32" or not hide:
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": flags, "startupinfo": startupinfo}


def _foreground_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    return int(ctypes.windll.user32.GetForegroundWindow())


def _pick_folder_powershell(title: str) -> tuple[str | None, str | None]:
    """PowerShell FolderBrowserDialog。返回 (path, error)，双 None 表示用户取消。"""
    safe_title = title.replace("'", "''")
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(0, 0)
$owner.Opacity = 0
[void]$owner.Show()
[void]$owner.Activate()
[void]$owner.BringToFront()
[void]$owner.Focus()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '{safe_title}'
$dialog.ShowNewFolderButton = $true
$result = $dialog.ShowDialog($owner)
[void]$owner.Dispose()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    Write-Output $dialog.SelectedPath
}}
"""
    try:
        result = subprocess.run(
            _powershell_encoded(ps, hidden=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            **_subprocess_hide_window(hide=True),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"PowerShell 退出码 {result.returncode}"
        return None, err
    path = _extract_path_from_text(result.stdout or "")
    if not path:
        return None, None
    return path, None


def _pick_folder_win32(title: str) -> str | None:
    """SHBrowseForFolderW（独立子进程 CLI 或主线程）。"""
    if sys.platform != "win32":
        return None

    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32

    ole32.CoInitialize(None)
    try:
        BIF_RETURNONLYFSDIRS = 0x00000001
        BIF_NEWDIALOGSTYLE = 0x00000040

        class BROWSEINFOW(ctypes.Structure):
            _fields_ = [
                ("hwndOwner", wintypes.HWND),
                ("pidlRoot", ctypes.c_void_p),
                ("pszDisplayName", wintypes.LPWSTR),
                ("lpszTitle", wintypes.LPCWSTR),
                ("ulFlags", wintypes.UINT),
                ("lpfn", ctypes.c_void_p),
                ("lParam", ctypes.c_ssize_t),
                ("iImage", ctypes.c_int),
            ]

        display_name = ctypes.create_unicode_buffer(260)
        bi = BROWSEINFOW()
        bi.hwndOwner = _foreground_hwnd()
        bi.pidlRoot = 0
        bi.pszDisplayName = ctypes.cast(display_name, wintypes.LPWSTR)
        bi.lpszTitle = title
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
        bi.lpfn = 0
        bi.lParam = 0
        bi.iImage = 0

        if bi.hwndOwner:
            user32.SetForegroundWindow(bi.hwndOwner)
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return None

        path_buf = ctypes.create_unicode_buffer(1024)
        try:
            if not shell32.SHGetPathFromIDListW(pidl, path_buf):
                return None
            path = path_buf.value.strip() or None
            return path if _is_plausible_path(path) else None
        finally:
            ole32.CoTaskMemFree(pidl)
    finally:
        ole32.CoUninitialize()


def pick_folder_dialog(title: str = "选择数据存储目录") -> str | None:
    """在当前进程主线程弹出（供 CLI / 轻量子进程使用）。"""
    if sys.platform != "win32":
        return None
    try:
        path = _pick_folder_win32(title)
        if path:
            return path
    except Exception:  # noqa: BLE001
        path, _err = _pick_folder_powershell(title)
        return path
    return None


def _helper_cmd(title: str, out_file: str) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env["TMS_PICK_TITLE"] = title
    if getattr(sys, "frozen", False):
        return [sys.executable, "--pick-folder", out_file], env
    backend_dir = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(backend_dir)
    return [
        sys.executable,
        "-m",
        "app.services.folder_dialog",
        title,
        out_file,
    ], env


def _pick_folder_subprocess(title: str) -> tuple[str | None, str | None]:
    """独立子进程弹出对话框（API 工作线程不可直接调 GUI）。"""
    if getattr(sys, "frozen", False):
        cwd = str(Path(sys.executable).resolve().parent)
    else:
        cwd = str(Path(__file__).resolve().parents[2])

    cmd, env = _helper_cmd(title, "")
    fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="tms-pick-")
    os.close(fd)
    out_path_str = str(Path(out_path))
    if getattr(sys, "frozen", False):
        cmd[2] = out_path_str
    else:
        cmd[-1] = out_path_str

    try:
        result = subprocess.run(
            cmd,
            timeout=120,
            cwd=cwd,
            env=env,
            **_subprocess_hide_window(hide=False),
        )
        if result.returncode != 0:
            return None, f"子进程退出码 {result.returncode}"
        raw = Path(out_path_str).read_text(encoding="utf-8-sig")
        path = _extract_path_from_text(raw)
        return (path if path else None), None
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, str(exc)
    finally:
        Path(out_path_str).unlink(missing_ok=True)


def pick_folder(title: str = "选择数据存储目录") -> tuple[str | None, str | None]:
    """从 API 线程安全调用。返回 (path, error)，双 None 表示用户取消。"""
    if sys.platform != "win32":
        return None, "仅支持 Windows"

    sub_path, sub_err = _pick_folder_subprocess(title)
    if sub_path:
        return sub_path, None
    if sub_err:
        return None, sub_err
    return None, None


def run_pick_folder_cli() -> None:
    """CLI：弹出对话框并将结果写入 out_file。"""
    title = os.environ.get("TMS_PICK_TITLE", "").strip()
    out_file = ""
    if len(sys.argv) >= 2 and sys.argv[1] == "--pick-folder":
        out_file = sys.argv[2] if len(sys.argv) > 2 else ""
        if not title and len(sys.argv) > 3:
            title = sys.argv[3]
    else:
        title = title or (sys.argv[1] if len(sys.argv) > 1 else "选择数据存储目录")
        out_file = sys.argv[2] if len(sys.argv) > 2 else ""

    path = pick_folder_dialog(title or "选择数据存储目录") or ""
    if out_file:
        Path(out_file).write_text(path, encoding="utf-8")
    else:
        print(path)


if __name__ == "__main__":
    run_pick_folder_cli()
