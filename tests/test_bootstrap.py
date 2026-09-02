import json
from pathlib import Path

from scrapyrealestate.bootstrap import build_application
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.runtime import RuntimePaths


class ImmediateServer:
    def __init__(self, callback=None):
        self.callback = callback
        self.shutdown_requested = False

    def serve(self, app):
        if self.callback is not None:
            self.callback(app)

    def request_shutdown(self):
        self.shutdown_requested = True


def test_bootstrap_starts_web_and_scheduler_without_legacy_config(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "data").resolve())
    runtime = build_application(runtime_paths=paths)
    observations = []

    def serve(app):
        observations.append(runtime.scheduler.is_running)
        client = app.test_client()
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200

    server = ImmediateServer(serve)
    runtime.run(server)

    assert observations == [True]
    assert server.shutdown_requested is True
    assert runtime.scheduler.is_running is False
    assert paths.database_file.exists()
    assert not paths.config_file.exists()
    assert runtime.report.schema_version > 0
    assert runtime.report.config_imported is False
    assert runtime.report.ids_imported is False


def test_bootstrap_imports_each_preserved_legacy_source_only_once(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "data").resolve())
    paths.ensure_data_dir()
    paths.config_file.write_text(
        json.dumps(
            {
                "scrapy_rs_name": "Legacy search",
                "time_update": "600",
                "url_pisoscom": "https://www.pisos.com/alquiler/pisos-madrid/",
            }
        ),
        encoding="utf-8",
    )
    paths.ids_file.write_text("[10, 20, 20]", encoding="utf-8")
    original_config = paths.config_file.read_bytes()
    original_ids = paths.ids_file.read_bytes()

    first = build_application(runtime_paths=paths)
    first_report = first.report
    assert first.close()
    second = build_application(runtime_paths=paths)
    second_report = second.report
    assert second.close()

    assert first_report.config_imported is True
    assert first_report.ids_imported is True
    assert second_report.config_imported is False
    assert second_report.ids_imported is False
    with Database(paths.database_file).connection() as connection:
        assert connection.execute("SELECT count(*) FROM searches").fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM legacy_seen_ids"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM legacy_import_reports"
        ).fetchone()[0] == 2
    assert paths.config_file.read_bytes() == original_config
    assert paths.ids_file.read_bytes() == original_ids


def test_invalid_legacy_sources_do_not_prevent_persistent_web_start(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "data").resolve())
    paths.ensure_data_dir()
    paths.config_file.write_text("not json", encoding="utf-8")
    paths.ids_file.write_text('{"not": "a list"}', encoding="utf-8")
    runtime = build_application(runtime_paths=paths)

    try:
        assert runtime.app.test_client().get("/").status_code == 200
        assert runtime.report.config_imported is False
        assert runtime.report.ids_imported is False
        assert len(runtime.report.import_warnings) == 2
    finally:
        runtime.close()
