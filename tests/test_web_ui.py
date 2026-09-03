from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scrapyrealestate.flask_server import WebRepositories, WebServices, create_app
from scrapyrealestate.domain.values import PortalKey, RunStatus
from scrapyrealestate.notifiers.base import DeliveryResult
from scrapyrealestate.notifiers.registry import NotifierRegistry
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import (
    NotificationProvider,
    NotificationRepository,
)
from scrapyrealestate.persistence.runs import (
    RunCounts,
    RunRepository,
    SearchRunStatus,
    TriggerKind,
)
from scrapyrealestate.persistence.searches import SearchRepository
from scrapyrealestate.portals import build_default_registry
from scrapyrealestate.runtime import RuntimePaths
from scrapyrealestate.services.locks import SearchAlreadyRunningError


class _Notifier:
    def send(self, _event):
        return DeliveryResult.delivered("safe-test-id")


class _Trigger:
    def __init__(self, runs):
        self.runs = runs
        self.conflict = False

    def run_search(self, search_id, trigger):
        if self.conflict:
            raise SearchAlreadyRunningError(search_id)
        now = datetime.now(timezone.utc)
        run = self.runs.create_run(search_id, trigger)
        self.runs.start_run(run.id, now)
        run = self.runs.finish_run(run.id, SearchRunStatus.SUCCESS, now)
        return SimpleNamespace(run=run)


@pytest.fixture
def web_app(tmp_path: Path):
    database = Database(tmp_path / "data" / "application.sqlite3")
    with database.connection(check_same_thread=False) as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        notifications = NotificationRepository(connection)
        registry = NotifierRegistry()
        for provider in NotificationProvider:
            registry.register(provider, lambda _channel: _Notifier())
        trigger = _Trigger(runs)
        schedule_changes = []
        app = create_app(
            runtime_paths=RuntimePaths((tmp_path / "data").resolve()),
            database=database,
            repositories=WebRepositories(searches, runs, notifications),
            services=WebServices(
                search_trigger=trigger,
                readiness_check=lambda: True,
                portals=build_default_registry(),
                schedule_changed=lambda: schedule_changes.append(True),
                scheduler_running=lambda: True,
                notifier_registry=registry,
            ),
            config={"TESTING": True, "SECRET_KEY": "test-only-key"},
        )
        yield app, connection, trigger, schedule_changes


def _csrf(client):
    with client.session_transaction() as flask_session:
        flask_session["_csrf_token"] = "known-token"
    return "known-token"


def _search_form(**overrides):
    values = {
        "csrf_token": "known-token",
        "name": "Madrid centro",
        "transaction_type": "rent",
        "interval_minutes": "15",
        "enabled": "on",
        "location": "Madrid",
        "portal_pisoscom": "on",
        "action": "save",
    }
    values.update(overrides)
    return values


def test_dashboard_and_search_crud_use_prg_and_csrf(web_app):
    app, connection, _trigger, schedule_changes = web_app
    client = app.test_client()
    token = _csrf(client)

    assert client.get("/").status_code == 200
    assert client.post("/searches/new", data=_search_form(csrf_token="bad")).status_code == 400

    response = client.post("/searches/new", data=_search_form(csrf_token=token))
    assert response.status_code == 302
    search = SearchRepository(connection).list()[0]
    assert response.headers["Location"].endswith(f"/searches/{search.id}/edit")
    assert search.search.filters.location == "Madrid"
    assert search.portals[0].portal.value == "pisoscom"
    assert schedule_changes == [True]

    listing = client.get("/searches")
    assert listing.status_code == 200
    assert "Madrid centro" in listing.get_data(as_text=True)

    response = client.post(
        f"/searches/{search.id}/toggle", data={"csrf_token": token}
    )
    assert response.status_code == 302
    assert SearchRepository(connection).get(search.id).enabled is False


def test_search_preview_reports_local_coverage_without_saving(web_app):
    app, connection, _trigger, _changes = web_app
    client = app.test_client()
    token = _csrf(client)

    response = client.post(
        "/searches/new",
        data=_search_form(
            csrf_token=token,
            action="preview",
            max_price_euros="1500",
        ),
    )

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Cobertura solicitada" in page
    assert "max_price_euros" in page
    assert SearchRepository(connection).list() == ()


