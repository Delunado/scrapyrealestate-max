"""Persistent Flask application factory and transitional configuration routes.

The legacy runtime still launches this module as a subprocess during first-run
configuration. The Flask application itself is no longer a module-global
singleton, though: the future bootstrap can create one long-lived instance and
inject the repositories and services it owns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from flask import Blueprint, Flask, current_app, render_template, request

from scrapyrealestate.atomic_files import atomic_write_json
from scrapyrealestate.runtime import RuntimePaths, get_runtime_paths

if TYPE_CHECKING:
    from scrapyrealestate.persistence.notifications import NotificationRepository
    from scrapyrealestate.persistence.runs import RunRepository
    from scrapyrealestate.persistence.searches import SearchRepository
    from scrapyrealestate.services.search_orchestration import (
        SearchOrchestrationService,
    )


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


@dataclass(frozen=True, slots=True)
class WebApplicationContext:
    """Dependencies owned by one Flask application instance."""

    runtime_paths: RuntimePaths
    repositories: WebRepositories = field(default_factory=WebRepositories)
    services: WebServices = field(default_factory=WebServices)


routes = Blueprint("legacy_configuration", __name__)


def create_app(
    *,
    runtime_paths: RuntimePaths | None = None,
    repositories: WebRepositories | None = None,
    services: WebServices | None = None,
    config: Mapping[str, Any] | None = None,
) -> Flask:
    """Create an independently configured, long-lived Flask application.

    Repositories and services are injected rather than constructed in web code.
    They are optional during the transitional legacy first-run flow because its
    two routes only write ``config.json``; the persistent bootstrap will supply
    the SQLite-backed collaborators it owns.
    """
    app = Flask(__name__, template_folder="templates")
    if config is not None:
        app.config.from_mapping(config)

    app.extensions[WEB_CONTEXT_EXTENSION] = WebApplicationContext(
        runtime_paths=runtime_paths or get_runtime_paths(),
        repositories=repositories or WebRepositories(),
        services=services or WebServices(),
    )
    app.register_blueprint(routes)
    return app


def get_web_context() -> WebApplicationContext:
    """Return the dependencies injected into the active Flask application."""
    return current_app.extensions[WEB_CONTEXT_EXTENSION]


@routes.route("/")
@routes.route("/home")
def home():
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


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8080)
