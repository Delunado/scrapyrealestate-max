import json
import sys
from pathlib import Path

from scrapyrealestate.bootstrap import build_application
from scrapyrealestate.domain.notification import NotificationEventType
from scrapyrealestate.execution.runner import SpiderRunner
from scrapyrealestate.flask_server import WEB_CONTEXT_EXTENSION
from scrapyrealestate.notifiers.base import DeliveryResult
from scrapyrealestate.notifiers.registry import NotifierRegistry
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.notifications import (
    DeliveryStatus,
    NotificationProvider,
)
from scrapyrealestate.persistence.runs import SearchRunStatus, TriggerKind
from scrapyrealestate.runtime import RuntimePaths


FAKE_SPIDER = Path(__file__).parent / "fixtures" / "execution" / "fake_spider.py"


class RecordingNotifier:
    def __init__(self, events):
        self.events = events

    def send(self, event):
        self.events.append(event)
        return DeliveryResult.delivered("integration-delivery")


class ImmediateServer:
    def __init__(self):
        self.shutdown_requested = False

    def serve(self, app):
        assert app.test_client().get("/readyz").status_code == 200

    def request_shutdown(self):
        self.shutdown_requested = True


def test_cutover_integrates_migration_scrape_notification_and_lifecycle(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "data").resolve())
    paths.ensure_data_dir()
    paths.config_file.write_text(
        json.dumps(
            {
                "scrapy_rs_name": "Cutover",
                "time_update": "600",
                "url_pisoscom": "https://www.pisos.com/alquiler/pisos-madrid/",
                "telegram_chatuserID": "123",
                "telegram_bot_token": "integration-secret",
            }
        ),
        encoding="utf-8",
    )
    delivered = []
    notifiers = NotifierRegistry()
    notifiers.register(
        NotificationProvider.TELEGRAM,
        lambda channel: RecordingNotifier(delivered),
    )
    runner = SpiderRunner(
        working_directory=tmp_path,
        build_command=lambda request: [
            sys.executable,
            str(FAKE_SPIDER),
            "listing",
            str(request.output_path),
        ],
    )
    runtime = build_application(
        runtime_paths=paths,
        spider_runner=runner,
        notifier_registry=notifiers,
    )
    context = runtime.app.extensions[WEB_CONTEXT_EXTENSION]

    search_id = context.repositories.searches.list()[0].id
    outcome = context.services.search_trigger.run_search(search_id, TriggerKind.MANUAL)
    server = ImmediateServer()
    runtime.run(server)

    assert outcome.run.status is SearchRunStatus.SUCCESS
    assert len(outcome.attempts) == 1
    assert len(delivered) == 1
    assert delivered[0].event_type is NotificationEventType.NEW_LISTING
    assert server.shutdown_requested is True
    assert runtime.scheduler.is_running is False
    with Database(paths.database_file).connection() as connection:
        assert connection.execute("SELECT count(*) FROM searches").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM notification_events"
        ).fetchone()[0] == 1
        delivery = connection.execute(
            "SELECT * FROM notification_delivery_attempts"
        ).fetchone()
        assert delivery["status"] == DeliveryStatus.SUCCEEDED.value
        assert delivery["provider_message_id"] == "integration-delivery"
        assert "integration-secret" not in str(dict(delivery))
