import threading

import pytest

from scrapyrealestate.services.locks import SearchAlreadyRunningError, SearchRunLock


def test_second_acquire_of_the_same_search_is_rejected():
    lock = SearchRunLock()

    with lock.acquire(1):
        assert lock.is_running(1) is True
        with pytest.raises(SearchAlreadyRunningError) as excinfo:
            with lock.acquire(1):
                pass
        assert excinfo.value.search_id == 1

    assert lock.is_running(1) is False


def test_lock_is_released_after_an_exception_inside_the_block():
    lock = SearchRunLock()

    with pytest.raises(RuntimeError):
        with lock.acquire(1):
            raise RuntimeError("boom")

    assert lock.is_running(1) is False
    with lock.acquire(1):
        pass


def test_independent_searches_do_not_block_each_other():
    lock = SearchRunLock()
    entered = threading.Event()
    release = threading.Event()

    def hold_search_one():
        with lock.acquire(1):
            entered.set()
            release.wait(timeout=5)

    worker = threading.Thread(target=hold_search_one)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        # A different search acquires immediately, without waiting for
        # search 1's holder to finish.
        with lock.acquire(2):
            assert lock.is_running(1) is True
            assert lock.is_running(2) is True
    finally:
        release.set()
        worker.join(timeout=5)

    assert lock.is_running(1) is False
