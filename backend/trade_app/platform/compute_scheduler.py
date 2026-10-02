"""Fair single-CPU dispatch, with domain operations injected by the application.

A quantum is non-preemptive: an ordinary backtest may consume its full 120-second
budget. Scans and parameter searches yield at a chunk/point boundary. This gate
is process-wide; the application's single-instance lock supplies process scope.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import threading

_CPU_GATE = threading.Lock()


class ComputeScheduler:
    def __init__(self, operations: Sequence[Callable[[], bool]]):
        if not operations:
            raise ValueError('At least one compute operation is required')
        self._operations = tuple(operations)
        self._next = 0

    def process_one(self) -> bool:
        # Never wait for CPU while holding a database write transaction.
        if not _CPU_GATE.acquire(blocking=False):
            return False
        try:
            for _ in self._operations:
                index = self._next
                self._next = (index + 1) % len(self._operations)
                if self._operations[index]():
                    return True
            return False
        finally:
            _CPU_GATE.release()
