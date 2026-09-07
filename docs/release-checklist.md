# Migration and release checklist

This checklist applies to the persistent SQLite/Waitress release represented by
schema version 15. Use it for both local installations and the single-service Docker
Compose deployment. Database migrations are forward-only.

## Supported upgrade paths

| Starting state | Supported action |
| --- | --- |
| No database | Start normally; migrations create schema v15. |
| Valid migration prefix v1–v14 | Start normally; an integrity-checked pre-migration snapshot is created before pending migrations. |
| Schema v15 | Start normally; migration is a no-op. |
| Legacy `config.json` and/or `ids.json`, with or without a database | Keep the files in the configured data directory. Startup imports each distinct source once after migration and preserves it unchanged. |
| Database newer than v15 | Do not start this release; install a release that knows that schema or restore a compatible backup. |
| Missing, renamed, reordered, or manually edited migration history | Unsupported. Restore an unmodified backup; do not repair `schema_migrations` by hand. |

Only legacy saved-search configuration and unscoped seen IDs are imported. Retired
aggregate crawl JSON, old logs, and transient output are not application history and
are not imported.

## Breaking changes from the retired runtime

- Python 3.12 is required. Dependencies are pinned in
  `scrapyrealestate/requirements.txt`, and Chromium must be installed through
  Playwright for browser portals.
- `python main.py` now starts one persistent Waitress web process and in-process
  scheduler; it no longer waits for a first-run form, runs a direct sleep loop, or
  sends Telegram messages from spiders.
- SQLite is authoritative. Preserved `config.json` and `ids.json` files are one-time,
  hash-tracked import/rollback sources, not live configuration.
- `SCRAPYREALESTATE_DATA_DIR`, when set, must be absolute. Compose fixes the container
  path at `/var/lib/scrapyrealestate` and persists it in one named volume.
- The web service listens on container port 8080. Only the host port and named-volume
  name are configurable through `.env`/Compose variables.
- The container runs as UID/GID 10001. Bind mounts must be writable by that identity.
- Notification credentials are user-supplied, stored in SQLite, and managed through
  channels. There is no shared Telegram fallback token.
- Schedules are per saved search and have a five-minute minimum. Manual and scheduled
  triggers share the same process-local overlap guard.
- Crawl output is strict per-attempt JSON Lines under `runs/`; the concatenated JSON
  repair behavior was removed.

## Pre-release verification

From the repository root in a clean Python 3.12 environment:

```powershell
python -m compileall -q scrapyrealestate
python -m pytest
python -m ruff check .
Set-Location scrapyrealestate
scrapy list
Set-Location ..
docker compose config --quiet
```

For image, persistence, or lifecycle changes, also run the two isolated Docker
checks on a host with a running daemon:

```powershell
$env:SCRAPYREALESTATE_RUN_DOCKER_SMOKE = "1"
python -m pytest tests/test_deployment_smoke.py -m deployment
$env:SCRAPYREALESTATE_RUN_DOCKER_SOAK = "1"
python -m pytest tests/test_container_soak.py -m soak
```

Live portal probes are diagnostic evidence, never a release gate for offline
correctness:

```powershell
Set-Location scrapyrealestate
$env:SCRAPYREALESTATE_RUN_LIVE_SMOKE = "1"
python -m scrapyrealestate.live_smoke --timeout-seconds 60
```

Record failures as `application`, `parser`, `site_change`, `blocking`, or
`transport`; do not rewrite a parser merely to make one transient live run pass.

## Before upgrading an installation

1. Record the deployed Git tag/commit, data-volume name, host port, image digest,
   current schema version, and whether any searches are running.
2. Stop the service and confirm `docker compose ps` shows no running application
   container. Do not use `docker compose down -v`.
3. Create an integrity-checked database backup with
   `python -m scrapyrealestate.maintenance backup <off-volume-path>` or its
   `docker compose run --rm --no-deps --entrypoint python ...` equivalent.
