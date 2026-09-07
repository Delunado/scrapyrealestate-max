"""Lifecycle-managed asynchronous duplicate candidate computation."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Sequence

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.duplicates import DuplicateCandidateRepository
from scrapyrealestate.services.duplicate_candidates import (
    DuplicateCandidateGenerator,
    DuplicateCandidateScorer,
)


logger = logging.getLogger(__name__)
_STOP = object()


class DuplicateCandidateWorker:
    """Process post-ingestion listing IDs on one bounded background worker."""

    def __init__(self, database: Database, *, queue_size: int = 100) -> None:
        if isinstance(queue_size, bool) or not isinstance(queue_size, int) or queue_size < 1:
            raise ValueError("queue_size must be a positive integer")
        self._database = database
        self._queue: queue.Queue[tuple[int, ...] | object] = queue.Queue(queue_size)
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._pending = 0

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            self._accepting = True
            self._thread = threading.Thread(
                target=self._run,
                name="duplicate-candidate-worker",
                daemon=False,
            )
            self._thread.start()

    def submit(self, listing_ids: Sequence[int]) -> bool:
        """Queue a batch without waiting; return false when stopped or saturated."""
        batch = tuple(dict.fromkeys(listing_ids))
        if not batch:
            return True
        with self._condition:
            if not self._accepting:
                return False
            try:
                self._queue.put_nowait(batch)
            except queue.Full:
                return False
            self._pending += 1
            return True

    def stop_accepting(self) -> None:
        with self._condition:
            self._accepting = False

    def shutdown(self, timeout: float | None = None) -> bool:
        """Drain accepted work and stop the worker within the caller's bound."""
        self.stop_accepting()
        with self._condition:
            thread = self._thread
        if thread is None:
            return True
        self._queue.put(_STOP)
        thread.join(timeout)
        return not thread.is_alive()

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self._pending == 0, timeout=timeout)

    def _run(self) -> None:
        with self._database.connection() as connection:
            repository = DuplicateCandidateRepository(connection)
            generator = DuplicateCandidateGenerator(repository)
            scorer = DuplicateCandidateScorer()
            while True:
                batch = self._queue.get()
                if batch is _STOP:
                    self._queue.task_done()
                    return
                try:
                    self._process_batch(batch, repository, generator, scorer)
                except Exception:  # candidate work must never affect search delivery
                    logger.warning("duplicate candidate processing failed")
                finally:
                    self._queue.task_done()
                    with self._condition:
                        self._pending -= 1
                        self._condition.notify_all()

    @staticmethod
    def _process_batch(
        batch: tuple[int, ...],
        repository: DuplicateCandidateRepository,
        generator: DuplicateCandidateGenerator,
        scorer: DuplicateCandidateScorer,
    ) -> None:
        generated = generator.generate(batch)
        for pair in generated.pairs:
            if repository.is_rejected_pair(pair.first.id, pair.second.id):
                continue
            result = scorer.score(pair)
            if not result.eligible:
                continue
            repository.upsert_pair(
                pair.first.id,
                pair.second.id,
                score=result.score,
                reasons=list(result.reasons),
            )
