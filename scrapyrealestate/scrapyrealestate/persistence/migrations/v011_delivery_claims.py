"""Add lease-based delivery claiming and retry scheduling metadata."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE notification_delivery_attempts ADD COLUMN available_at TEXT
        CHECK (available_at IS NULL OR (
            datetime(available_at) IS NOT NULL AND substr(available_at, -1) = 'Z'
        ))
        """
    )
    connection.execute(
        """
        ALTER TABLE notification_delivery_attempts ADD COLUMN claim_token TEXT
        CHECK (claim_token IS NULL OR length(trim(claim_token)) > 0)
        """
    )
    connection.execute(
        """
        ALTER TABLE notification_delivery_attempts ADD COLUMN lease_expires_at TEXT
        CHECK (lease_expires_at IS NULL OR (
            datetime(lease_expires_at) IS NOT NULL
            AND substr(lease_expires_at, -1) = 'Z'
        ))
        """
    )
    connection.execute(
        "UPDATE notification_delivery_attempts SET available_at = created_at"
    )
    connection.execute(
        """
        UPDATE notification_delivery_attempts
        SET claim_token = 'legacy-' || id,
            lease_expires_at = coalesce(claimed_at, created_at)
        WHERE status = 'claimed'
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX notification_delivery_attempts_claim_token_idx
        ON notification_delivery_attempts(claim_token) WHERE claim_token IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX notification_delivery_attempts_claimable_idx
        ON notification_delivery_attempts(status, available_at, lease_expires_at, id)
        """
    )
