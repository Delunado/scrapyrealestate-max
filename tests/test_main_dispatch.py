"""``scrap_realestate`` must route through the portal registry, not an
if/elif domain chain, while continuing to accept legacy raw search URLs."""

import logging
import subprocess
from pathlib import Path

import pytest

import main
from scrapyrealestate.legacy_config import LegacyConfig
from scrapyrealestate.portals import build_default_registry
from scrapyrealestate.runtime import RuntimePaths


@pytest.fixture(autouse=True)
def _stub_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "runtime_paths", RuntimePaths(tmp_path))
    monkeypatch.setattr(
        main, "logger", logging.getLogger("test-main-dispatch"), raising=False
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode=0),
    )


def _run(monkeypatch: pytest.MonkeyPatch, config: LegacyConfig, *, idealista_proxy=False):
    calls = []
    monkeypatch.setattr(main, "data", config)
    monkeypatch.setattr(
        main, "registry", build_default_registry(idealista_proxy=idealista_proxy)
    )
    monkeypatch.setattr(
        main,
        "run_spider",
        lambda spider_name, scrapy_log, out_file, start_url: calls.append(
            (spider_name, start_url)
        ),
    )
    main.scrap_realestate(telegram_msg=False)
    return calls


def test_routes_each_portal_to_its_spider_via_registry(monkeypatch):
    config = LegacyConfig(
        url_pisoscom=("https://www.pisos.com/venta/pisos-madrid/",),
        url_habitaclia=("https://www.habitaclia.com/venta-madrid.htm",),
        url_fotocasa=("https://www.fotocasa.es/es/comprar/viviendas/madrid/todas-las-zonas/l",),
        url_yaencontre=("https://www.yaencontre.com/venta/pisos/madrid",),
    )
    calls = _run(monkeypatch, config)

    spiders = {spider for spider, _ in calls}
    assert spiders == {"pisoscom", "habitaclia", "fotocasa", "yaencontre"}


def test_idealista_proxy_flag_selects_the_proxy_spider(monkeypatch):
    config = LegacyConfig(
        url_idealista=("https://www.idealista.com/venta-viviendas/madrid-madrid/",),
        proxy_idealista=True,
    )
    calls = _run(monkeypatch, config, idealista_proxy=True)

    assert [spider for spider, _ in calls] == ["idealista_proxy"]


def test_idealista_without_proxy_flag_uses_the_playwright_spider(monkeypatch):
    config = LegacyConfig(
        url_idealista=("https://www.idealista.com/venta-viviendas/madrid-madrid/",),
    )
    calls = _run(monkeypatch, config, idealista_proxy=False)

    assert [spider for spider, _ in calls] == ["idealista"]


def test_unrecognized_hostname_is_skipped_without_raising(monkeypatch):
    config = LegacyConfig(url_pisoscom=("https://www.example.com/venta/pisos-madrid/",))
    calls = _run(monkeypatch, config)

    assert calls == []


def test_url_with_unresolvable_transaction_type_is_skipped_without_raising(monkeypatch):
    # Pisos.com's transaction type comes from the first path segment; a URL
    # missing "venta"/"alquiler" cannot be built into a request and must not
    # crash the whole run.
    config = LegacyConfig(url_pisoscom=("https://www.pisos.com/otra-seccion/",))
    calls = _run(monkeypatch, config)

    assert calls == []


def test_recognized_and_unrecognized_urls_can_share_one_run(monkeypatch):
    config = LegacyConfig(
        url_pisoscom=("https://www.pisos.com/venta/pisos-madrid/",),
        url_habitaclia=("https://www.example.com/no-portal-here",),
    )
    calls = _run(monkeypatch, config)

    assert [spider for spider, _ in calls] == ["pisoscom"]
