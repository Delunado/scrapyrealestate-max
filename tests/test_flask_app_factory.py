from pathlib import Path

from scrapyrealestate.flask_server import (
    WEB_CONTEXT_EXTENSION,
    WebRepositories,
    WebServices,
    create_app,
    get_web_context,
)
from scrapyrealestate.runtime import RuntimePaths


def test_factory_injects_dependencies_into_one_application(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "persistent-data").resolve())
    searches = object()
    runs = object()
    notifications = object()
    orchestration = object()
    search_trigger = object()
    repositories = WebRepositories(
        searches=searches,
        runs=runs,
        notifications=notifications,
    )
    services = WebServices(
        orchestration=orchestration,
        search_trigger=search_trigger,
    )

    app = create_app(
        runtime_paths=paths,
        repositories=repositories,
        services=services,
        config={"TESTING": True},
    )

    assert app.config["TESTING"] is True
    with app.app_context():
        context = get_web_context()
        assert context.runtime_paths is paths
        assert context.repositories is repositories
        assert context.repositories.searches is searches
        assert context.repositories.runs is runs
        assert context.repositories.notifications is notifications
        assert context.services is services
        assert context.services.orchestration is orchestration
        assert context.services.search_trigger is search_trigger


def test_factory_instances_do_not_share_runtime_state(tmp_path: Path):
    first_paths = RuntimePaths((tmp_path / "first").resolve())
    second_paths = RuntimePaths((tmp_path / "second").resolve())
    first = create_app(runtime_paths=first_paths, config={"TESTING": True})
    second = create_app(runtime_paths=second_paths, config={"TESTING": True})

    first.test_client().post("/data", data={"scrapy_rs_name": "first"})
    second.test_client().post("/data", data={"scrapy_rs_name": "second"})

    assert '"first"' in first_paths.config_file.read_text(encoding="utf-8")
    assert '"second"' in second_paths.config_file.read_text(encoding="utf-8")
    assert first.extensions[WEB_CONTEXT_EXTENSION] is not second.extensions[
        WEB_CONTEXT_EXTENSION
    ]


def test_application_remains_available_with_or_without_legacy_config(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "persistent-data").resolve())
    app = create_app(runtime_paths=paths, config={"TESTING": True})
    client = app.test_client()

    assert not paths.config_file.exists()
    assert client.get("/").status_code == 200

    response = client.post("/data", data={"scrapy_rs_name": "configured"})

    assert response.status_code == 200
    assert paths.config_file.exists()
    assert client.get("/").status_code == 200
    assert client.get("/home").status_code == 200
