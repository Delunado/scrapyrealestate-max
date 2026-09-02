import signal
import sqlite3
import threading
from pathlib import Path

import pytest

from scrapyrealestate.bootstrap import build_application
from scrapyrealestate.flask_server import WEB_CONTEXT_EXTENSION
from scrapyrealestate.runtime import RuntimePaths


class SignalServer:
    def __init__(self, signum):
        self.signum = signum
        self.served = threading.Event()
        self.shutdown_requested = threading.Event()

    def serve(self, app):
        self.served.set()
        signal.raise_signal(self.signum)
        assert self.shutdown_requested.wait(1)

    def request_shutdown(self):
        self.shutdown_requested.set()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_process_signal_stops_web_scheduler_and_closes_database(
    tmp_path: Path, signum
):
    runtime = build_application(
        runtime_paths=RuntimePaths((tmp_path / "data").resolve())
    )
    context = runtime.app.extensions[WEB_CONTEXT_EXTENSION]
    connection = context.repositories.searches.connection
    previous = signal.getsignal(signum)
    server = SignalServer(signum)

    runtime.run(server)

    assert server.served.is_set()
    assert server.shutdown_requested.is_set()
    assert runtime.scheduler.is_running is False
    assert signal.getsignal(signum) is previous
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError as error:
        assert "closed" in str(error)
    else:
        raise AssertionError("bootstrap connection was not closed")
