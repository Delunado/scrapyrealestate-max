"""Add persistent, reviewable cross-site duplicate candidate groups."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE duplicate_candidate_groups (
            id INTEGER PRIMARY KEY,
            pair_key TEXT NOT NULL UNIQUE CHECK (
                pair_key GLOB '[0-9]*:[0-9]*' AND length(pair_key) <= 64
            ),
            score REAL NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
            reasons_json TEXT NOT NULL CHECK (
                json_valid(reasons_json) AND json_type(reasons_json) = 'array'
            ),
            review_state TEXT NOT NULL DEFAULT 'pending'
                CHECK (review_state IN ('pending', 'accepted', 'rejected')),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z'),
            updated_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (
                    datetime(updated_at) IS NOT NULL
                    AND substr(updated_at, -1) = 'Z'
                    AND updated_at >= created_at
                ),
            reviewed_at TEXT CHECK (reviewed_at IS NULL OR (
                datetime(reviewed_at) IS NOT NULL
                AND substr(reviewed_at, -1) = 'Z'
                AND reviewed_at >= created_at
            )),
            CHECK (
                (review_state = 'pending' AND reviewed_at IS NULL)
                OR (review_state IN ('accepted', 'rejected') AND reviewed_at IS NOT NULL)
            )
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE TABLE duplicate_candidate_memberships (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL
                REFERENCES duplicate_candidate_groups(id) ON DELETE CASCADE,
            listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
            added_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(added_at) IS NOT NULL AND substr(added_at, -1) = 'Z'),
            removed_at TEXT CHECK (removed_at IS NULL OR (
                datetime(removed_at) IS NOT NULL
                AND substr(removed_at, -1) = 'Z'
                AND removed_at >= added_at
            )),
            UNIQUE (group_id, listing_id, added_at)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX duplicate_candidate_active_membership_uq
        ON duplicate_candidate_memberships(group_id, listing_id)
        WHERE removed_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX duplicate_candidate_review_score_idx
        ON duplicate_candidate_groups(review_state, score DESC, updated_at DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX duplicate_candidate_listing_history_idx
        ON duplicate_candidate_memberships(listing_id, added_at DESC, id DESC)
        """
    )
