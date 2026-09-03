import threading
from types import SimpleNamespace

import pytest

from scrapyrealestate.services.locks import SearchAlreadyRunningError
from scrapyrealestate.services.manual_runs import (
    ManualRunsStoppingError,
    ManualSearchRunLauncher,
)


class _BlockingTrigger:
    def __init__(self):
        self.release = threading.Event()

    def run_search(self, search_id, _trigger, *, on_run_created=None):
        on_run_created(SimpleNamespace(id=search_id + 100))
        self.release.wait(5)


def test_launcher_returns_after_record_creation_while_work_continues():
    trigger = _BlockingTrigger()
    launcher = ManualSearchRunLauncher(trigger)

    assert launcher.launch(7) == 107
    assert launcher.shutdown(timeout=0.01) is False

    trigger.release.set()
    assert launcher.shutdown(timeout=1) is True


def test_launcher_surfaces_overlap_before_redirecting():
    class ConflictTrigger:
        def run_search(self, search_id, _trigger, *, on_run_created=None):
            raise SearchAlreadyRunningError(search_id)

    launcher = ManualSearchRunLauncher(ConflictTrigger())

    with pytest.raises(SearchAlreadyRunningError):
        launcher.launch(9)


def test_stopping_launcher_rejects_new_work():
    launcher = ManualSearchRunLauncher(_BlockingTrigger())
    launcher.stop_accepting()

    with pytest.raises(ManualRunsStoppingError):
        launcher.launch(1)