4. Archive the complete data directory/volume as a second recovery source. It
   contains credentials, so restrict access and never commit it.
5. Preserve legacy `config.json` and `ids.json` files if they have not yet been
   imported. Record checksums if they are part of the migration.
6. Verify adequate free space for the database, a full pre-migration snapshot, WAL,
   and temporary crawl output.

## Upgrade procedure

1. Fetch and check out the intended release revision; review this checklist and its
   release notes before rebuilding.
2. Build the image from that exact revision: `docker compose build --pull`.
3. Start/recreate it: `docker compose up -d --force-recreate`.
4. Wait for `docker compose ps` to report healthy and confirm `/readyz` returns 200.
5. Inspect startup logs for migration/import warnings. An older existing database
   must produce `backups/pre-migration-v<old>-to-v15.sqlite3` before its schema is
   changed. Do not delete this snapshot during validation.
6. Confirm `SELECT max(version) FROM schema_migrations` returns `15` and
   `PRAGMA integrity_check` returns `ok`.
7. Verify the dashboard, saved searches, schedules, portal selections, listing/price
   history, duplicate-candidate history, notification assignments, and masked channel
   forms.
8. Send a notification test through each enabled channel, then run one maintainable
   portal manually. Confirm delivery/run status contains stable categories and no
   credentials.
9. Observe at least one scheduler deadline for installations where release timing
   permits. Confirm no same-search overlap and no duplicate successful delivery pair.
10. Retain the pre-upgrade and automatic snapshots until the release has completed
    its chosen observation window.

## Rollback procedure

A code-only rollback is supported only when the older revision recognizes the
current schema. Otherwise:

1. Stop the service and retain the failed-upgrade data directory separately for
   diagnosis. Confirm no crawl, Chromium, scheduler, or web process remains.
2. Restore the off-volume pre-upgrade database/volume, or the automatic
   `pre-migration-v<old>-to-v15.sqlite3` snapshot, while the application is stopped.
   Use `scrapyrealestate.maintenance restore <backup> --replace` for a database-only
   restore. Restore the whole volume when legacy sources or other runtime files also
   matter.
3. Check out the recorded earlier revision and rebuild its image.
4. Start the service, verify `/readyz`, schema compatibility, SQLite integrity,
   searches, history, and notification configuration before re-enabling normal
   access.

Never delete migration rows, downgrade tables in place, mix database files with WAL
or SHM files from another snapshot, or run restore against a live service.

## Known release limitations

- The management UI has no built-in user authentication or TLS termination. Bind it
  only to a trusted network or place it behind an authenticated HTTPS reverse proxy.
- The scheduler and overlap locks are process-local. Run exactly one application
  replica against a data volume; this is not a multi-node/HA deployment.
- SQLite is a single-host store. Lock contention is bounded and visible, but network
  filesystems and concurrent replicas are unsupported.
- Provider delivery is durable but inherently at-least-once if the process dies after
  the provider accepts a message and before SQLite records success.
- Missing listing fields are treated as unknown, not false. Local filters keep an
  otherwise plausible result when the requested field cannot be evaluated.
- Cross-site duplicate groups are conservative review candidates only. Accepting one
  never merges or rewrites listings.
- Idealista and its public-proxy option remain degraded and provide no anti-bot
  guarantee. Normalized Idealista URL construction is deliberately unavailable when
  province taxonomy cannot be derived safely.
- The 2026-09-07 live probe returned listings for Pisos.com, Habitaclia, and Fotocasa;
  Yaencontre was classified `site_change` after its rendered-card selector timed out.
  Portal behavior can change independently of a release.
- Phase 11 portal expansion remains deliberately open and is not required for this
  release.

## Release sign-off record

Record the release revision, date/time, operator, backup path/checksum, starting and
ending schema versions, offline check results, Docker smoke/soak results, live probe
report, and rollback decision owner. Links to the latest checked-in evidence are:

- `docs/live-smoke-2026-09-07.md`
- `docs/container-soak-2026-09-07.md`
