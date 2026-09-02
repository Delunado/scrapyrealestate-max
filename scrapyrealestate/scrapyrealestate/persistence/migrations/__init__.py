"""Ordered, forward-only database migrations."""

from scrapyrealestate.persistence.migrations.runner import Migration, MigrationRunner


MIGRATIONS: tuple[Migration, ...] = ()

__all__ = ["MIGRATIONS", "Migration", "MigrationRunner"]
