# ScrapyRealEstate

ScrapyRealEstate monitors saved searches on Spanish property portals, stores
normalized listing history in SQLite, and delivers provider-neutral change events
through Telegram, ntfy, or HTTP webhooks. One persistent process serves the web UI,
runs the in-process scheduler, launches isolated Scrapy jobs, and retains run and
delivery status.

Supported adapters currently cover Pisos.com, Habitaclia, Fotocasa, Yaencontre,
and Idealista. Idealista is marked degraded because DataDome commonly blocks
headless automation; the optional public-proxy variant is not a supported anti-bot
guarantee.

## Test the web UI locally from this checkout

Python 3.12 is the supported version. From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r scrapyrealestate\requirements.txt
playwright install chromium
New-Item -ItemType Directory -Force .local-data
$env:SCRAPYREALESTATE_DATA_DIR = (Resolve-Path .local-data).Path
Set-Location scrapyrealestate
python main.py
```

If the virtual environment and dependencies already exist, start at the
`Activate.ps1` line. Open <http://localhost:8080/> after Waitress starts. Stop the
application with `Ctrl+C`.

The absolute `SCRAPYREALESTATE_DATA_DIR` keeps the test database at
`.local-data/scrapyrealestate.sqlite3`, independently of the process working directory.
Delete that test directory only when you intentionally want a clean local database.
Without the environment variable, the compatibility location is
`scrapyrealestate/data/` when launched as shown above.

The UI supports:

- dashboard and scheduler/search status;
- saved-search creation, editing, enabling, deletion, and manual execution;
- normalized filters, per-portal selection, capability coverage, and advanced raw
  URL validation;
- Telegram, ntfy, and webhook channel management with masked secrets;
- per-search channel assignments and event preferences;
- safe test notifications with persisted, redacted outcomes;
- paginated listing history, event/inactive views, listing and price details;
- per-run diagnostics and bounded recent portal-health summaries;
- conservative cross-site duplicate suggestions with evidence and durable manual
  accept/reject review; suggestions never merge listing identity automatically.

Use a real reachable portal URL only when you intentionally run a crawl. The
ordinary test suite is offline and does not contact portals.

## Offline development checks

From the repository root with the virtual environment active:

```powershell
python -m compileall -q scrapyrealestate
python -m pytest
python -m ruff check .
```

Optional checks:

```powershell
Set-Location scrapyrealestate
scrapy list
Set-Location ..
docker compose config --quiet
```

Live spider checks are opt-in and must run from the inner `scrapyrealestate/`
directory, where `scrapy.cfg` lives. The cross-platform runner discovers the
portals selected by enabled searches, uses fixed non-secret Madrid probes, and
writes a secret-free summary plus ignored logs/feeds to the configured data
directory:

```powershell
$env:SCRAPYREALESTATE_RUN_LIVE_SMOKE = "1"
python -m scrapyrealestate.live_smoke --timeout-seconds 60
```

For a single portal, the original Bash helper remains available:

```bash
./test_spider.sh pisoscom
./test_spider.sh fotocasa 'https://www.fotocasa.es/...'
```

The latest recorded run is [docs/live-smoke-2026-09-07.md](docs/live-smoke-2026-09-07.md).

## Self-hosted Docker deployment

Docker Compose builds the application image from this checkout and uses the named
volume `scrapyrealestate-data` for all persistent state. It runs the application as
the unprivileged UID/GID `10001`, has a readiness healthcheck, restarts unless it is
explicitly stopped, and gives graceful shutdown 30 seconds. `tini` reaps Scrapy and
Chromium child processes for both Compose and direct image use.

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

Open <http://localhost:8080/> once `docker compose ps` reports the service as
healthy. Compose exposes only the web port. To use a different host port or an
independent persistent volume, set variables for the current PowerShell session
before starting:

```powershell
$env:SCRAPYREALESTATE_WEB_PORT = "8181"
$env:SCRAPYREALESTATE_DATA_VOLUME = "scrapyrealestate-production-data"
docker compose up --build -d
```

The application always writes this deployment's data to
`/var/lib/scrapyrealestate`. For a direct `docker run`, set
`SCRAPYREALESTATE_DATA_DIR` only to an absolute, writable path. The Compose file
sets it deliberately; do not override it with a path outside the mounted volume.

### Data, secrets, and permissions

The persistent volume contains `scrapyrealestate.sqlite3`, its SQLite WAL/SHM files,
legacy import sources if supplied, and per-attempt output under `runs/`. It is the
complete application state. A named volume is initialized with the image's
UID/GID `10001`; if you replace it with a host bind mount, create the directory and
grant write access to UID/GID `10001` before starting the service.

Notification credentials are supplied through the UI and stored in the local SQLite
database so durable delivery can survive a restart. They are masked in ordinary UI,
status, and log output, but database access and backups can read them. Treat the
volume and every backup as secret material; do not commit a legacy `config.json`, a
database, or a backup archive. No provider token is baked into the image.

### Backup, restore, update, and rollback

The application creates an integrity-checked SQLite snapshot before applying any
pending migration to an existing database. It is stored once as
`backups/pre-migration-v<old>-to-v<new>.sqlite3`; a fresh database and a database
already at the current schema do not create one. Startup aborts if that safety copy
cannot be made or validated.

For a consistent manual SQLite backup, stop the application and run the maintenance
command from the inner `scrapyrealestate/` directory. An omitted destination creates
a timestamped file under the configured data directory's `backups/` folder:

```powershell
python -m scrapyrealestate.maintenance backup D:\secure-backups\scrapyrealestate.sqlite3
python -m scrapyrealestate.maintenance restore D:\secure-backups\scrapyrealestate.sqlite3 --replace
```

Restore refuses to overwrite the configured database unless `--replace` is explicit,
validates both source and restored copies with SQLite's integrity check, and must not
run alongside the application. In Docker, with the Compose service stopped, the same
commands can run against its mounted volume:

```powershell
docker compose run --rm --no-deps --entrypoint python scrapyrealestate `
  -m scrapyrealestate.maintenance backup
docker compose run --rm --no-deps --entrypoint python scrapyrealestate `
  -m scrapyrealestate.maintenance restore `
  /var/lib/scrapyrealestate/backups/manual-<timestamp>.sqlite3 --replace
```

