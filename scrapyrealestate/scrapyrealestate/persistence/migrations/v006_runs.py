"""Create search-run and isolated portal-attempt operational history."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE search_runs (
            id INTEGER PRIMARY KEY,
            search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
            trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('scheduled', 'manual')),
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                'pending', 'running', 'success', 'partial', 'failed', 'cancelled'
            )),
            scheduled_for TEXT CHECK (scheduled_for IS NULL OR (
                datetime(scheduled_for) IS NOT NULL AND substr(scheduled_for, -1) = 'Z'
            )),
            started_at TEXT CHECK (started_at IS NULL OR (
                datetime(started_at) IS NOT NULL AND substr(started_at, -1) = 'Z'
            )),
            finished_at TEXT CHECK (finished_at IS NULL OR (
                datetime(finished_at) IS NOT NULL
                AND substr(finished_at, -1) = 'Z'
                AND started_at IS NOT NULL
                AND finished_at >= started_at
            )),
            returned_count INTEGER NOT NULL DEFAULT 0 CHECK (returned_count >= 0),
            matched_count INTEGER NOT NULL DEFAULT 0 CHECK (matched_count >= 0),
            new_count INTEGER NOT NULL DEFAULT 0 CHECK (new_count >= 0),
            changed_count INTEGER NOT NULL DEFAULT 0 CHECK (changed_count >= 0),
            error_category TEXT CHECK (
                error_category IS NULL OR length(trim(error_category)) > 0
            ),
            redacted_diagnostic TEXT CHECK (
                redacted_diagnostic IS NULL OR length(redacted_diagnostic) <= 2000
            ),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z'),
            CHECK (status IN ('pending', 'running') OR finished_at IS NOT NULL),
            CHECK (status != 'running' OR (started_at IS NOT NULL AND finished_at IS NULL))
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX search_runs_latest_idx
        ON search_runs(search_id, created_at DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE portal_attempts (
            id INTEGER PRIMARY KEY,
            search_run_id INTEGER NOT NULL
                REFERENCES search_runs(id) ON DELETE CASCADE,
            portal_key TEXT NOT NULL
                CHECK (
                    length(trim(portal_key)) > 0
                    AND portal_key = lower(portal_key)
                    AND portal_key NOT GLOB '*[^a-z0-9_]*'
                ),
            attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
            status TEXT NOT NULL DEFAULT 'running' CHECK (status IN (
                'running', 'success', 'empty', 'timeout', 'transport_error',
                'parser_error', 'blocked', 'unavailable'
            )),
            started_at TEXT NOT NULL
                CHECK (datetime(started_at) IS NOT NULL AND substr(started_at, -1) = 'Z'),
            finished_at TEXT CHECK (finished_at IS NULL OR (
                datetime(finished_at) IS NOT NULL
                AND substr(finished_at, -1) = 'Z'
                AND finished_at >= started_at
            )),
            returned_count INTEGER NOT NULL DEFAULT 0 CHECK (returned_count >= 0),
            matched_count INTEGER NOT NULL DEFAULT 0 CHECK (matched_count >= 0),
            new_count INTEGER NOT NULL DEFAULT 0 CHECK (new_count >= 0),
            changed_count INTEGER NOT NULL DEFAULT 0 CHECK (changed_count >= 0),
            error_category TEXT CHECK (
                error_category IS NULL OR length(trim(error_category)) > 0
            ),
            redacted_diagnostic TEXT CHECK (
                redacted_diagnostic IS NULL OR length(redacted_diagnostic) <= 2000
            ),
            UNIQUE (search_run_id, portal_key, attempt_number),
            CHECK (status = 'running' OR finished_at IS NOT NULL),
            CHECK (status != 'running' OR finished_at IS NULL)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX portal_attempts_run_idx
        ON portal_attempts(search_run_id, portal_key, attempt_number)
        """
    )
