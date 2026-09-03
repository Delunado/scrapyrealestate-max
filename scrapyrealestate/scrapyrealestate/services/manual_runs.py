"""Lifecycle-managed asynchronous launcher for manual web-triggered searches."""

from __future__ import annotations

import threading
from typing import Protocol

from scrapyrealestate.persistence.runs import SearchRunRecord, TriggerKind


class ManualRunTrigger(Protocol):
    def run_search(
        self,
        search_id: int,
        trigger: TriggerKind,
        *,
        on_run_created=None,
    ) -> object: ...


class ManualRunsStoppingError(RuntimeError):
    """The application is shutting down and accepts no new manual work."""


class ManualSearchRunLauncher:
    """Create a run promptly, then let its crawl continue off-request."""

    def __init__(self, trigger: ManualRunTrigger, *, startup_timeout: float = 5.0) -> None:
        if startup_timeout <= 0:
            raise ValueError("startup_timeout must be positive")
        self._trigger = trigger
        self._startup_timeout = startup_timeout
        self._condition = threading.Condition()
        self._threads: set[threading.Thread] = set()
        self._accepting = True

    def launch(self, search_id: int) -> int:
        """Return the new pending/running run ID without waiting for its crawl."""
        ready = threading.Event()
        state: dict[str, object] = {}

        def run_created(run: SearchRunRecord) -> None:
            state["run_id"] = run.id
            ready.set()

        def worker() -> None:
            try:
                self._trigger.run_search(
                    search_id,
                    TriggerKind.MANUAL,
                    on_run_created=run_created,
                )
            except BaseException as error:
                state["error"] = error
                ready.set()
            finally:
                with self._condition:
                    self._threads.discard(threading.current_thread())
                    self._condition.notify_all()

        with self._condition:
            if not self._accepting:
                raise ManualRunsStoppingError("manual runs are stopping")
            thread = threading.Thread(
                target=worker,
                name=f"manual-search-{search_id}",
                daemon=False,
            )
            self._threads.add(thread)
            thread.start()

        if not ready.wait(self._startup_timeout):
            raise RuntimeError("manual run did not create a status record in time")
        error = state.get("error")
        if isinstance(error, BaseException):
            raise error
        run_id = state.get("run_id")
        if not isinstance(run_id, int):
            raise RuntimeError("manual run did not create a status record")
        return run_id

    def stop_accepting(self) -> None:
        with self._condition:
            self._accepting = False

    def shutdown(self, timeout: float | None = None) -> bool:
        """Stop accepting work and wait a bounded time for launched runs."""
        self.stop_accepting()
        with self._condition:
            return self._condition.wait_for(lambda: not self._threads, timeout=timeout)