def test_edit_rejects_stale_version_and_preserves_unknown_filter_keys(web_app):
    app, connection, _trigger, _changes = web_app
    client = app.test_client()
    token = _csrf(client)
    client.post("/searches/new", data=_search_form(csrf_token=token))
    record = SearchRepository(connection).list()[0]
    connection.execute(
        "UPDATE searches SET filters_json = json_set(filters_json, '$.future_filter', 'keep-me') WHERE id = ?",
        (record.id,),
    )

    stale = client.post(
        f"/searches/{record.id}/edit",
        data=_search_form(csrf_token=token, version="0", name="Changed"),
    )
    assert stale.status_code == 409
    assert "otra sesión" in stale.get_data(as_text=True)

    current = SearchRepository(connection).get(record.id)
    saved = client.post(
        f"/searches/{record.id}/edit",
        data=_search_form(
            csrf_token=token,
            version=str(current.version),
            name="Changed",
        ),
    )
    assert saved.status_code == 302
    assert connection.execute(
        "SELECT json_extract(filters_json, '$.future_filter') FROM searches WHERE id = ?",
        (record.id,),
    ).fetchone()[0] == "keep-me"


def test_raw_url_is_validated_by_selected_portal(web_app):
    app, connection, _trigger, _changes = web_app
    client = app.test_client()
    token = _csrf(client)

    response = client.post(
        "/searches/new",
        data=_search_form(
            csrf_token=token,
            raw_url_pisoscom="https://example.com/alquiler/pisos-madrid/",
        ),
    )

    assert response.status_code == 400
    assert "does not belong" in response.get_data(as_text=True)
    assert SearchRepository(connection).list() == ()


def test_manual_run_redirects_to_status_and_reports_conflict(web_app):
    app, connection, trigger, _changes = web_app
    client = app.test_client()
    token = _csrf(client)
    client.post("/searches/new", data=_search_form(csrf_token=token))
    search_id = SearchRepository(connection).list()[0].id

    response = client.post(
        f"/searches/{search_id}/run", data={"csrf_token": token}
    )
    assert response.status_code == 303
    status = client.get(response.headers["Location"])
    assert status.status_code == 200
    assert "Ejecución #" in status.get_data(as_text=True)

    trigger.conflict = True
    response = client.post(
        f"/searches/{search_id}/run", data={"csrf_token": token}
    )
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/searches")


def test_run_detail_shows_portal_counts_and_redacted_diagnostic(web_app):
    app, connection, _trigger, _changes = web_app
    client = app.test_client()
    token = _csrf(client)
    client.post("/searches/new", data=_search_form(csrf_token=token))
    search_id = SearchRepository(connection).list()[0].id
    runs = RunRepository(connection)
    started = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
    finished = datetime(2026, 9, 3, 10, 1, tzinfo=timezone.utc)
    run = runs.start_run(runs.create_run(search_id, TriggerKind.MANUAL).id, started)
    attempt = runs.start_attempt(run.id, PortalKey.FOTOCASA, started)
    counts = RunCounts(returned=7, matched=5, new=2, changed=1)
    runs.finish_attempt(
        attempt.id,
        RunStatus.PARSER_ERROR,
        finished,
        counts=counts,
        error_category="parser_error",
        redacted_diagnostic="safe parser summary",
    )
    runs.finish_run(
        run.id,
        SearchRunStatus.FAILED,
        finished,
        counts=counts,
        error_category="failed",
        redacted_diagnostic="safe run summary",
    )

    page = client.get(f"/runs/{run.id}").get_data(as_text=True)

    assert "fotocasa" in page
    assert "parser_error" in page
    assert "safe parser summary" in page
    assert "safe run summary" in page
    assert all(label in page for label in ("Devueltos", "Coincidentes", "Nuevos"))


def test_portal_health_page_distinguishes_operational_statuses(web_app):
    app, connection, _trigger, _changes = web_app
    client = app.test_client()
    token = _csrf(client)
    client.post("/searches/new", data=_search_form(csrf_token=token))
    search_id = SearchRepository(connection).list()[0].id
    runs = RunRepository(connection)
    started = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
    run = runs.start_run(runs.create_run(search_id, TriggerKind.MANUAL).id, started)
    for number, status in enumerate(
        (RunStatus.EMPTY, RunStatus.BLOCKED, RunStatus.PARSER_ERROR), start=1
    ):
        attempt = runs.start_attempt(
            run.id, PortalKey.PISOSCOM, started, attempt_number=number
        )
        runs.finish_attempt(attempt.id, status, started)

    response = client.get("/status/portals")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Vacíos correctos" in page
    assert "Bloqueados" in page
    assert "Error de parser" in page
    assert "No disponibles" in page
    assert "parser_error" in page


