from pathlib import Path

from scrapyrealestate.flask_server import WebServices, create_app
from scrapyrealestate.runtime import RuntimePaths


def _app(tmp_path: Path, services: WebServices | None = None):
    return create_app(
        runtime_paths=RuntimePaths((tmp_path / "data").resolve()),
        services=services,
        config={"TESTING": True},
    )


def test_health_and_default_readiness_do_not_require_legacy_config(tmp_path: Path):
    app = _app(tmp_path)
    client = app.test_client()

    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").get_json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    assert client.get("/readyz").get_json() == {"status": "ready"}
    assert not (tmp_path / "data" / "config.json").exists()


def test_readiness_uses_only_the_injected_local_check(tmp_path: Path):
    calls = []

    def readiness_check():
        calls.append("checked")
        return False

    app = _app(tmp_path, WebServices(readiness_check=readiness_check))

    response = app.test_client().get("/readyz")

    assert response.status_code == 503
    assert response.get_json() == {"status": "not_ready"}
    assert calls == ["checked"]


def test_readiness_failure_does_not_disclose_exception_details(tmp_path: Path):
    def readiness_check():
        raise RuntimeError("database failed with super-secret-token")

    app = _app(tmp_path, WebServices(readiness_check=readiness_check))

    response = app.test_client().get("/readyz")

    assert response.status_code == 503
    assert response.get_json() == {"status": "not_ready"}
    assert b"super-secret-token" not in response.data
