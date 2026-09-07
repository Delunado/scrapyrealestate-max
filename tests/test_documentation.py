from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shipped_commands_and_current_architecture_are_documented():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for command in (
        "python -m scrapyrealestate.maintenance backup",
        "python -m scrapyrealestate.maintenance restore",
        "python -m scrapyrealestate.live_smoke",
        "python -m pytest tests/test_container_soak.py -m soak",
    ):
        assert command in readme
    assert "The current Flask server is not persistent" not in agents
    assert "Listing IDs are not globally unique today" not in agents
    assert "pre-migration" in readme.lower()
    assert "Yaencontre" in readme and "site_change" in readme


def test_environment_example_contains_only_supported_non_secret_compose_values():
    lines = {
        line
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }

    assert lines == {
        "SCRAPYREALESTATE_WEB_PORT=8080",
        "SCRAPYREALESTATE_DATA_VOLUME=scrapyrealestate-data",
    }
    assert all("TOKEN" not in line and "SECRET" not in line for line in lines)
