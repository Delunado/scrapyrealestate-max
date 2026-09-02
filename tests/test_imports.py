import importlib
import socket
import urllib.request


APPLICATION_MODULES = (
    "main",
    "scrapyrealestate.atomic_files",
    "scrapyrealestate.flask_server",
    "scrapyrealestate.items",
    "scrapyrealestate.legacy_config",
    "scrapyrealestate.notifiers",
    "scrapyrealestate.notifiers.ntfy",
    "scrapyrealestate.notifiers.telegram",
    "scrapyrealestate.notifiers.webhook",
    "scrapyrealestate.proxies",
    "scrapyrealestate.settings",
    "scrapyrealestate.security",
    "scrapyrealestate.spiders.fotocasa_spider",
    "scrapyrealestate.spiders.habitaclia_spider",
    "scrapyrealestate.spiders.idealista_spider",
    "scrapyrealestate.spiders.idealista_spider_proxy",
    "scrapyrealestate.spiders.pisoscom_spider",
    "scrapyrealestate.spiders.yaencontre_spider",
)


def test_application_modules_import_without_network_calls(
    monkeypatch, temporary_data_dir
):
    network_attempts = []

    def reject_network_call(*args, **kwargs):
        network_attempts.append((args, kwargs))
        raise AssertionError("application modules must not access the network on import")

    monkeypatch.setattr(socket, "create_connection", reject_network_call)
    monkeypatch.setattr(socket.socket, "connect", reject_network_call)
    monkeypatch.setattr(urllib.request, "urlopen", reject_network_call)

    for module_name in APPLICATION_MODULES:
        importlib.import_module(module_name)

    assert network_attempts == []
