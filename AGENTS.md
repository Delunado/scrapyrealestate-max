# Repository Guide for Coding Agents

## Purpose and current maturity

ScrapyRealEstate monitors Spanish property portals, periodically scrapes configured
search-result URLs, and notifies a Telegram channel about newly seen listings. The
current `master` branch is a small script-oriented application, not yet the
persistent multi-search product described in `TASKS.md`.

Preserve working spiders while evolving the system incrementally. Prefer the
standard library, Flask/Jinja, Scrapy, Playwright only where necessary, and SQLite.
Do not add PostgreSQL, Redis, Celery, MongoDB, a separate SPA, or distributed
infrastructure without a concrete requirement and an explicit update to
`TASKS.md`.

## Repository layout

- `scrapyrealestate/main.py`: current process entrypoint. It loads first-run JSON
  configuration, validates Telegram, runs spiders as subprocesses, merges their
  JSON output, performs price filtering and ID deduplication, sends Telegram
  messages, and sleeps forever.
- `scrapyrealestate/scrapy.cfg`: Scrapy project marker. Scrapy commands must run
  from this directory unless `SCRAPY_SETTINGS_MODULE` is set explicitly.
- `scrapyrealestate/scrapyrealestate/settings.py`: shared Scrapy and Playwright
  configuration. It reads the current User-Agent from `./data/useragent.txt`.
- `scrapyrealestate/scrapyrealestate/items.py`: current loose Scrapy item contract.
- `scrapyrealestate/scrapyrealestate/domain/`: normalized value enums, listing and
  search models, Spanish display-value normalization, the transitional legacy item
  mapper, and explicit three-state local filter evaluation. The legacy runtime does
  not consume this boundary yet.
- `scrapyrealestate/scrapyrealestate/persistence/`: configured SQLite connections,
  explicit transactions, and the ordered migration infrastructure/schema.
- `scrapyrealestate/scrapyrealestate/spiders/`: one spider module per portal plus
  the optional Idealista proxy variant.
- `scrapyrealestate/scrapyrealestate/flask_server.py`: current first-run-only Flask
  server. It writes `data/config.json`; `main.py` then terminates it.
- `scrapyrealestate/scrapyrealestate/templates/`: current unstyled first-run form
  and confirmation page.
- `scrapyrealestate/scrapyrealestate/proxies.py`: downloads public HTTPS proxies
  for `idealista_proxy`; this source is inherently unreliable.
- `scrapyrealestate/test_spider.sh`: opt-in live crawl helper. It retains a crawl
  log and classifies non-empty success, valid empty output, parser failure,
  transport failure, and likely blocking. Live portal behavior is not a
  deterministic regression test.
- `Dockerfile`: Python 3.12 image with Chromium and `tini`; its working directory is
  `/scrapyrealestate/scrapyrealestate`.
- `docker-compose.yml`: current published-image deployment. It exposes port 8080
  but does not mount persistent data or define a healthcheck.
- `README.md`: current user-facing behavior and deployment instructions.
- `TASKS.md`: canonical ordered improvement plan. Read it before making changes.

There is currently no package metadata or type checker. SQLite infrastructure is
being introduced incrementally and is not consumed by the legacy runtime yet.
Offline tests use pytest with shared fixtures in `tests/`, and Ruff
enforces the focused Python 3.12 lint baseline configured in `pyproject.toml`.
GitHub Actions runs tests, lint, and Compose validation without live portal access.

## Current runtime flow

Run `python main.py` from the inner `scrapyrealestate/` directory. The process:

1. creates `./data/` if `data/config.json` is absent;
2. launches `scrapyrealestate/flask_server.py` as a child process and waits for the
   form to create `config.json`;
3. stops the Flask child, configures logging, checks the 300-second minimum interval,
   and sends a Telegram startup/validation message;
4. refreshes `data/useragent.txt` every ten cycles;
5. randomizes all configured raw portal URLs and selects a spider with a domain
   `if/elif` chain;
6. invokes `scrapy crawl` once per URL, appending every crawl to a shared JSON export;
7. repairs concatenated JSON arrays, filters only by global min/max price, compares
   integer listing IDs against `data/ids.json`, and sends new matches to Telegram;
