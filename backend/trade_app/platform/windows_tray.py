"""Native Windows notification icon owned by the launcher, with no GUI dependency."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import threading
import traceback

OPEN_COMMAND = 1001
EXIT_COMMAND = 1002
CALLBACK_MESSAGE = 0x8001
UPDATE_MESSAGE = 0x8002
DISPOSE_MESSAGE = 0x8003


def show_startup_error(message: str) -> None:
    if os.name == 'nt':
        from ctypes import wintypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        user.MessageBoxW.restype = ctypes.c_int
        user.MessageBoxW(None, message, 'Trade 启动失败', 0x10 | 0x10000)


def hide_owned_console() -> None:
    """Hide only a frozen app's private console, never a user's shared terminal."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel.GetConsoleWindow.restype = wintypes.HWND
    kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    kernel.GetConsoleProcessList.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    window = kernel.GetConsoleWindow()
    processes = (wintypes.DWORD * 16)()
    count = kernel.GetConsoleProcessList(processes, len(processes))
    if not window or not 0 < count <= len(processes):
        return
    for pid in processes[:count]:
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return
        try:
            path, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if (not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))
                    or os.path.normcase(path.value) != os.path.normcase(sys.executable)):
                return
        finally:
            kernel.CloseHandle(handle)
    user.ShowWindow(window, 0)


