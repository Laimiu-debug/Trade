"""Start the local rebuild on a free loopback port and open its UI."""
from __future__ import annotations

import argparse
import os
import socket
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(BACKEND))
from trade_app.platform.runtime import backend_root, configure_protocol_stdio, dispatch_worker, resource_root
BACKEND = backend_root()
FRONTEND = resource_root() / 'frontend' / 'dist-rebuild' / 'rebuild.html'
from trade_app.platform.launcher import ManagedLauncher, default_state_path, selected_directory, launch_or_reopen


def free_port(preferred: int) -> int:
    if not 1 <= preferred <= 65535:
        raise ValueError('port must be between 1 and 65535')
    for port in range(preferred, min(65535, preferred + 19) + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            try:
                server.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise RuntimeError('No free local port in the requested 20-port range')


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {'export', 'lab', 'sync'}:
        configure_protocol_stdio()
        if sys.argv[1] == 'export':
            from trade_app.reviews.export_cli import main as cli_main
        elif sys.argv[1] == 'lab':
            from trade_app.research.lab_cli import main as cli_main
        else:
            from trade_app.market.sync_cli import main as cli_main
        return cli_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description='Start the Trade rebuild locally')
    parser.add_argument('--port', type=int, default=8011)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-tray', action='store_true', help='Disable the Windows tray for headless automation')
    parser.add_argument('--state-file', type=Path, help='Independent launcher state file, useful for isolated installations')
    args = parser.parse_args()
    state_path = args.state_file.expanduser().resolve() if args.state_file else default_state_path()
    data_dir = selected_directory(args.data_dir, state_path)
    def start(on_ready):
        port = free_port(args.port)
        launcher = ManagedLauncher(BACKEND, port=port, state_path=state_path,
                                   no_browser=args.no_browser, on_ready=on_ready)
        if os.name == 'nt' and not args.no_tray:
            from trade_app.platform.windows_tray import WindowsTray
            launcher.desktop = WindowsTray(FRONTEND.parent / 'trade-icon.ico',
                                           on_open=launcher.open_ui, on_exit=launcher.request_exit)
        return launcher.run(data_dir)
    try:
        if not FRONTEND.is_file():
            raise RuntimeError('缺少应用页面，请重新构建或下载完整程序。源码构建命令：npm run build')
        data_dir.mkdir(parents=True, exist_ok=True)
        result = launch_or_reopen(data_dir, no_browser=args.no_browser, start=start, preferred_port=args.port)
        if result:
            raise RuntimeError(f'Trade 服务未能启动。请查看日志：\n{data_dir / "launch.log"}')
        return 0
    except Exception as exc:
        traceback.print_exc()
        try:
            with (data_dir / 'startup-error.log').open('a', encoding='utf-8') as log:
                traceback.print_exc(file=log)
        except OSError:
            pass
        if not args.no_browser and os.name == 'nt':
            from trade_app.platform.windows_tray import show_startup_error
            show_startup_error(f'{exc}\n\n启动日志：{data_dir / "startup-error.log"}')
        return 1


if __name__ == '__main__':
    if not dispatch_worker(sys.argv[1:]):
        raise SystemExit(main())
