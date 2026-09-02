"""Ordered, forward-only database migrations."""

from scrapyrealestate.persistence.migrations.runner import Migration, MigrationRunner
from scrapyrealestate.persistence.migrations.v001_searches import apply as apply_v001
from scrapyrealestate.persistence.migrations.v002_search_portals import apply as apply_v002
from scrapyrealestate.persistence.migrations.v003_listings import apply as apply_v003
from scrapyrealestate.persistence.migrations.v004_search_listing_matches import (
    apply as apply_v004,
)
from scrapyrealestate.persistence.migrations.v005_price_history import apply as apply_v005


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "application_settings_searches_schedules", apply_v001),
    Migration(2, "search_portal_selections", apply_v002),
    Migration(3, "normalized_listings", apply_v003),
    Migration(4, "search_listing_matches", apply_v004),
    Migration(5, "listing_price_history", apply_v005),
)

__all__ = ["MIGRATIONS", "Migration", "MigrationRunner"]
