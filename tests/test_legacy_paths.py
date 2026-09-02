import json
from pathlib import Path

from scrapyrealestate import flask_server, settings
from scrapyrealestate.runtime import RuntimePaths


def test_first_run_form_writes_config_to_runtime_paths(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "runtime-data").resolve())
    app = flask_server.create_app(runtime_paths=paths, config={"TESTING": True})

    response = app.test_client().post(
        "/data",
        data={
            "scrapy_rs_name": "configured",
            "url_pisoscom": ["https://www.pisos.com/alquiler/pisos-madrid/"],
        },
    )

    assert response.status_code == 200
    assert json.loads(paths.config_file.read_text(encoding="utf-8")) == {
        "scrapy_rs_name": "configured",
        "url_pisoscom": ["https://www.pisos.com/alquiler/pisos-madrid/"],
        "url_idealista": [],
        "url_fotocasa": [],
        "url_habitaclia": [],
        "url_yaencontre": [],
    }


def test_scrapy_headers_read_user_agent_from_runtime_paths(tmp_path: Path, monkeypatch):
    paths = RuntimePaths((tmp_path / "runtime-data").resolve())
    paths.ensure_data_dir()
    paths.user_agent_file.write_text("configured-user-agent\n", encoding="utf-8")
    monkeypatch.setattr(settings, "runtime_paths", paths)

    headers = settings.custom_headers(None, None, None)

    assert headers["User-Agent"] == "configured-user-agent"
