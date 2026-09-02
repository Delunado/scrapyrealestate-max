"""Production WSGI server adapter for the single-process application."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

from flask import Flask


DEFAULT_WSGI_THREADS = 4
DEFAULT_WSGI_SHUTDOWN_TIMEOUT = 5.0


class WaitressTaskDispatcher(Protocol):
    def shutdown(self, cancel_pending: bool = True, timeout: float = 5) -> bool: ...


class WaitressServer(Protocol):
    task_dispatcher: WaitressTaskDispatcher

    def run(self) -> None: ...

    def close(self) -> None: ...


ServerFactory = Callable[..., WaitressServer]


class WaitressApplicationServer:
    """Serve Flask with Waitress and expose non-blocking lifecycle shutdown."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        *,
        threads: int = DEFAULT_WSGI_THREADS,
        shutdown_timeout: float = DEFAULT_WSGI_SHUTDOWN_TIMEOUT,
        server_factory: ServerFactory | None = None,
    ) -> None:
        if not host.strip():
            raise ValueError("WSGI host is required")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("WSGI port must be between 0 and 65535")
        if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
            raise ValueError("WSGI threads must be a positive integer")
        if shutdown_timeout < 0:
            raise ValueError("WSGI shutdown timeout cannot be negative")
        self._host = host.strip()
        self._port = port
        self._threads = threads
        self._shutdown_timeout = shutdown_timeout
        self._server_factory = server_factory or _create_waitress_server
        self._lock = threading.Lock()
        self._shutdown_requested = False
        self._server: WaitressServer | None = None

    def serve(self, app: Flask) -> None:
        server = self._server_factory(
            app,
            host=self._host,
            port=self._port,
            threads=self._threads,
            expose_tracebacks=False,
        )
        with self._lock:
            self._server = server
            shutdown_requested = self._shutdown_requested
        if shutdown_requested:
            server.close()
        try:
            if not shutdown_requested:
                server.run()
        finally:
            server.close()
            server.task_dispatcher.shutdown(
                cancel_pending=True,
                timeout=self._shutdown_timeout,
            )
            with self._lock:
                self._server = None

    def request_shutdown(self) -> None:
        """Stop accepting HTTP connections without blocking a signal handler."""
        with self._lock:
            self._shutdown_requested = True
            server = self._server
        if server is not None:
            server.close()


def _create_waitress_server(app: Flask, **kwargs: Any) -> WaitressServer:
    # Kept lazy so importing application modules never starts server machinery.
    from waitress.server import create_server

    return create_server(app, **kwargs)
