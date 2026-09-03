"""Server-rendered management UI routes and form parsing."""

from __future__ import annotations

import hmac
import sqlite3
from dataclasses import dataclass
from functools import wraps
from typing import Any

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    g,
)

from scrapyrealestate.domain.notification import NotificationEventType, NotificationPreferences
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, PropertyType, TransactionType
from scrapyrealestate.notifiers.base import NotifierConfigurationError
from scrapyrealestate.persistence.notifications import NotificationProvider
from scrapyrealestate.persistence.runs import TriggerKind
from scrapyrealestate.persistence.searches import (
    SearchConflictError,
    SearchNotFoundError,
    SearchPortalRecord,
)
from scrapyrealestate.portals.base import PortalRequestError
from scrapyrealestate.services.locks import SearchAlreadyRunningError
from scrapyrealestate.services.manual_runs import ManualRunsStoppingError


ui = Blueprint("ui", __name__)

_INTEGER_FILTERS = (
    "min_price_euros",
    "max_price_euros",
    "min_rooms",
    "max_rooms",
    "min_bathrooms",
    "max_bathrooms",
    "min_floor",
    "max_floor",
)
_FLOAT_FILTERS = ("min_area_sqm", "max_area_sqm", "max_price_per_sqm")
_BOOLEAN_FILTERS = ("elevator", "terrace", "garage")


