"""Ordered, forward-only database migrations."""

from scrapyrealestate.persistence.migrations.runner import Migration, MigrationRunner
from scrapyrealestate.persistence.migrations.v001_searches import apply as apply_v001
from scrapyrealestate.persistence.migrations.v002_search_portals import apply as apply_v002
from scrapyrealestate.persistence.migrations.v003_listings import apply as apply_v003


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "application_settings_searches_schedules", apply_v001),
    Migration(2, "search_portal_selections", apply_v002),
    Migration(3, "normalized_listings", apply_v003),
)

__all__ = ["MIGRATIONS", "Migration", "MigrationRunner"]
