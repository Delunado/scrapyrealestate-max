import threading

import pytest

from scrapyrealestate.flask_server import create_app
from scrapyrealestate.wsgi import WaitressApplicationServer


class FakeDispatcher:
    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, cancel_pending=True, timeout=5):
        self.shutdown_calls.append((cancel_pending, timeout))
        return True


class FakeWaitressServer:
    def __init__(self):
        self.task_dispatcher = FakeDispatcher()
        self.running = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0

    def run(self):
        self.running.set()
        self.closed.wait(1)

    def close(self):
        self.close_calls += 1
        self.closed.set()


def test_waitress_adapter_serves_with_safe_production_defaults_and_stops():
    waitress_server = FakeWaitressServer()
    factory_calls = []

    def factory(app, **kwargs):
        factory_calls.append((app, kwargs))
        return waitress_server

    adapter = WaitressApplicationServer(
        host="127.0.0.1",
        port=9090,
        threads=3,
        shutdown_timeout=2.5,
        server_factory=factory,
    )
    app = create_app(config={"TESTING": True})
    worker = threading.Thread(target=adapter.serve, args=(app,))
    worker.start()
    assert waitress_server.running.wait(1)

    adapter.request_shutdown()
    worker.join(1)

    assert not worker.is_alive()
    assert factory_calls == [
        (
            app,
            {
                "host": "127.0.0.1",
                "port": 9090,
                "threads": 3,
                "expose_tracebacks": False,
            },
        )
    ]
    assert waitress_server.close_calls >= 1
    assert waitress_server.task_dispatcher.shutdown_calls == [(True, 2.5)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": " "},
        {"port": -1},
        {"port": 65536},
        {"threads": 0},
        {"shutdown_timeout": -1},
    ],
)
def test_waitress_adapter_rejects_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        WaitressApplicationServer(**kwargs)