def test_recent_listings_page_filters_and_rejects_invalid_queries(web_app):
    app, connection, _trigger, _changes = web_app
    listing_id = connection.execute(
        """
        INSERT INTO listings (
            portal_key, external_id, canonical_url, transaction_type, title,
            price_euros, area_sqm, rooms,
            first_seen_at, last_seen_at
        ) VALUES ('pisoscom', 'recent-1', 'https://www.pisos.com/alquiler/piso-1/',
                  'rent', 'Piso reciente', 180000, 90, 3,
                  '2026-09-03T10:00:00Z', '2026-09-03T11:00:00Z') RETURNING id
        """
    ).fetchone()[0]
    assert listing_id > 0

    response = app.test_client().get("/listings?portal=pisoscom&active=active")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Piso reciente" in page
    assert "180.000 €" in page
    assert "2.000 €/m²" in page
    assert 'rel="noopener noreferrer"' in page
    search_id = connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('Historial', 'rent') RETURNING id"
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO search_listing_matches (
            search_id, listing_id, first_seen_at, last_seen_at
        ) VALUES (?, ?, '2026-09-03T10:00:00Z', '2026-09-03T11:00:00Z')
        """,
        (search_id, listing_id),
    )
    connection.executemany(
        """
        INSERT INTO listing_price_history (listing_id, price_euros, observed_at)
        VALUES (?, ?, ?)
        """,
        (
            (listing_id, 190000, "2026-09-03T10:00:00Z"),
            (listing_id, 180000, "2026-09-03T11:00:00Z"),
        ),
    )

    detail = app.test_client().get(f"/listings/{listing_id}")
    detail_page = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert "Búsquedas coincidentes" in detail_page
    assert "Historial" in detail_page
    assert detail_page.index("2026-09-03T10:00:00Z") < detail_page.rindex(
        "2026-09-03T11:00:00Z"
    )
    assert app.test_client().get("/listings?page=0").status_code == 400
    assert app.test_client().get("/listings/999").status_code == 404


def test_dedicated_listing_views_lock_their_event_or_state_filter(web_app):
    app, connection, _trigger, _changes = web_app
    search_id = connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('Vistas', 'buy') RETURNING id"
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (?, 900)",
        (search_id,),
    )
    new_id = connection.execute(
        """
        INSERT INTO listings (
            portal_key, external_id, transaction_type, title, first_seen_at, last_seen_at
        ) VALUES ('pisoscom', 'new-view', 'buy', 'Anuncio nuevo',
                  '2026-09-03T10:00:00Z', '2026-09-03T12:00:00Z') RETURNING id
        """
    ).fetchone()[0]
    inactive_id = connection.execute(
        """
        INSERT INTO listings (
            portal_key, external_id, transaction_type, title, first_seen_at,
            last_seen_at, active
        ) VALUES ('fotocasa', 'inactive-view', 'buy', 'Anuncio retirado',
                  '2026-09-03T09:00:00Z', '2026-09-03T11:00:00Z', 0) RETURNING id
        """
    ).fetchone()[0]
    connection.executemany(
        """
        INSERT INTO notification_events (
            search_id, listing_id, event_type, deduplication_key, occurred_at
        ) VALUES (?, ?, ?, ?, '2026-09-03T12:00:00Z')
        """,
        (
            (search_id, new_id, "new_listing", "new:view"),
            (search_id, inactive_id, "price_drop", "drop:view"),
            (search_id, new_id, "reappearance", "reappearance:view"),
        ),
    )

    client = app.test_client()
    assert "Anuncio nuevo" in client.get(
        "/listings/new?event_type=price_drop"
    ).get_data(as_text=True)
    assert "Anuncio retirado" in client.get(
        "/listings/price-drops"
    ).get_data(as_text=True)
    assert "Anuncio nuevo" in client.get(
        "/listings/reappearances"
    ).get_data(as_text=True)
    inactive_page = client.get("/listings/inactive").get_data(as_text=True)
    assert "Anuncio retirado" in inactive_page
    assert "Anuncio nuevo" not in inactive_page


def test_listing_web_pagination_preserves_combined_filters_and_order(web_app):
    app, connection, _trigger, _changes = web_app
    search_id = connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('Paginada', 'buy') RETURNING id"
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (?, 900)",
        (search_id,),
    )
    for number in range(26):
        timestamp = f"2026-09-03T10:00:{number:02d}Z"
        listing_id = connection.execute(
            """
            INSERT INTO listings (
                portal_key, external_id, transaction_type, title,
                first_seen_at, last_seen_at
            ) VALUES ('pisoscom', ?, 'buy', ?, ?, ?) RETURNING id
            """,
            (f"page-{number}", f"Paginado {number:02d}", timestamp, timestamp),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO search_listing_matches (
                search_id, listing_id, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?)
            """,
            (search_id, listing_id, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO notification_events (
                search_id, listing_id, event_type, deduplication_key, occurred_at
            ) VALUES (?, ?, 'new_listing', ?, ?)
            """,
            (search_id, listing_id, f"page:event:{number}", timestamp),
        )

    query = (
        f"search_id={search_id}&portal=pisoscom&event_type=new_listing&active=active"
    )
    first_page = app.test_client().get(f"/listings?{query}").get_data(as_text=True)

    assert "Página 1 de 2" in first_page
    assert "Paginado 25" in first_page
    assert "Paginado 00" not in first_page
    assert f"search_id={search_id}" in first_page
    assert "portal=pisoscom" in first_page
    assert "event_type=new_listing" in first_page
    assert "active=active" in first_page

    second_page = app.test_client().get(
        f"/listings?page=2&{query}"
    ).get_data(as_text=True)
    assert "Paginado 00" in second_page
    assert "Paginado 01" not in second_page


def test_channel_crud_masks_secrets_and_records_safe_test(web_app):
    app, connection, _trigger, _changes = web_app
    client = app.test_client()
    token = _csrf(client)
    secret = "12345:super-secret-token"

    response = client.post(
        "/channels/new",
        data={
            "csrf_token": token,
            "name": "Telegram personal",
            "provider": "telegram",
            "chat_id": "-100123",
            "bot_token": secret,
            "enabled": "on",
        },
    )
    assert response.status_code == 302
    channel = NotificationRepository(connection).list_channels()[0]
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert secret not in page
    assert "super-secret-token" not in page

    response = client.post(
        f"/channels/{channel.id}/test", data={"csrf_token": token}
    )
    assert response.status_code == 302
    test = NotificationRepository(connection).latest_channel_test(channel.id)
    assert test.success is True
    assert secret not in repr(test)

    response = client.post(
        f"/channels/{channel.id}/edit",
        data={
            "csrf_token": token,
            "name": "Telegram renamed",
            "provider": "telegram",
            "chat_id": "-100456",
            "bot_token": "",
            "enabled": "on",
        },
    )
    assert response.status_code == 302
    delivery = NotificationRepository(connection).delivery_channel(channel.id)
    assert delivery.secret_config["bot_token"] == secret


def test_notification_assignments_and_preferences_are_saved(web_app):
    app, connection, _trigger, _changes = web_app
    client = app.test_client()
    token = _csrf(client)
    client.post("/searches/new", data=_search_form(csrf_token=token))
    search_id = SearchRepository(connection).list()[0].id
    channel_id = NotificationRepository(connection).create_channel(
        "Webhook", NotificationProvider.WEBHOOK
    ).id

    response = client.post(
        f"/searches/{search_id}/notifications",
        data={
            "csrf_token": token,
            "channel_ids": str(channel_id),
            "new_listing": "on",
            "reappearance": "on",
        },
    )

    assert response.status_code == 302
    repository = NotificationRepository(connection)
    assert [item.id for item in repository.channels_for_search(search_id)] == [channel_id]
    preferences = repository.preferences_for_search(search_id)
    assert preferences.new_listing is True
    assert preferences.price_drop is False
    assert preferences.reappearance is True


@pytest.mark.parametrize(
    "path",
    ["/searches/999/edit", "/channels/999/edit", "/runs/999"],
)
def test_missing_management_records_return_safe_404(web_app, path):
    app, _connection, _trigger, _changes = web_app
    response = app.test_client().get(path)
    assert response.status_code == 404
    assert "no existe" in response.get_data(as_text=True)
