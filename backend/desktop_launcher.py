from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.main import app as api_app


def _resolve_frontend_dist() -> Path | None:
    candidates: list[Path] = []

    env_path = os.getenv("FINAL_TRADE_FRONTEND_DIST", "").strip()
    if env_path:
        candidates.append(Path(env_path))

    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                meipass / "frontend_dist",
                exe_dir / "frontend_dist",
                exe_dir / "frontend" / "dist",
            ]
        )

    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    candidates.extend(
        [
            repo_root / "frontend" / "dist",
            Path.cwd() / "frontend" / "dist",
            Path.cwd() / "dist",
        ]
    )

    for candidate in candidates:
        index_file = candidate / "index.html"
        if index_file.is_file():
            return candidate

    return None


def _safe_resolve(base_dir: Path, relative_path: str) -> Path | None:
    base_resolved = base_dir.resolve()
    target = (base_resolved / relative_path).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError:
        return None
    return target


def _attach_frontend_routes(frontend_dist: Path) -> None:
    index_file = frontend_dist / "index.html"
    if not index_file.is_file():
        return

    blocked_exact = {"api", "health", "openapi.json", "docs", "redoc"}
    blocked_prefix = ("api/", "docs/", "redoc/")

    @api_app.get("/", include_in_schema=False)
    def serve_root() -> FileResponse:
        return FileResponse(index_file)

    @api_app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        path = full_path.strip("/")
        if path in blocked_exact or path.startswith(blocked_prefix):
            raise HTTPException(status_code=404, detail="Not Found")

        if path:
            static_file = _safe_resolve(frontend_dist, path)
            if static_file and static_file.is_file():
                return FileResponse(static_file)

        return FileResponse(index_file)


def _kill_port_process(port: int) -> None:
    """Kill any process listening on the given TCP port (Windows only)."""
    if os.name != "nt":
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return

    pids: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        state = parts[3] if len(parts) >= 5 else ""
        # Match lines where local address ends with :<port> and state is LISTENING
        if f":{port}" in local_addr and state == "LISTENING":
            try:
                pids.add(int(parts[4]))
            except ValueError:
                continue

    current_pid = os.getpid()
    for pid in pids:
        if pid == current_pid:
            continue
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
            print(f"Killed process {pid} occupying port {port}.")
        except Exception as exc:
            print(f"Failed to kill process {pid}: {exc}")


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _find_port(host: str, preferred: int, span: int = 20) -> int:
    if _is_port_available(host, preferred):
        return preferred

    for port in range(preferred + 1, preferred + span + 1):
        if _is_port_available(host, port):
            return port

    # Nearby range exhausted — try well-separated alternative ranges
    for alt_base in (9000, 5500, 3000, 10000):
        for port in range(alt_base, alt_base + span + 1):
            if _is_port_available(host, port):
                return port

    print(f"Warning: could not find a free port, falling back to {preferred}")
    return preferred


def _open_browser_when_ready(url: str, health_url: str, timeout_sec: int = 30) -> None:
    def _worker() -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=2):
                    break
            except Exception:
                time.sleep(0.4)
        webbrowser.open(url)

    threading.Thread(target=_worker, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Final Trade desktop launcher")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    frontend_dist = _resolve_frontend_dist()
    if frontend_dist is None:
        print("Warning: frontend dist not found, only API endpoints will be available.")
    else:
        print(f"Frontend dist: {frontend_dist}")
        _attach_frontend_routes(frontend_dist)

    host = args.host
    if not _is_port_available(host, args.port):
        print(f"Port {args.port} is occupied, attempting to free it...")
        _kill_port_process(args.port)

    selected_port = _find_port(host, args.port)
    if selected_port != args.port:
        print(f"Port {args.port} is busy, fallback to {selected_port}.")

    base_url = f"http://{host}:{selected_port}"
    health_url = f"{base_url}/health"

    if not args.no_browser:
        _open_browser_when_ready(base_url, health_url)

    print(f"Starting Final Trade on {base_url}")
    uvicorn.run(
        api_app,
        host=host,
        port=selected_port,
        log_level="info",
        loop="asyncio",
        http="h11",
    )


if __name__ == "__main__":
    main()