8. sleeps for the configured interval plus 3–40 seconds and repeats.

Subprocess exit codes are currently ignored. A failed portal can therefore look like
an empty result, and no per-portal run status is retained. The current process also
requires valid Telegram configuration to stay alive.

## Current configuration and persistence

Runtime paths are resolved centrally by `scrapyrealestate/runtime.py`. The
`SCRAPYREALESTATE_DATA_DIR` environment variable accepts an absolute data
directory; its compatibility default is `./data` resolved from the runtime working
directory, normally `scrapyrealestate/` locally and
`/scrapyrealestate/scrapyrealestate` in the image. The ignored
`scrapyrealestate/data/` directory may contain:

- `config.json`: one global search and runtime/Telegram settings;
- `ids.json`: a flat global list of notified integer IDs, without portal names;
- `useragent.txt`: generated browser User-Agent;
- `<scrapy_rs_name>.json`: temporary aggregate crawl output;
- `test_<spider>.json` and `test_<spider>.log`: manual live-test output and the
  retained log used to classify the crawl result.

Legacy `config.json` is loaded and validated as typed configuration by
`scrapyrealestate/legacy_config.py`. Current JSON keys are `scrapy_rs_name`, `log_level`,
`log_level_scrapy`, `time_update`, `telegram_chatuserID`,
`telegram_bot_token`, `start_msg`, `min_price`, `max_price`,
`proxy_idealista`, `send_first`, and a list-valued `url_<portal>` key for each
portal. Form values, including booleans and numbers, are stored as strings and are
converted at the loading boundary; portal URL fields accept their current list
form and older scalar strings.

Legacy `config.json` and `ids.json` writes use same-directory temporary files and
atomic replacement through `scrapyrealestate/atomic_files.py`. Keep legacy writes
on this helper until SQLite becomes authoritative.

Do not change the meaning of legacy files before the SQLite importer and rollback
path in `TASKS.md` exist. Import must be idempotent, preserve source files, and
record ambiguity: legacy IDs cannot always be assigned to a portal because
`ids.json` has no portal field.

The target persistent path is one configurable data directory containing the
SQLite database and any non-database runtime files, mounted into Docker. Resolve it
through one path/configuration module rather than adding new `./data` literals.
SQLite changes must use ordered, transactional, forward-only migrations tracked in
the database; never mutate a deployed schema opportunistically at startup.

## Spiders and current portal behavior

All spiders currently emit `ScrapyrealestateItem`. Portal parsing quirks belong in
their spider or future adapter, never in web, scheduling, persistence, or notifier
code.

| Portal | Spider | Transport | Current caveats |
| --- | --- | --- | --- |
| Pisos.com | `pisoscom` | normal Scrapy HTTP | HTML/CSS selectors; currently considered the simplest maintained target. |
| Habitaclia | `habitaclia` | normal Scrapy HTTP | HTML/CSS selectors; prefers the stable `-i<id>` detail-URL identifier and uses a numeric canonical-URL fingerprint only when that marker is absent. |
| Fotocasa | `fotocasa` | Playwright/Chromium | Parses `script#__initial_props__`; wait/JSON structure may change. |
| Yaencontre | `yaencontre` | Playwright/Chromium | Plain requests have returned 403; relies on rendered card selectors. |
| Idealista | `idealista` | Playwright/Chromium | DataDome commonly blocks headless automation; treat as externally unreliable. |
| Idealista proxy | `idealista_proxy` | Scrapy HTTP + public rotating proxies | Public proxy discovery is slow/unreliable and is not a supported anti-bot guarantee. |

`start_urls` is passed as a spider argument containing one URL string; each spider
overrides `start_requests`, so do not assume Scrapy's normal class-level list.
Fotocasa and Yaencontre require the asyncio reactor and Playwright download handlers
in `settings.py`. Keep Playwright opt-in through request metadata for browser portals;
do not make normal HTML spiders launch Chromium.

The current item fields are `id`, `price`, `m2`, `rooms`, `floor`, `town`,
`neighbour`, `street`, `number`, `type`, `title`, `href`, `site`, and declared but
usually unset `post_time`. Values are mostly display strings and may be missing,
empty, malformed, or portal-specific.

