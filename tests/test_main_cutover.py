import main
from scrapyrealestate import bootstrap


def test_legacy_entrypoint_delegates_to_persistent_bootstrap(monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "main", lambda: calls.append("persistent"))
    monkeypatch.setattr(
        main,
        "init",
        lambda: (_ for _ in ()).throw(AssertionError("legacy loop was called")),
    )

    main.main()

    assert calls == ["persistent"]