Keep an off-volume copy as well. To archive all application state, stop the service
so no writes are in flight, then archive the named volume (including SQLite files,
automatic snapshots, and legacy import sources). In PowerShell:

```powershell
docker compose stop
docker run --rm -v scrapyrealestate-data:/data -v "${PWD}:/backup" alpine `
  tar -C /data -czf /backup/scrapyrealestate-backup.tgz .
docker compose start
```

To restore, stop the service, remove the current volume only after confirming that
the archive is usable, create a new empty volume of the same name, and extract the
archive into it:

```powershell
docker compose down
docker volume rm scrapyrealestate-data
docker volume create scrapyrealestate-data
docker run --rm -v scrapyrealestate-data:/data -v "${PWD}:/backup" alpine `
  sh -c "tar -C /data -xzf /backup/scrapyrealestate-backup.tgz && chown -R 10001:10001 /data"
docker compose up --build -d
```

Use the value of `SCRAPYREALESTATE_DATA_VOLUME` in place of
`scrapyrealestate-data` when you configured one. `docker compose down` preserves
named volumes; `docker compose down -v` deletes them and must not be used for normal
updates.

For an update, take a backup, obtain the desired source revision, then rebuild and
recreate the service:

```powershell
git pull
docker compose build --pull
docker compose up -d --force-recreate
docker compose ps
```

SQLite migrations run automatically at startup and are forward-only. A code rollback
is safe only if the earlier revision supports the existing database schema. If it
does not, stop the service, restore the pre-update volume backup, check out the
earlier revision, and run `docker compose up --build -d`. Never attempt to edit or
reverse migration records in place.

### Deployment verification

The ordinary test suite remains offline. On a Docker host, run the deployment smoke
test after changing the image, Compose file, or persistence lifecycle:

