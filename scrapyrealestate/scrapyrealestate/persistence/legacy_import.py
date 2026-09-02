"""Idempotent import of legacy JSON configuration into SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scrapyrealestate.domain.search import SearchFilters
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.legacy_config import LegacyConfig, load_legacy_config
from scrapyrealestate.persistence.database import transaction


CONFIG_IMPORT_MARKER = "legacy.config_import.v1"
LEGACY_RUNTIME_SETTINGS = "legacy.runtime_settings.v1"


@dataclass(frozen=True, slots=True)
class LegacyConfigImportResult:
    imported: bool
    search_id: int
    channel_id: int | None
    portal_count: int
    warnings: tuple[str, ...]
    source_digest: str


class LegacyConfigImporter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def import_file(self, config_file: Path) -> LegacyConfigImportResult:
        source = Path(config_file)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        existing = self._marker()
        if existing is not None:
            return LegacyConfigImportResult(
                imported=False,
                search_id=existing["search_id"],
                channel_id=existing.get("channel_id"),
                portal_count=existing["portal_count"],
                warnings=tuple(existing.get("warnings", ())),
                source_digest=existing["source_digest"],
            )

        config = load_legacy_config(source)
        transaction_type, warnings = _infer_transaction(config)
        name = self._available_search_name(config.scrapy_rs_name, warnings)
        filters = SearchFilters(
            min_price_euros=config.min_price or None,
            max_price_euros=config.max_price or None,
        )
        portals = _portal_selections(config)
        timestamp = _utc_now()

        with transaction(self.connection, immediate=True):
            search_id = self.connection.execute(
                """
                INSERT INTO searches (
                    name, transaction_type, filters_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?) RETURNING id
                """,
                (
                    name,
                    transaction_type.value,
                    _json(filters.to_dict()),
                    timestamp,
                    timestamp,
                ),
            ).fetchone()[0]
            self.connection.execute(
                """
                INSERT INTO search_schedules (
                    search_id, interval_seconds, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (search_id, config.time_update, timestamp, timestamp),
            )
            self.connection.executemany(
                """
                INSERT INTO search_portals (
                    search_id, portal_key, raw_url_override,
                    adapter_options_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        search_id,
                        portal.value,
                        urls[0],
                        _json({"legacy_urls": list(urls)} if len(urls) > 1 else {}),
                        timestamp,
                        timestamp,
                    )
                    for portal, urls in portals
                ),
            )
            channel_id = self._create_telegram_channel(
                search_id, config, timestamp, warnings
            )
            self._store_setting(
                LEGACY_RUNTIME_SETTINGS,
                {
                    "log_level": config.log_level,
                    "log_level_scrapy": config.log_level_scrapy,
                    "proxy_idealista": config.proxy_idealista,
                    "send_first": config.send_first,
                    "start_msg": config.start_msg,
                },
                timestamp,
            )
            marker = {
                "search_id": search_id,
                "channel_id": channel_id,
                "portal_count": len(portals),
                "warnings": warnings,
                "source_digest": digest,
                "source_name": source.name,
                "imported_at": timestamp,
            }
            self._store_setting(CONFIG_IMPORT_MARKER, marker, timestamp)

        return LegacyConfigImportResult(
            imported=True,
            search_id=search_id,
            channel_id=channel_id,
            portal_count=len(portals),
            warnings=tuple(warnings),
            source_digest=digest,
        )

    def _marker(self) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT value_json FROM application_settings WHERE key = ?",
            (CONFIG_IMPORT_MARKER,),
        ).fetchone()
        return json.loads(row["value_json"]) if row is not None else None

    def _available_search_name(self, preferred: str, warnings: list[str]) -> str:
        exists = self.connection.execute(
            "SELECT 1 FROM searches WHERE name = ? COLLATE NOCASE", (preferred,)
        ).fetchone()
        if exists is None:
            return preferred
        warnings.append("search name already existed; imported with a legacy suffix")
        base = f"{preferred} (legacy import)"
        candidate = base
        suffix = 2
        while self.connection.execute(
            "SELECT 1 FROM searches WHERE name = ? COLLATE NOCASE", (candidate,)
        ).fetchone():
            candidate = f"{base} {suffix}"
            suffix += 1
        return candidate

    def _create_telegram_channel(
        self,
        search_id: int,
        config: LegacyConfig,
        timestamp: str,
        warnings: list[str],
    ) -> int | None:
        if not config.telegram_chatuser_id and not config.telegram_bot_token:
            warnings.append("legacy configuration has no Telegram channel settings")
            return None
        enabled = bool(config.telegram_chatuser_id and config.telegram_bot_token)
        if not enabled:
            warnings.append("incomplete Telegram settings were imported disabled")
        channel_id = self.connection.execute(
            """
            INSERT INTO notification_channels (
                name, provider, config_json, secret_config_json, enabled,
                created_at, updated_at
            ) VALUES (?, 'telegram', ?, ?, ?, ?, ?) RETURNING id
            """,
            (
                "Telegram (legacy)",
                _json({"chat_id": config.telegram_chatuser_id}),
                _json({"bot_token": config.telegram_bot_token}),
                int(enabled),
                timestamp,
                timestamp,
            ),
        ).fetchone()[0]
        self.connection.execute(
            """
            INSERT INTO search_notification_channels (search_id, channel_id, created_at)
            VALUES (?, ?, ?)
            """,
            (search_id, channel_id, timestamp),
        )
        return channel_id

    def _store_setting(self, key: str, value: object, timestamp: str) -> None:
        self.connection.execute(
            """
            INSERT INTO application_settings (
                key, value_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (key, _json(value), timestamp, timestamp),
        )


_PORTAL_FIELDS = (
    ("url_idealista", PortalKey.IDEALISTA),
    ("url_pisoscom", PortalKey.PISOSCOM),
    ("url_fotocasa", PortalKey.FOTOCASA),
    ("url_habitaclia", PortalKey.HABITACLIA),
    ("url_yaencontre", PortalKey.YAENCONTRE),
)


def _portal_selections(
    config: LegacyConfig,
) -> tuple[tuple[PortalKey, tuple[str, ...]], ...]:
    selections = []
    for field, portal in _PORTAL_FIELDS:
        urls = getattr(config, field)
        if not urls:
            continue
        if portal is PortalKey.IDEALISTA and config.proxy_idealista:
            portal = PortalKey.IDEALISTA_PROXY
        selections.append((portal, urls))
    return tuple(selections)


def _infer_transaction(config: LegacyConfig) -> tuple[TransactionType, list[str]]:
    urls = [url.casefold() for values in config.portal_urls.values() for url in values]
    has_rent = any(
        marker in url for url in urls for marker in ("alquiler", "alquilar", "rent")
    )
    has_buy = any(
        marker in url for url in urls for marker in ("venta", "comprar", "sale")
    )
    warnings: list[str] = []
    if has_rent and not has_buy:
        return TransactionType.RENT, warnings
    if has_rent and has_buy:
        warnings.append("legacy URLs mix buy and rent; transaction defaulted to buy")
    elif not has_buy:
        warnings.append("transaction could not be inferred; defaulted to buy")
    return TransactionType.BUY, warnings


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
