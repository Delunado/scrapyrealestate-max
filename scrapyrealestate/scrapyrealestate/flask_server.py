"""Persistent Flask application factory and legacy-import configuration routes.

The application is no longer a module-global singleton or a first-run subprocess.
The persistent bootstrap creates one long-lived instance and injects the
repositories and services it owns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
import secrets
from typing import TYPE_CHECKING, Any, Mapping

from flask import Blueprint, Flask, current_app, jsonify, render_template, request

from scrapyrealestate.atomic_files import atomic_write_json
from scrapyrealestate.runtime import RuntimePaths, get_runtime_paths

if TYPE_CHECKING:
    from scrapyrealestate.persistence.database import Database
    from scrapyrealestate.notifiers.registry import NotifierRegistry
    from scrapyrealestate.persistence.notifications import NotificationRepository
    from scrapyrealestate.persistence.runs import RunRepository
    from scrapyrealestate.persistence.searches import SearchRepository
    from scrapyrealestate.services.search_orchestration import (
        SearchOrchestrationService,
    )
    from scrapyrealestate.services.search_triggering import SearchTriggerService
    from scrapyrealestate.services.notification_configuration import (
        NotificationChannelConfigurationService,
    )
    from scrapyrealestate.services.manual_runs import ManualSearchRunLauncher
    from scrapyrealestate.portals.registry import PortalRegistry


WEB_CONTEXT_EXTENSION = "scrapyrealestate.web_context"
_PORTAL_FORM_FIELDS = (
    "url_idealista",
    "url_pisoscom",
    "url_fotocasa",
    "url_habitaclia",
    "url_yaencontre",
)


@dataclass(frozen=True, slots=True)
class WebRepositories:
    """Repositories made available to request handlers by the bootstrap layer."""

    searches: SearchRepository | None = None
    runs: RunRepository | None = None
    notifications: NotificationRepository | None = None


@dataclass(frozen=True, slots=True)
class WebServices:
    """Application services made available to request handlers."""

    orchestration: SearchOrchestrationService | None = None
    search_trigger: SearchTriggerService | None = None
    readiness_check: Callable[[], bool] | None = None
    portals: PortalRegistry | None = None
    notification_configuration: NotificationChannelConfigurationService | None = None
    schedule_changed: Callable[[], None] | None = None
    scheduler_running: Callable[[], bool] | None = None
    manual_runs: ManualSearchRunLauncher | None = None
    notifier_registry: NotifierRegistry | None = None


@dataclass(frozen=True, slots=True)
class WebApplicationContext:
    """Dependencies owned by one Flask application instance."""

    runtime_paths: RuntimePaths
    repositories: WebRepositories = field(default_factory=WebRepositories)
    services: WebServices = field(default_factory=WebServices)
    database: Database | None = None


routes = Blueprint("legacy_configuration", __name__)


def create_app(
    *,
    runtime_paths: RuntimePaths | None = None,
    repositories: WebRepositories | None = None,
    services: WebServices | None = None,
    database: Database | None = None,
    config: Mapping[str, Any] | None = None,
) -> Flask:
    """Create an independently configured, long-lived Flask application.

    Repositories and services are injected rather than constructed in web code.
    They remain optional for isolated route tests and the legacy import form; the
    persistent bootstrap supplies the SQLite-backed collaborators it owns.
    """
    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    if config is not None:
        app.config.from_mapping(config)

    app.extensions[WEB_CONTEXT_EXTENSION] = WebApplicationContext(
        runtime_paths=runtime_paths or get_runtime_paths(),
        repositories=repositories or WebRepositories(),
        services=services or WebServices(),
        database=database,
    )
    app.register_blueprint(routes)
    from scrapyrealestate.web_ui import csrf_token, ui

    app.register_blueprint(ui)
    app.context_processor(lambda: {"csrf_token": csrf_token})
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.filters["duration"] = _format_duration
    app.jinja_env.filters["interval"] = _format_interval

    @app.teardown_request
    def close_request_database(_error):
        from flask import g

        connection = g.pop("scrapyrealestate_connection", None)
        if connection is not None:
            connection.close()

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", title="No encontrado", message="El recurso solicitado no existe."), 404

    @app.errorhandler(400)
    def bad_request(_error):
        return render_template("error.html", title="Solicitud no válida", message="La solicitud no es válida o ha caducado."), 400

    return app


def get_web_context() -> WebApplicationContext:
    """Return the dependencies injected into the active Flask application."""
    return current_app.extensions[WEB_CONTEXT_EXTENSION]


@routes.get("/healthz")
def health():
    """Report that the web process can serve requests."""
    return jsonify(status="ok")


@routes.get("/readyz")
def readiness():
    """Report local application readiness without probing external portals."""
    readiness_check = get_web_context().services.readiness_check
    try:
        ready = readiness_check is None or readiness_check()
    except Exception:
        ready = False
    return jsonify(status="ready" if ready else "not_ready"), 200 if ready else 503


@routes.route("/legacy")
def legacy_configuration():
    return render_template("index.html")


@routes.route("/data", methods=["POST", "GET"])
def result():
    dict_form = request.form.to_dict()
    if dict_form:
        # Each portal accepts multiple URLs; preserve all repeated form values.
        for portal in _PORTAL_FORM_FIELDS:
            dict_form[portal] = request.form.getlist(portal)
        atomic_write_json(get_web_context().runtime_paths.config_file, dict_form)
        return render_template("info.html")

    return render_template("index.html")


def _format_duration(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f} s"


def _format_interval(value: int) -> str:
    if value % 3600 == 0:
        hours = value // 3600
        return f"{hours} h"
    return f"{value // 60} min"