@dataclass(slots=True)
class ParsedSearchForm:
    search: NormalizedSearch | None
    interval_seconds: int | None
    portals: tuple[SearchPortalRecord, ...]
    coverage: tuple[dict[str, Any], ...]
    errors: dict[str, list[str]]


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not isinstance(token, str) or not token:
        import secrets

        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def csrf_protected(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            abort(400, description="La solicitud no incluye un token CSRF válido.")
        return view(*args, **kwargs)

    return wrapped


@ui.get("/")
@ui.get("/home")
def dashboard():
    context = _context()
    repositories = _repositories()
    searches = repositories.searches.list() if repositories.searches else ()
    statuses = _statuses(searches)
    recent_events = (
        repositories.notifications.recent_events(limit=8)
        if repositories.notifications
        else ()
    )
    scheduler_running = (
        context.services.scheduler_running()
        if context.services.scheduler_running
        else False
    )
    degraded = []
    registry = context.services.portals
    if registry:
        degraded = [adapter.metadata for adapter in registry if adapter.metadata.degraded]
    return render_template(
        "dashboard.html",
        searches=searches,
        statuses=statuses,
        recent_events=recent_events,
        scheduler_running=scheduler_running,
        degraded_portals=degraded,
    )


@ui.get("/searches")
def search_list():
    searches = _searches().list()
    return render_template(
        "searches/list.html", searches=searches, statuses=_statuses(searches)
    )


@ui.get("/listings")
def listing_list():
    return _render_listing_list()


@ui.get("/listings/new")
def new_listings():
    return _render_listing_list(
        title="Anuncios nuevos",
        event_type=NotificationEventType.NEW_LISTING,
    )


@ui.get("/listings/price-drops")
def price_drop_listings():
    return _render_listing_list(
        title="Bajadas de precio",
        event_type=NotificationEventType.PRICE_DROP,
    )


@ui.get("/listings/reappearances")
def reappeared_listings():
    return _render_listing_list(
        title="Reapariciones",
        event_type=NotificationEventType.REAPPEARANCE,
    )


@ui.get("/listings/inactive")
def inactive_listings():
    return _render_listing_list(title="Anuncios inactivos", active=False)


def _render_listing_list(
    *,
    title: str = "Anuncios recientes",
    event_type: NotificationEventType | None = None,
    active: bool | None = None,
):
    try:
        page = _query_integer("page", default=1)
        search_id = _query_integer("search_id")
        portal = _query_enum("portal", PortalKey)
        selected_event_type = event_type or _query_enum(
            "event_type", NotificationEventType
        )
        selected_active = active if active is not None else _query_active()
        listings = _listings().recent(
            page=page,
            search_id=search_id,
            portal=portal,
            event_type=selected_event_type,
            active=selected_active,
        )
    except (TypeError, ValueError):
        abort(400)
    return render_template(
        "listings/list.html",
        listings=listings,
        searches=_searches().list(),
        portals=tuple(PortalKey),
        event_types=tuple(NotificationEventType),
        title=title,
        pagination_endpoint=request.endpoint,
        locked_event_type=event_type,
        locked_active=active,
        selected={
            "search_id": search_id,
            "portal": portal,
            "event_type": selected_event_type,
            "active": selected_active,
        },
    )


@ui.get("/listings/<int:listing_id>")
def listing_detail(listing_id: int):
    try:
        listing = _listings().get(listing_id)
    except LookupError:
        abort(404)
    prices = _repositories().prices
    if prices is None:
        abort(503)
    return render_template(
        "listings/detail.html",
        listing=listing,
        matches=_listings().matches_for_listing(listing_id),
        prices=prices.list_for_listing(listing_id),
    )


@ui.route("/searches/new", methods=["GET", "POST"])
def search_create():
    if request.method == "GET":
        return _render_search_form()
    _verify_csrf()
    parsed = _parse_search_form()
    if parsed.errors:
        return _render_search_form(parsed=parsed, status=400)
    if request.form.get("action") == "preview":
        flash("Cobertura calculada. Revisa cómo se aplicará cada filtro.", "info")
        return _render_search_form(parsed=parsed)
    try:
        record = _searches().create(
            parsed.search,
            interval_seconds=parsed.interval_seconds,
            portals=parsed.portals,
            enabled=_checked("enabled"),
        )
    except sqlite3.IntegrityError:
        parsed.errors.setdefault("name", []).append("Ya existe una búsqueda con ese nombre.")
        return _render_search_form(parsed=parsed, status=400)
    _schedule_changed()
    flash("Búsqueda creada.", "success")
    return redirect(url_for("ui.search_edit", search_id=record.id))


@ui.route("/searches/<int:search_id>/edit", methods=["GET", "POST"])
def search_edit(search_id: int):
    record = _get_search(search_id)
    if request.method == "GET":
        return _render_search_form(record=record)
    _verify_csrf()
    parsed = _parse_search_form(existing=record)
    if parsed.errors:
        return _render_search_form(record=record, parsed=parsed, status=400)
    if request.form.get("action") == "preview":
        flash("Cobertura calculada. Todavía no se ha guardado ningún cambio.", "info")
        return _render_search_form(record=record, parsed=parsed)
    try:
        expected_version = int(request.form.get("version", ""))
    except ValueError:
        abort(400, description="La versión de la búsqueda no es válida.")
    try:
        updated = _searches().update_configuration(
            search_id,
            parsed.search,
            interval_seconds=parsed.interval_seconds,
            portals=parsed.portals,
            expected_version=expected_version,
            enabled=_checked("enabled"),
        )
    except SearchConflictError:
        current = _get_search(search_id)
        parsed.errors.setdefault("form", []).append(
            "La búsqueda cambió en otra sesión. Revisa los datos actuales y vuelve a intentarlo."
        )
        return _render_search_form(record=current, parsed=parsed, status=409)
    except sqlite3.IntegrityError:
        parsed.errors.setdefault("name", []).append("El nombre o el intervalo no es válido.")
        return _render_search_form(record=record, parsed=parsed, status=400)
    _schedule_changed()
    flash("Búsqueda actualizada.", "success")
    return redirect(url_for("ui.search_edit", search_id=updated.id))


@ui.post("/searches/<int:search_id>/toggle")
@csrf_protected
def search_toggle(search_id: int):
    record = _get_search(search_id)
    _searches().set_enabled(search_id, not record.enabled)
    _schedule_changed()
    flash("Búsqueda habilitada." if not record.enabled else "Búsqueda deshabilitada.", "success")
    return redirect(url_for("ui.search_list"))


@ui.route("/searches/<int:search_id>/delete", methods=["GET", "POST"])
def search_delete(search_id: int):
    record = _get_search(search_id)
    if request.method == "GET":
        return render_template("searches/delete.html", search=record)
    _verify_csrf()
    _searches().delete(search_id)
    _schedule_changed()
    flash(
        "Búsqueda eliminada. Los anuncios normalizados y su historial global se conservan.",
        "success",
    )
    return redirect(url_for("ui.search_list"))


@ui.post("/searches/<int:search_id>/run")
@csrf_protected
def search_run(search_id: int):
    _get_search(search_id)
    trigger = _context().services.search_trigger
    launcher = _context().services.manual_runs
    if trigger is None and launcher is None:
        abort(503)
    try:
        if launcher is not None:
            run_id = launcher.launch(search_id)
        else:
            outcome = trigger.run_search(search_id, TriggerKind.MANUAL)
            run = getattr(outcome, "run", None)
            run_id = run.id if run is not None else None
    except SearchAlreadyRunningError:
        flash("Esta búsqueda ya se está ejecutando.", "warning")
        return redirect(url_for("ui.search_list")), 303
    except (ManualRunsStoppingError, RuntimeError):
        flash("No se puede iniciar la búsqueda mientras la aplicación se detiene.", "error")
        return redirect(url_for("ui.search_list")), 303
    if run_id is None:
        flash("No se pudo iniciar la ejecución.", "error")
        return redirect(url_for("ui.search_list")), 303
    flash("Ejecución manual registrada.", "success")
    return redirect(url_for("ui.run_status", run_id=run_id), code=303)


@ui.get("/runs/<int:run_id>")
def run_status(run_id: int):
    runs = _repositories().runs
    if runs is None:
        abort(503)
    try:
        run = runs.get_run(run_id)
    except LookupError:
        abort(404)
    return render_template(
        "runs/status.html", run=run, attempts=runs.attempts_for_run(run_id)
    )


@ui.get("/status/portals")
def portal_health():
    runs = _repositories().runs
    if runs is None:
        abort(503)
    summaries = {summary.portal: summary for summary in runs.portal_health()}
    registry = _context().services.portals
    metadata = tuple(adapter.metadata for adapter in registry) if registry else ()
    return render_template(
        "status/portals.html", summaries=summaries, portal_metadata=metadata
    )


@ui.get("/channels")
def channel_list():
    repository = _notifications()
    channels = repository.list_channels()
    tests = {channel.id: repository.latest_channel_test(channel.id) for channel in channels}
    return render_template("channels/list.html", channels=channels, tests=tests)


@ui.route("/channels/new", methods=["GET", "POST"])
def channel_create():
    if request.method == "GET":
        return _render_channel_form()
    _verify_csrf()
    provider, config, secrets, errors = _parse_channel_form()
    if errors:
        return _render_channel_form(errors=errors, status=400)
    try:
        channel = _channel_service().create(
            name=request.form.get("name", ""),
            provider=provider,
            config=config,
            secret_config=secrets,
            enabled=_checked("enabled"),
        )
    except (ValueError, NotifierConfigurationError, sqlite3.IntegrityError) as error:
        return _render_channel_form(errors={"form": [_safe_validation(error)]}, status=400)
    flash("Canal creado.", "success")
    return redirect(url_for("ui.channel_edit", channel_id=channel.id))


@ui.route("/channels/<int:channel_id>/edit", methods=["GET", "POST"])
def channel_edit(channel_id: int):
    channel = _get_channel(channel_id)
    if request.method == "GET":
        return _render_channel_form(channel=channel)
    _verify_csrf()
    provider, config, secrets, errors = _parse_channel_form(channel.provider)
    if errors:
        return _render_channel_form(channel=channel, errors=errors, status=400)
    try:
        updated = _channel_service().update(
            channel_id,
            name=request.form.get("name", ""),
            config=config,
            submitted_secrets=secrets,
            enabled=_checked("enabled"),
        )
    except (ValueError, NotifierConfigurationError, sqlite3.IntegrityError) as error:
        return _render_channel_form(
            channel=channel, errors={"form": [_safe_validation(error)]}, status=400
        )
    flash("Canal actualizado; los secretos vacíos conservaron su valor anterior.", "success")
    return redirect(url_for("ui.channel_edit", channel_id=updated.id))


@ui.post("/channels/<int:channel_id>/toggle")
@csrf_protected
def channel_toggle(channel_id: int):
    channel = _get_channel(channel_id)
    _notifications().update_channel(
        channel_id,
        name=channel.name,
        config=channel.config,
        enabled=not channel.enabled,
    )
    flash("Canal habilitado." if not channel.enabled else "Canal deshabilitado.", "success")
    return redirect(url_for("ui.channel_list"))


@ui.route("/channels/<int:channel_id>/delete", methods=["GET", "POST"])
def channel_delete(channel_id: int):
    channel = _get_channel(channel_id)
    if request.method == "GET":
        return render_template("channels/delete.html", channel=channel)
    _verify_csrf()
    _notifications().delete_channel(channel_id)
    flash("Canal eliminado.", "success")
    return redirect(url_for("ui.channel_list"))


@ui.post("/channels/<int:channel_id>/test")
@csrf_protected
def channel_test(channel_id: int):
    _get_channel(channel_id)
    result = _channel_service().send_test(channel_id)
    if result.success:
        flash("Notificación de prueba enviada.", "success")
    else:
        flash("La prueba falló. Revisa la configuración del canal.", "error")
    return redirect(url_for("ui.channel_list"))


@ui.route("/searches/<int:search_id>/notifications", methods=["GET", "POST"])
def search_notifications(search_id: int):
    search = _get_search(search_id)
    repository = _notifications()
    if request.method == "POST":
        _verify_csrf()
        try:
            channel_ids = frozenset(int(value) for value in request.form.getlist("channel_ids"))
        except ValueError:
            abort(400)
        preferences = NotificationPreferences(
            new_listing=_checked("new_listing"),
            price_drop=_checked("price_drop"),
            price_increase=_checked("price_increase"),
            reappearance=_checked("reappearance"),
        )
        try:
            repository.update_search_settings(
                search_id, channel_ids=channel_ids, preferences=preferences
            )
        except LookupError:
            abort(400)
        flash("Preferencias de notificación actualizadas.", "success")
        return redirect(url_for("ui.search_notifications", search_id=search_id))
    assigned = {channel.id for channel in repository.channels_for_search(search_id, enabled_only=False)}
    return render_template(
        "searches/notifications.html",
        search=search,
        channels=repository.list_channels(),
        assigned=assigned,
        preferences=repository.preferences_for_search(search_id),
    )


def _render_search_form(*, record=None, parsed=None, status: int = 200):
    registry = _context().services.portals
    adapters = tuple(registry) if registry else ()
    return (
        render_template(
            "searches/form.html",
            record=record,
            parsed=parsed,
            adapters=adapters,
            property_types=tuple(PropertyType),
            transaction_types=tuple(TransactionType),
            errors=parsed.errors if parsed else {},
        ),
        status,
    )


def _parse_search_form(*, existing=None) -> ParsedSearchForm:
    errors: dict[str, list[str]] = {}
    search = None
    interval = None
    try:
        interval = int(request.form.get("interval_minutes", "")) * 60
        if interval < 300:
            raise ValueError
    except ValueError:
        errors.setdefault("interval_minutes", []).append("El intervalo mínimo es de 5 minutos.")
    values: dict[str, Any] = {}
    for field in _INTEGER_FILTERS:
        values[field] = _number(field, int, errors)
    for field in _FLOAT_FILTERS:
        values[field] = _number(field, float, errors)
    for field in _BOOLEAN_FILTERS:
        raw = request.form.get(field, "")
        if raw not in {"", "true", "false"}:
            errors.setdefault(field, []).append("Selecciona sí, no o sin filtro.")
        values[field] = None if raw == "" else raw == "true"
    values["location"] = request.form.get("location", "")
    values["neighbourhood"] = request.form.get("neighbourhood", "")
    try:
        values["property_types"] = frozenset(
            PropertyType(value) for value in request.form.getlist("property_types")
        )
        filters = SearchFilters(**values)
        search = NormalizedSearch(
            name=request.form.get("name", ""),
            transaction_type=TransactionType(request.form.get("transaction_type", "")),
            filters=filters,
        )
    except (TypeError, ValueError) as error:
        errors.setdefault("form", []).append(str(error))

    portals = []
    coverage = []
    registry = _context().services.portals
    existing_portals = {item.portal: item for item in existing.portals} if existing else {}
    if registry and search:
        for adapter in registry:
            key = adapter.metadata.key
            if not _checked(f"portal_{key.value}"):
                continue
            raw_url = request.form.get(f"raw_url_{key.value}", "").strip() or None
            try:
                portal_request = (
                    adapter.build_request(raw_url)
                    if raw_url
                    else adapter.build_request_from_search(search)
                )
                if portal_request.transaction_type is not search.transaction_type:
                    raise PortalRequestError("La URL avanzada usa otro tipo de operación.")
            except PortalRequestError as error:
                errors.setdefault(f"portal_{key.value}", []).append(str(error))
            old = existing_portals.get(key)
            portals.append(
                SearchPortalRecord(
                    portal=key,
                    raw_url_override=raw_url,
                    adapter_options=old.adapter_options if old else {},
                    enabled=True,
                )
            )
            report = adapter.metadata.report_capabilities(search.filters)
            coverage.append(
                {
                    "metadata": adapter.metadata,
                    "remote": sorted(item.value for item in report.remote),
                    "local": sorted(item.value for item in report.local),
                    "unsupported": sorted(item.value for item in report.unsupported),
                }
            )
    if not portals:
        errors.setdefault("portals", []).append("Selecciona al menos un portal.")
    return ParsedSearchForm(search, interval, tuple(portals), tuple(coverage), errors)


def _render_channel_form(*, channel=None, errors=None, status: int = 200):
    return (
        render_template(
            "channels/form.html",
            channel=channel,
            providers=tuple(NotificationProvider),
            errors=errors or {},
        ),
        status,
    )


def _parse_channel_form(fixed_provider=None):
    errors: dict[str, list[str]] = {}
    try:
        provider = fixed_provider or NotificationProvider(request.form.get("provider", ""))
    except ValueError:
        errors.setdefault("provider", []).append("Selecciona un proveedor válido.")
        return None, {}, {}, errors
    try:
        timeout = float(request.form.get("timeout_seconds", "10"))
    except ValueError:
        errors.setdefault("timeout_seconds", []).append("El tiempo de espera debe ser numérico.")
        timeout = 10.0
    if provider is NotificationProvider.TELEGRAM:
        config = {"chat_id": request.form.get("chat_id", "")}
        secrets = {"bot_token": request.form.get("bot_token", "")}
    elif provider is NotificationProvider.NTFY:
        config = {
            "server_url": request.form.get("server_url", ""),
            "topic": request.form.get("topic", ""),
            "timeout_seconds": timeout,
        }
        secrets = {"access_token": request.form.get("access_token", "")}
    else:
        config = {
            "endpoint_url": request.form.get("endpoint_url", ""),
            "timeout_seconds": timeout,
        }
        secrets = {"authorization": request.form.get("authorization", "")}
    return provider, config, secrets, errors


def _number(field: str, converter, errors: dict[str, list[str]]):
    raw = request.form.get(field, "").strip()
    if not raw:
        return None
    try:
        return converter(raw)
    except ValueError:
        errors.setdefault(field, []).append("Introduce un número válido.")
        return None


def _statuses(searches):
    runs = _repositories().runs
    return {search.id: runs.latest_status(search.id) for search in searches} if runs else {}


def _context():
    from scrapyrealestate.flask_server import get_web_context

    return get_web_context()


def _searches():
    repository = _repositories().searches
    if repository is None:
        abort(503)
    return repository


def _notifications():
    repository = _repositories().notifications
    if repository is None:
        abort(503)
    return repository


def _listings():
    repository = _repositories().listings
    if repository is None:
        abort(503)
    return repository


def _channel_service():
    service = _context().services.notification_configuration
    if service is not None:
        return service
    registry = _context().services.notifier_registry
    if registry is None:
        abort(503)
    from scrapyrealestate.services.notification_configuration import (
        NotificationChannelConfigurationService,
    )

    return NotificationChannelConfigurationService(_notifications(), registry)


def _repositories():
    context = _context()
    if context.database is None:
        return context.repositories
    cached = g.get("scrapyrealestate_repositories")
    if cached is not None:
        return cached
    from scrapyrealestate.flask_server import WebRepositories
    from scrapyrealestate.persistence.listings import ListingQueryRepository
    from scrapyrealestate.persistence.notifications import NotificationRepository
    from scrapyrealestate.persistence.prices import PriceHistoryRepository
    from scrapyrealestate.persistence.runs import RunRepository
    from scrapyrealestate.persistence.searches import SearchRepository

    connection = context.database.connect(check_same_thread=False)
    g.scrapyrealestate_connection = connection
    repositories = WebRepositories(
        searches=SearchRepository(connection),
        runs=RunRepository(connection),
        notifications=NotificationRepository(connection),
        listings=ListingQueryRepository(connection),
        prices=PriceHistoryRepository(connection),
    )
    g.scrapyrealestate_repositories = repositories
    return repositories


def _get_search(search_id: int):
    try:
        return _searches().get(search_id)
    except SearchNotFoundError:
        abort(404)


def _get_channel(channel_id: int):
    try:
        return _notifications().get_channel(channel_id)
    except LookupError:
        abort(404)


def _schedule_changed() -> None:
    callback = _context().services.schedule_changed
    if callback:
        callback()


def _verify_csrf() -> None:
    expected = session.get("_csrf_token", "")
    supplied = request.form.get("csrf_token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, description="La solicitud no incluye un token CSRF válido.")


def _checked(name: str) -> bool:
    return request.form.get(name) == "on"


def _safe_validation(error: Exception) -> str:
    if isinstance(error, sqlite3.IntegrityError):
        return "El nombre ya existe o los datos no cumplen las restricciones."
    return str(error)


def _query_integer(name: str, *, default: int | None = None) -> int | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError
    return value


def _query_enum(name: str, enum_type):
    raw = request.args.get(name, "").strip()
    return enum_type(raw) if raw else None


def _query_active() -> bool | None:
    raw = request.args.get("active", "").strip()
    if not raw:
        return None
    if raw not in {"active", "inactive"}:
        raise ValueError
    return raw == "active"
