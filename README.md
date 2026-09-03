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
- safe test notifications with persisted, redacted outcomes.

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
directory, where `scrapy.cfg` lives:

```bash
./test_spider.sh pisoscom
./test_spider.sh fotocasa 'https://www.fotocasa.es/...'
```

## Docker status

The current `docker-compose.yml` still uses the published Docker Hub image and does
not mount persistent data. It therefore does **not** build or test uncommitted Phase
8 changes from this checkout. For a one-off container test of this source tree:

```powershell
docker build -t scrapyrealestate-local .
docker run --rm --init -p 8080:8080 scrapyrealestate-local
```

Data in that one-off container is ephemeral. Persistent Compose volumes,
healthchecks, configurable host ports, least-privileged image execution, and the
complete backup/update procedure are intentionally tracked in Phase 10 of
`TASKS.md`.

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
- `runs/`: unique JSON Lines output for isolated portal attempts.

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
| Yaencontre | Playwright | Rendered requests are needed because plain requests can return 403. |
| Idealista | Playwright | Degraded; DataDome commonly blocks headless automation. |
| Idealista proxy | Rotating public proxies | Degraded and inherently unreliable. |

Use respectful intervals (the persisted minimum is five minutes), review each
portal's terms, and do not treat live portal access as a deterministic regression
test.

## Credits and license

Based on [mferark/scrapyrealestate](https://github.com/mferark/scrapyrealestate).
Licensed under GPL-3.0.
