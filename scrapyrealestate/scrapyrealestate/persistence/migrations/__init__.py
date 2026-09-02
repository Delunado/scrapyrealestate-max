"""Ordered, forward-only database migrations."""

from scrapyrealestate.persistence.migrations.runner import Migration, MigrationRunner
from scrapyrealestate.persistence.migrations.v001_searches import apply as apply_v001
from scrapyrealestate.persistence.migrations.v002_search_portals import apply as apply_v002
from scrapyrealestate.persistence.migrations.v003_listings import apply as apply_v003
from scrapyrealestate.persistence.migrations.v004_search_listing_matches import (
    apply as apply_v004,
)
from scrapyrealestate.persistence.migrations.v005_price_history import apply as apply_v005
from scrapyrealestate.persistence.migrations.v006_runs import apply as apply_v006
from scrapyrealestate.persistence.migrations.v007_notifications import apply as apply_v007
from scrapyrealestate.persistence.migrations.v008_legacy_seen import apply as apply_v008
from scrapyrealestate.persistence.migrations.v009_legacy_import_reports import (
    apply as apply_v009,
)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "application_settings_searches_schedules", apply_v001),
    Migration(2, "search_portal_selections", apply_v002),
    Migration(3, "normalized_listings", apply_v003),
    Migration(4, "search_listing_matches", apply_v004),
    Migration(5, "listing_price_history", apply_v005),
    Migration(6, "search_runs_and_portal_attempts", apply_v006),
    Migration(7, "notification_channels_events_deliveries", apply_v007),
    Migration(8, "portal_unscoped_legacy_seen_ids", apply_v008),
    Migration(9, "legacy_import_reports", apply_v009),
)

__all__ = ["MIGRATIONS", "Migration", "MigrationRunner"]