class WindowsTray:
    def __init__(self, icon: Path, *, on_open, on_exit):
        self.icon, self.on_open, self.on_exit = icon, on_open, on_exit
        self.window = None
        self._ready = threading.Event()
        self._thread = None
        self._error = None
        self._disposing = False
        self._stopping = False
        self._can_open = False
        self._tooltip = 'Trade · 正在启动'

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name='trade-windows-tray', daemon=True)
        self._thread.start()
        if not self._ready.wait(8):
            self.close()
            raise RuntimeError('Windows 托盘未能及时初始化')
        if self._error:
            raise RuntimeError(f'Windows 托盘初始化失败：{self._error}') from self._error
        hide_owned_console()

    def status(self, text: str, *, can_open: bool = False) -> None:
        self._tooltip, self._can_open = text[:127], can_open and not self._stopping
        if self.window:
            self._user.PostMessageW(self.window, UPDATE_MESSAGE, 0, 0)

    def _exit(self) -> None:
        if not self._stopping:
            self._stopping = True
            self.status('Trade · 正在退出并清理后台')
            self.on_exit()

    def close(self) -> None:
        self._disposing = True
        if self.window:
            self._user.PostMessageW(self.window, DISPOSE_MESSAGE, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            self._message_loop()
        except Exception as exc:
            self._error = exc
            traceback.print_exc()
            # Losing the only native exit control must not leave an invisible app.
            if not self._disposing:
                self.on_exit()
        finally:
            self._ready.set()

    def _message_loop(self) -> None:
        from ctypes import wintypes as wt
        user = self._user = ctypes.WinDLL('user32', use_last_error=True)
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [('style', wt.UINT), ('lpfnWndProc', wndproc_type), ('cbClsExtra', ctypes.c_int),
                        ('cbWndExtra', ctypes.c_int), ('hInstance', wt.HINSTANCE), ('hIcon', wt.HICON),
                        ('hCursor', wt.HANDLE), ('hbrBackground', wt.HBRUSH),
                        ('lpszMenuName', wt.LPCWSTR), ('lpszClassName', wt.LPCWSTR)]

        class Guid(ctypes.Structure):
            _fields_ = [('Data1', wt.DWORD), ('Data2', wt.WORD), ('Data3', wt.WORD), ('Data4', wt.BYTE * 8)]

        class NotifyData(ctypes.Structure):
            _fields_ = [('cbSize', wt.DWORD), ('hWnd', wt.HWND), ('uID', wt.UINT), ('uFlags', wt.UINT),
                        ('uCallbackMessage', wt.UINT), ('hIcon', wt.HICON), ('szTip', wt.WCHAR * 128),
                        ('dwState', wt.DWORD), ('dwStateMask', wt.DWORD), ('szInfo', wt.WCHAR * 256),
                        ('uVersion', wt.UINT), ('szInfoTitle', wt.WCHAR * 64), ('dwInfoFlags', wt.DWORD),
                        ('guidItem', Guid), ('hBalloonIcon', wt.HICON)]

        def bind(dll, name, args, result):
            function = getattr(dll, name)
            function.argtypes, function.restype = args, result
            return function

        bind(kernel, 'GetModuleHandleW', [wt.LPCWSTR], wt.HMODULE)
        bind(user, 'RegisterClassW', [ctypes.POINTER(WindowClass)], wt.ATOM)
        bind(user, 'UnregisterClassW', [wt.LPCWSTR, wt.HINSTANCE], wt.BOOL)
        bind(user, 'CreateWindowExW', [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
             ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID], wt.HWND)
        bind(user, 'DefWindowProcW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], ctypes.c_ssize_t)
        bind(user, 'PostMessageW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL)
        bind(user, 'DestroyWindow', [wt.HWND], wt.BOOL)
        bind(user, 'PostQuitMessage', [ctypes.c_int], None)
        bind(user, 'GetMessageW', [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT], wt.BOOL)
        bind(user, 'TranslateMessage', [ctypes.POINTER(wt.MSG)], wt.BOOL)
        bind(user, 'DispatchMessageW', [ctypes.POINTER(wt.MSG)], ctypes.c_ssize_t)
        bind(user, 'RegisterWindowMessageW', [wt.LPCWSTR], wt.UINT)
        bind(user, 'LoadImageW', [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT], wt.HANDLE)
        bind(user, 'DestroyIcon', [wt.HICON], wt.BOOL)
        bind(user, 'CreatePopupMenu', [], wt.HMENU)
        bind(user, 'AppendMenuW', [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR], wt.BOOL)
        bind(user, 'TrackPopupMenu', [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID], wt.UINT)
        bind(user, 'DestroyMenu', [wt.HMENU], wt.BOOL)
        bind(user, 'GetCursorPos', [ctypes.POINTER(wt.POINT)], wt.BOOL)
        bind(user, 'SetForegroundWindow', [wt.HWND], wt.BOOL)
        bind(shell, 'Shell_NotifyIconW', [wt.DWORD, ctypes.POINTER(NotifyData)], wt.BOOL)

        instance = kernel.GetModuleHandleW(None)
        class_name = f'TradeRebuild.Tray.{os.getpid()}'
        taskbar_created = user.RegisterWindowMessageW('TaskbarCreated')
        data = NotifyData()
        icon = None
        registered = False

        def notify(action):
            data.szTip = self._tooltip
            ok = shell.Shell_NotifyIconW(action, ctypes.byref(data))
            if action == 0 and ok:
                data.uVersion = 4
                shell.Shell_NotifyIconW(4, ctypes.byref(data))
            return ok

        def command(value):
            if value == EXIT_COMMAND:
                self._exit()
            elif value == OPEN_COMMAND and self._can_open and not self._stopping:
                # Browser startup must not block Windows' message pump.
                threading.Thread(target=self.on_open, name='trade-open-browser', daemon=True).start()

        def popup(hwnd):
            menu = user.CreatePopupMenu()
            if not menu:
                return
            try:
                user.AppendMenuW(menu, 0 if self._can_open else 1, OPEN_COMMAND, '打开 Trade')
                user.AppendMenuW(menu, 0x800, 0, None)
                user.AppendMenuW(menu, 1 if self._stopping else 0, EXIT_COMMAND,
                                 '正在退出…' if self._stopping else '退出 Trade（关闭全部后台）')
                point = wt.POINT()
                user.GetCursorPos(ctypes.byref(point))
                user.SetForegroundWindow(hwnd)
                selected = user.TrackPopupMenu(menu, 0x100 | 0x2, point.x, point.y, 0, hwnd, None)
                user.PostMessageW(hwnd, 0, 0, 0)
                shell.Shell_NotifyIconW(3, ctypes.byref(data))
                command(selected)
            finally:
                user.DestroyMenu(menu)

        @wndproc_type
        def procedure(hwnd, message, wparam, lparam):
            try:
                if message == CALLBACK_MESSAGE:
                    event = lparam & 0xffff
                    if event in (0x400, 0x401):  # NIN_SELECT / NIN_KEYSELECT, version 4
                        command(OPEN_COMMAND)
                    elif event == 0x7b:  # WM_CONTEXTMENU, mouse or keyboard
                        popup(hwnd)
                    return 0
                if message == 0x111:  # WM_COMMAND: also supports native automation/accessibility
                    command(wparam & 0xffff)
                    return 0
                if message == taskbar_created:
                    if not notify(0):
                        self._exit()
                    return 0
                if message == UPDATE_MESSAGE:
                    notify(1)
                    return 0
                if message in (0x10, 0x11):  # Close / session ending
                    self._exit()
                    return 1
                if message == 0x16 and wparam:  # WM_ENDSESSION
                    self._exit()
                    return 0
                if message == DISPOSE_MESSAGE:
                    user.DestroyWindow(hwnd)
                    return 0
                if message == 0x2:
                    if not self._disposing:
                        self._exit()
                    user.PostQuitMessage(0)
                    return 0
                return user.DefWindowProcW(hwnd, message, wparam, lparam)
            except Exception:
                traceback.print_exc()
                self._exit()
                return 0

        window_class = WindowClass(lpfnWndProc=procedure, hInstance=instance, lpszClassName=class_name)
        try:
            if not user.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            self.window = user.CreateWindowExW(0, class_name, 'Trade · 复盘工作台', 0,
                                              0, 0, 0, 0, None, None, instance, None)
            if not self.window:
                raise ctypes.WinError(ctypes.get_last_error())
            icon = user.LoadImageW(None, str(self.icon), 1, 32, 32, 0x10)
            if not icon:
                raise ctypes.WinError(ctypes.get_last_error())
            data.cbSize, data.hWnd, data.uID = ctypes.sizeof(data), self.window, 1
            data.uFlags, data.uCallbackMessage, data.hIcon = 1 | 2 | 4 | 0x80, CALLBACK_MESSAGE, icon
            if not notify(0):
                raise RuntimeError('无法向 Windows 通知区域添加 Trade 图标')
            self._ready.set()
            if self._disposing:
                return
            message = wt.MSG()
            while True:
                result = user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        finally:
            if data.hWnd:
                notify(2)
            if self.window:
                user.DestroyWindow(self.window)
                self.window = None
            if icon:
                user.DestroyIcon(icon)
            if registered:
                user.UnregisterClassW(class_name, instance)
