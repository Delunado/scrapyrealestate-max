from datetime import datetime, timezone
from pathlib import Path

from scrapy.http import HtmlResponse, Request

from scrapyrealestate.bootstrap import build_application
from scrapyrealestate.domain.notification import NotificationEventType
from scrapyrealestate.domain.values import RunStatus
from scrapyrealestate.execution.contract import PortalRunResult
from scrapyrealestate.flask_server import WEB_CONTEXT_EXTENSION
from scrapyrealestate.notifiers.base import DeliveryResult
from scrapyrealestate.notifiers.registry import NotifierRegistry
from scrapyrealestate.persistence.notifications import (
    DeliveryStatus,
    NotificationProvider,
)
from scrapyrealestate.persistence.runs import TriggerKind
from scrapyrealestate.runtime import RuntimePaths
from scrapyrealestate.spiders.pisoscom_spider import PisoscomSpider


class _FixtureRunner:
    """Serve parsed fixture items through the production orchestration boundary."""

    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.accepting = True

    def run(self, request):
        now = datetime.now(timezone.utc)
        return PortalRunResult(
            portal=request.portal,
            status=RunStatus.SUCCESS,
            started_at=now,
            finished_at=now,
            items=tuple(self.items),
        )

    def stop_accepting(self) -> None:
        self.accepting = False

    def shutdown(self, _grace_seconds: float) -> bool:
        self.accepting = False
        return True


class _RecordingNotifier:
    def __init__(self, delivered) -> None:
        self.delivered = delivered

    def send(self, event):
        self.delivered.append(event)
        return DeliveryResult.delivered(f"fixture-{len(self.delivered)}")


def _fixture_items(html: str) -> list[dict]:
    spider = PisoscomSpider()
    spider.start_urls = "https://www.pisos.com/venta/pisos-madrid/"
    url = spider.start_urls
    response = HtmlResponse(
        url=url,
        request=Request(url=url),
        body=html.encode(),
        encoding="utf-8",
    )
    return [dict(item) for item in spider.parse(response)]


def _csrf(client) -> str:
    with client.session_transaction() as flask_session:
        flask_session["_csrf_token"] = "end-to-end-token"
    return "end-to-end-token"


def test_saved_search_fixture_runs_cover_persistence_delivery_and_web(
    tmp_path: Path, load_fixture
):
    items = _fixture_items(load_fixture("pisoscom/search_results.html"))
    runner = _FixtureRunner(items)
    delivered = []
    notifiers = NotifierRegistry()
    notifiers.register(
        NotificationProvider.WEBHOOK,
        lambda _channel: _RecordingNotifier(delivered),
    )
    paths = RuntimePaths((tmp_path / "data").resolve())
    runtime = build_application(
        runtime_paths=paths,
        spider_runner=runner,
        notifier_registry=notifiers,
    )

    try:
        context = runtime.app.extensions[WEB_CONTEXT_EXTENSION]
        client = runtime.app.test_client()
        token = _csrf(client)
        created = client.post(
            "/searches/new",
            data={
                "csrf_token": token,
                "name": "Madrid fixture homes",
                "transaction_type": "buy",
                "interval_minutes": "15",
                "enabled": "on",
                "location": "Madrid",
                "portal_pisoscom": "on",
                "action": "save",
            },
        )
        assert created.status_code == 302
        search = context.repositories.searches.list()[0]

        channel = context.repositories.notifications.create_channel(
            "Offline recorder",
            NotificationProvider.WEBHOOK,
            config={"endpoint_url": "https://notifications.invalid/fixture"},
            secret_config={"authorization": "end-to-end-secret"},
        )
        context.repositories.notifications.assign_channel(search.id, channel.id)

        first = context.services.search_trigger.run_search(
            search.id, TriggerKind.SCHEDULED
        )
        assert first.run.trigger is TriggerKind.SCHEDULED
        assert first.run.status.value == "success"
        assert first.run.counts.new == 2
        assert [event.event_type for event in delivered] == [
            NotificationEventType.NEW_LISTING,
            NotificationEventType.NEW_LISTING,
        ]

        changed = dict(items[0])
        changed["price"] = "300.000 EUR"
        runner.items = [changed, items[1]]
        second = context.services.search_trigger.run_search(
            search.id, TriggerKind.SCHEDULED
        )
        assert second.run.counts.changed == 1
        assert delivered[-1].event_type is NotificationEventType.PRICE_DROP

        connection = context.repositories.searches.connection
        listing = connection.execute(
            "SELECT id, price_euros FROM listings WHERE external_id = '12345678901'"
        ).fetchone()
        assert listing["price_euros"] == 300_000
        assert connection.execute(
            "SELECT count(*) FROM listing_price_history WHERE listing_id = ?",
            (listing["id"],),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM notification_events"
        ).fetchone()[0] == 3
        assert {
            row["status"]
            for row in connection.execute(
                "SELECT status FROM notification_delivery_attempts"
            ).fetchall()
        } == {DeliveryStatus.SUCCEEDED.value}

        listing_page = client.get(f"/listings/{listing['id']}")
        assert listing_page.status_code == 200
        body = listing_page.get_data(as_text=True)
        assert "Piso en calle de" in body
        assert "300.000" in body
        assert "325.000" in body
        assert "Madrid fixture homes" in body

        dashboard = client.get("/").get_data(as_text=True)
        assert "Madrid fixture homes" in dashboard
        assert "price_drop" in dashboard
        assert "end-to-end-secret" not in dashboard + body
    finally:
        assert runtime.close(grace_seconds=1)