The target normalized listing contract must make `portal`, `external_id`,
`canonical_url`, and `title` explicit; normalized numeric and boolean fields must
be typed and nullable; original/raw values may be retained for diagnostics. Money
uses integer euros and area uses square metres. Empty strings are not domain-level
null values. A portal plus external ID is the preferred identity; canonical URL is
the fallback. Validate this contract at the spider/adapter boundary.

The initial contract is implemented in `domain/`. `NormalizedListing` requires a
typed portal and transaction, a non-empty title, and either an external ID or an
absolute HTTP(S) canonical URL. It stores aware timestamps in UTC, preserves legacy
source values as read-only diagnostics, and represents unknown amenities with
`TriState.UNKNOWN`. `SearchFilters` keeps absent constraints as `None`; local
evaluation reports `match`, `no_match`, or `unknown` per active filter and gives a
definite non-match precedence over unknown fields. Use `map_legacy_item` while
spiders still emit `ScrapyrealestateItem`.

## Portal adapter conventions

The adapter layer in `TASKS.md` will be introduced alongside the current dispatch
before replacing it. Each adapter should declare a stable key, display name,
domains, spider name, transaction types, browser requirement, operational caveats,
and filters it can translate remotely. It should build/validate the portal request
and normalize spider output.

Use a registry for lookup by stable key or hostname. Adding a portal must not add a
new central `if/elif`. Explicitly report unsupported filters and distinguish:

- remote filters encoded in a portal URL/request;
- local filters applied to normalized results;
- unsupported filters that cannot be evaluated reliably.

Do not silently ignore a requested filter. Keep raw search-URL overrides during the
legacy migration, but validate that their host matches the selected adapter. New
portal work requires saved response fixtures, parser/contract tests, metadata and
capability tests, status reporting, and an update to the portal table in this file.
Assess a portal before integrating it; strong anti-bot bypasses and paid solving
services are out of scope by default.

## Application boundaries to preserve

New code should maintain these practical boundaries without creating needless
micro-modules:

- domain: normalized searches, filters, listings, and events;
- persistence: SQLite connections, migrations, and repositories;
- portals: metadata, URL/request translation, spiders, and result normalization;
- execution: isolated spider subprocesses and per-portal outcomes;
- services: search orchestration, change detection, notification routing, and
  scheduling;
- notifiers: provider implementations behind one interface;
- web: Flask routes/forms/templates with no scraping or SQL details;
- bootstrap: configuration, lifecycle, and graceful shutdown only.

Do not grow `main.py` or replace it with another monolith. During migration, add new
components beside the legacy flow, cover them with tests, then switch the entrypoint
in one explicit task.

## Web, scheduler, and notifier conventions

The current Flask server is not persistent and is not a production server. The
target is one long-lived server-rendered Flask/Jinja application in the same main
service as a lightweight in-process scheduler. Keep route handlers thin and use
Post/Redirect/Get for mutations. State-changing forms require CSRF protection.

Scheduler state and run history belong in SQLite. Prevent overlapping runs per
search, isolate every portal attempt, use explicit timeouts, and continue after one
portal fails. Manual and scheduled runs must enter the same orchestration path.
Handle SIGTERM/SIGINT, stop scheduling new work, and allow bounded cleanup of child
processes.

Business logic emits provider-neutral events. Notifier implementations format and
deliver them. Telegram must not be imported by spiders, persistence, scheduler, or
domain modules. Add ntfy and generic HTTP webhook behind the same interface. Record
delivery attempts sufficiently to avoid duplicate delivery on restart.

Telegram requires a user-supplied token in legacy configuration or the
`TELEGRAM_BOT_TOKEN` environment override; there is no shared fallback credential.
Never expose user-provided secrets in new code, logs, tests, documentation, status
responses, or commits. Secrets persisted for UI editing in the local data store
must be masked on read forms, excluded from normal object representations, and
redacted from logs and error details. Environment-based secret overrides remain
available for unattended deployments.

## Tests and checks

The checked-in project also has a manual live spider script. Use the following
offline checks:

