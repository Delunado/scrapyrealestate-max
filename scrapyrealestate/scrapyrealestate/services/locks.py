"""In-process per-search execution locks.

``SearchRunLock`` prevents two runs of the *same* saved search from
executing concurrently - a scheduled tick landing while a manual run (or
a previous, still-running tick) is in flight - while leaving independent
searches completely free to run at the same time. It is process-local and
held in memory: every entry point able to trigger a run within one process
(a future scheduler and manual "run now" action alike; see Phase 7) is
meant to share a single instance so the guarantee actually holds across
all of them.

This is deliberately not a blocking queue: a caller that cannot acquire
the lock gets ``SearchAlreadyRunningError`` immediately and decides for
itself whether to skip, log, or surface that to a user, rather than
waiting for a run of arbitrary duration to finish.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class SearchAlreadyRunningError(RuntimeError):
    """A run for this search is already in progress in this process."""

    def __init__(self, search_id: int) -> None:
        super().__init__(f"search {search_id} already has a run in progress")
        self.search_id = search_id


class SearchRunLock:
    """Tracks which searches currently have a run in progress."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._active: set[int] = set()

    @contextmanager
    def acquire(self, search_id: int) -> Iterator[None]:
        """Hold the lock for ``search_id`` for the life of the ``with`` block.

        Raises ``SearchAlreadyRunningError`` immediately, without blocking,
        if another run for this search is already in progress.
        """
        with self._guard:
            if search_id in self._active:
                raise SearchAlreadyRunningError(search_id)
            self._active.add(search_id)
        try:
            yield
        finally:
            with self._guard:
                self._active.discard(search_id)

    def is_running(self, search_id: int) -> bool:
        with self._guard:
            return search_id in self._active