```powershell
$env:SCRAPYREALESTATE_RUN_DOCKER_SMOKE = "1"
python -m pytest -m deployment
```

It proves that an imported saved search and legacy-seen history survive an idle
stop/start and a container recreation, while `/readyz` remains available. The
offline lifecycle tests also exercise the active-crawl shutdown path: they confirm
the application stops new launches, drains a running child briefly, kills its
process group when needed, and closes cleanly. For a release with an actual portal
crawl in progress, trigger a manual run from the UI, issue `docker compose stop`,
wait for it to return, then inspect `docker compose ps` and start the service again.
There must be no running service container before the restart and its readiness
endpoint must return successfully afterwards.

The deterministic container soak is separately opt-in. It builds the image, runs
three independently scheduled fixture searches for 20 cycles in an isolated volume,
and checks overlap rejection, SQLite integrity, delivery uniqueness, and process
cleanup:

```powershell
$env:SCRAPYREALESTATE_RUN_DOCKER_SOAK = "1"
python -m pytest tests/test_container_soak.py -m soak
```

The latest passing result is
[docs/container-soak-2026-09-07.md](docs/container-soak-2026-09-07.md).

## Runtime and data

`python main.py` delegates to the persistent bootstrap. Startup creates the data
directory, applies ordered SQLite migrations, idempotently imports preserved
legacy `config.json` and `ids.json` files when present, then starts Waitress and the
scheduler. `SIGINT`/`SIGTERM` stop new dispatches, allow bounded crawler cleanup,
and close the database.

The data directory can contain:

- `scrapyrealestate.sqlite3`: saved searches, listings, prices, runs, events, channels,
  delivery attempts, schedules, and migration state;
- `config.json` and `ids.json`: preserved legacy import sources;
- `useragent.txt`: Scrapy User-Agent input;
- `runs/`: unique JSON Lines output for isolated portal attempts;
- `backups/`: manual and automatic pre-migration SQLite snapshots;
- `live-smoke-report.json` and `test_<portal>.*`: ignored opt-in live-probe results;
- `soak-report.json`: ignored deterministic soak summary when run in that data directory.

Operational maintenance clears diagnostic text after 30 days and removes terminal
delivery-attempt rows after 90 days or above the newest 10,000 records. Pending and
leased deliveries are retained, as are listing, match, event, and price histories.

### Configuración y deduplicación

La configuración autoritativa se guarda en SQLite. La deduplicación utiliza la
identidad externa o URL canónica dentro de cada portal, y conserva por separado las
coincidencias de cada búsqueda. Los JSON heredados solo son fuentes de importación
compatibles y no vuelven a ser el estado principal de la aplicación.

Notification credentials are user supplied. There is no shared Telegram token.
Ordinary channel reads and templates receive masked values; raw credentials are
available only to delivery-scoped services.

## Portal status

| Portal | Transport | Notes |
| --- | --- | --- |
| Pisos.com | Scrapy HTTP | Simplest maintained HTML target. |
| Habitaclia | Scrapy HTTP | Uses the stable detail-URL identifier where available. |
| Fotocasa | Playwright | Parses embedded initial JSON; site structure may change. |
| Yaencontre | Playwright | Rendered requests are needed because plain requests can return 403; the 2026-09-07 probe found a likely selector/site change, so verify before enabling. |
| Idealista | Playwright | Degraded; DataDome commonly blocks headless automation. |
| Idealista proxy | Rotating public proxies | Degraded and inherently unreliable. |

Use respectful intervals (the persisted minimum is five minutes), review each
portal's terms, and do not treat live portal access as a deterministic regression
test.

The 2026-09-07 enabled-portal smoke run returned non-empty results for Pisos.com,
Habitaclia, and Fotocasa. Yaencontre timed out waiting for its rendered card selector;
this is recorded as `site_change`, while Idealista was not enabled in that deployment
and retains its explicit degraded status.

## Credits and license

Based on [mferark/scrapyrealestate](https://github.com/mferark/scrapyrealestate).
Licensed under GPL-3.0.
