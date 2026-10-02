"""Launcher-owned Uvicorn entry point; external uvicorn has no shutdown authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sys
import threading

import uvicorn

from trade_app.platform.lifecycle import LifecycleController, load_json


def main() -> int:
    context = json.loads(os.environ['TRADE_MANAGED_CONTEXT'])
    if context.get('stdin_lifeline') and sys.stdin.buffer.read(1) != b'R':
        return 1  # Owner died before the server was attached to its process tree.
    directory = Path(os.environ['TRADE_REBUILD_DATA_DIR']).resolve()
    port = int(os.environ['TRADE_REBUILD_PORT'])
    controller = LifecycleController(directory, managed=context)
    from trade_app.main import create_app
    app = create_app(directory, lifecycle=controller, auto_detect_tdx=True)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='info',
                                         timeout_graceful_shutdown=65))
    controller.set_exit_callback(lambda: setattr(server, 'should_exit', True))
    finished = threading.Event()

    def owner_lifetime() -> None:
        # Only the launcher owns the write end. Windows additionally uses an OS
        # Job to terminate the complete tree even if Python cannot run cleanup.
        while os.read(sys.stdin.fileno(), 1024):
            pass
        if not finished.is_set():
            server.should_exit = True

    if context.get('stdin_lifeline'):
        threading.Thread(target=owner_lifetime, daemon=True, name='trade-launcher-lifeline').start()

    def commands() -> None:
        path = Path(context['control_dir']) / 'command.json'
        while not finished.wait(0.2):
            message = load_json(path)
            if (message and secrets.compare_digest(str(message.get('nonce', '')), context['nonce'])
                    and message.get('instance_id') == context['instance_id'] and message.get('kind') == 'exit'):
                try:
                    controller.request('exit', {}, 'launcher-exit-' + context['instance_id'])
                except Exception:
                    pass  # An already-running switch owns the controlled shutdown.
                return

    thread = threading.Thread(target=commands, daemon=True, name='trade-launcher-control')
    thread.start()
    try:
        server.run()
    finally:
        finished.set()
        thread.join(timeout=1)
    if not server.started:
        return 1
    intent = load_json(Path(context['control_dir']) / 'intent.json') or {}
    return 42 if intent.get('kind') == 'switch' and intent.get('instance_id') == context['instance_id'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