```bash
# From the repository root: syntax-only, no portal access
python -m compileall -q scrapyrealestate

# From the repository root: offline unit and import smoke tests
python -m pytest

# From the repository root: focused Python 3.12 lint baseline
python -m ruff check .

# From scrapyrealestate/: requires installed project dependencies
scrapy list

# From the repository root: validates Compose syntax; no daemon required
docker compose config --quiet
```

Run an individual live spider from `scrapyrealestate/`:

```bash
./test_spider.sh pisoscom
./test_spider.sh fotocasa 'https://www.fotocasa.es/...'
```

In the current container:

```bash
docker exec -it scrapyrealestate bash -c \
  'cd /scrapyrealestate/scrapyrealestate && ./test_spider.sh pisoscom'
```

Live tests require network access and may fail because a portal changed or blocked
the request. They must remain opt-in and must not be the sole parser tests. The
normal offline test command is `python -m pytest`; follow the checked-in tool
configuration for lint/type commands rather than inventing local flags. Keep saved
HTML/JSON fixtures small, sanitized, and free of secrets.

Every behavior change needs focused offline tests. Database tests use temporary
databases and real migrations. Parser tests use sanitized UTF-8 snapshots under
`tests/fixtures/`; fixture-based portal coverage is in the corresponding
`tests/test_<portal>_spider.py` modules. Adapter
contract tests must verify all registered adapters. Mock notifier HTTP boundaries;
do not send real messages during normal tests.

## Docker and deployment rules

Maintain one main Compose service unless a demonstrated limitation requires more.
The image needs Chromium only for adapters that use Playwright, an init/reaping
strategy (`tini` is already present), an unprivileged runtime user when feasible,
a healthcheck, a configurable host port, and one documented persistent data mount.
Do not expose Scrapy or debug ports.

The current Compose file uses the published image and ephemeral container storage.
Do not claim upgrades are persistent until the volume task is implemented and
tested by recreating the container. Keep stdout/stderr logs useful for `docker
compose logs`; do not add an internal monitoring stack.

## Git and task workflow

1. Read this file and `TASKS.md`, then inspect `git status --short` before editing.
2. Work on the first unchecked task unless the user explicitly reprioritizes it.
3. Implement only that task and strictly necessary support. Preserve unrelated user
   changes in a dirty worktree.
4. Run focused tests plus the proportionate broader checks documented here.
5. Update `TASKS.md` to mark the task complete. If discoveries change the plan,
   explain and reorder future tasks without removing completed history.
6. Update this guide whenever paths, commands, contracts, or architectural decisions
   change.
7. Commit the coherent task with a descriptive message. Normally use exactly one
   commit per checked task; do not batch unrelated checkboxes.
8. Continue with the next unchecked task only after the repository is usable.

Never mark a task complete because code was written alone. Required checks must pass,
or the task must remain unchecked with the concrete blocker documented. Do not commit
generated `data/`, live crawl output, credentials, `.env`, browser artifacts, caches,
or local editor files.

## Known limitations and migration hazards

- Third-party markup and embedded JSON are unstable; fixtures show parser intent,
  while live checks diagnose external drift.
- Idealista/DataDome is a known unreliable target. Do not make overall health depend
  on it and do not interpret blocking as an application regression.
- Listing IDs are not globally unique today. The SQLite identity must include the
  portal, and legacy unscoped IDs need conservative import semantics.
- Habitaclia normally exposes a stable ID in its detail URL. Unusual URLs without
  the `-i<id>` marker fall back to a deterministic 63-bit fingerprint of the
  canonical URL without its query or fragment; this remains a conservative legacy
  compatibility path rather than a portal-guaranteed identifier.
- A zero-result crawl may mean no listings, a selector regression, timeout, 403, or
  anti-bot challenge. Execution records must distinguish these cases where evidence
  permits.
- Current raw URL suffix concatenation can duplicate slashes or query strings. URL
  construction belongs in adapters and requires tests.
- Current notification and dedup writes are not atomic. SQLite ingestion and event
  creation must be transactional before JSON state is retired.
- User-facing README, template, and runtime text is UTF-8 and has encoding regression
  coverage. Keep new text UTF-8 and do not mix broad wording cleanup into unrelated
  behavior tasks.
