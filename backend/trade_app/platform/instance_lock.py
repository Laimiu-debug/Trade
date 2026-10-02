from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path
from typing import BinaryIO


class InstanceLock:
    def __init__(self, data_dir: Path, filename: str = '.trade-instance.lock') -> None:
        self._path = data_dir / filename
        self._file: BinaryIO | None = None
        self._published_instance: str | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        file = self._path.open("a+b")
        try:
            file.seek(0)
            if file.read(1) == b"":
                file.seek(0)
                file.write(b"0")
                file.flush()
            file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            file.close()
            raise RuntimeError(f"Trade data directory is already in use: {self._path.parent}") from exc
        self._file = file

    def publish(self, *, instance_id: str, port: int, launcher_pid: int | None = None) -> None:
        """Publish discovery only while holding the actual database-directory lock."""
        if self._file is None or self._path.name != '.trade-instance.lock':
            raise RuntimeError('Running instance must own the data directory lock')
        metadata = {'instance_id': instance_id, 'port': port, 'server_pid': os.getpid(),
                    'data_dir': str(self._path.parent.resolve())}
        if launcher_pid is not None:
            metadata['launcher_pid'] = launcher_pid
        path = self._path.parent / '.trade-running.json'
        fd, temporary = tempfile.mkstemp(prefix='.trade-instance-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(metadata, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            self._published_instance = instance_id
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def release(self) -> None:
        file = self._file
        if file is None:
            return
        if self._published_instance:
            path = self._path.parent / '.trade-running.json'
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('instance_id') == self._published_instance:
                    path.unlink()
            except (OSError, ValueError, AttributeError):
                pass  # Stale metadata never replaces the OS lock or health check.
            self._published_instance = None
        file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        file.close()
        self._file = None
