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
  explicit transactions, ordered migrations, typed repositories for searches,
  listings, prices, runs, and notifications, plus idempotent legacy importers and
  secret-free migration reports.
- `scrapyrealestate/scrapyrealestate/spiders/`: one spider module per portal plus
  the optional Idealista proxy variant.
- `scrapyrealestate/scrapyrealestate/portals/`: the `PortalAdapter` interface and
  `PortalMetadata`/`PortalRequest` contract (`base.py`), plus per-portal adapters
  that validate a legacy raw search URL, build a recent-sort crawl request, and
  normalize spider output around the existing spiders above. Not yet consumed by
  `main.py`'s dispatcher.
- `scrapyrealestate/scrapyrealestate/execution/`: isolated per-portal spider
  execution, built beside the legacy flow and not yet consumed by `main.py`.
  `execution/contract.py` defines `PortalRunRequest` (one crawl-ready attempt
  bound to its own output file) and `PortalRunResult` (the operational
  outcome, typed against `domain.values.RunStatus`: `success`, `empty`,
  `timeout`, `transport_error`, `parser_error`, `blocked`, `unavailable`); a
  non-conclusive result — anything but `success`/`empty` — cannot carry items.
  `execution/output.py` strictly decodes one attempt's JSON Lines output
  (`OutputDecodeError` on a malformed or non-object line; a missing file is
  a legitimate empty result). `RuntimePaths.attempt_output(label)`
  (`runtime.py`) hands out a unique `data/runs/<label>-<uuid>.jl` path per
  attempt, so — once wired into the runner — every attempt writes its own
  file and the legacy concatenated-JSON-array repair step
  (`main.scrap_realestate`'s `\n][` -> `,` patch) becomes unnecessary rather
  than needing a stricter parser for the same shared-file shape.
  `execution/runner.py`'s `SpiderRunner` replaces `main.run_spider`'s bare
  `subprocess.run(..., check=False)`: it applies `PortalRunRequest.
  timeout_seconds`, classifies a non-zero return code as `transport_error`
  and a `read_jsonl_items` decode failure as `parser_error`, bounds
  captured stderr to a fixed byte budget as the result's diagnostic, and
  best-effort kills the child (its whole process group on POSIX) on
  timeout. `run()` never raises — every failure mode becomes a
  `PortalRunResult`. Its subprocess command is pluggable
  (`build_command`) precisely so tests never need Scrapy installed; see
  `tests/fixtures/execution/fake_spider.py`.
  `execution/attempt.py`'s `run_portal_attempt` wraps adapter request
  building (`build_request`/`build_request_from_search`) around
  `SpiderRunner.run`: a `PortalRequestError` or any other unexpected
  exception while resolving the request becomes an `UNAVAILABLE` result,
  and an unexpected exception from the runner itself becomes
  `TRANSPORT_ERROR` — the function itself never raises. This is the
  per-portal guarantee `services.search_orchestration`'s multi-portal loop
  depends on.
- `scrapyrealestate/scrapyrealestate/services/`: cross-cutting services built
  on `execution/` and `persistence/`, beside the legacy flow and not yet
  consumed by `main.py`. `services/search_orchestration.py`'s
  `SearchOrchestrationService.run_search(search_record, trigger)` first
  acquires `services/locks.py`'s `SearchRunLock` for this search — a second
  call for the same search while one is in flight raises
  `SearchAlreadyRunningError` immediately, without creating a run record;
  independent searches never wait on each other, since the lock is
  per-search-id and process-local (in-memory, not a DB lock). It then
  records one `search_runs` row and, for every *enabled*
  `SearchPortalRecord` in randomized order (`random_source.shuffle`,
  injectable for deterministic tests; defaults to a fresh `random.Random`)
  separated by a configurable `inter_portal_delay_seconds` (default `0.0`
  so offline tests stay instant; real deployments should pass a respectful
  positive value), resolves its adapter from the `PortalRegistry` (an
  unregistered portal key is recorded as `UNAVAILABLE`, not raised), runs
  one attempt via `execution.run_portal_attempt`, normalizes whatever it
  returned (`adapter.normalize_result`, skipping any single malformed item
  rather than failing the whole attempt), and keeps every listing that is
  not a definite local non-match (`domain.filtering.evaluate_listing`; an
  `unknown` outcome is kept, not excluded, so missing data never silently
  narrows a search's results). A conclusive attempt's matched listings are
  then handed to `services/ingestion.py`'s `IngestionService.ingest_attempt`,
  which — as one SQLite transaction — upserts each listing and its
  per-search match (`ListingMatchRepository.ingest_locked`), records a price
  observation when a price is known (`PriceHistoryRepository`), raises
  provider-neutral `new_listing`/`price_drop`/`price_increase`/`reappearance`
  events (`NotificationRepository`), and reconciles which previously-active
  listings on this portal were not seen this time
  (`ListingMatchRepository.reconcile_portal_locked`). A listing-identity
  conflict or any other ingestion failure rolls the whole batch back and is
  recorded on that attempt as `error_category="ingestion_error"` — the
  portal's own fetch `status` is left untouched, since the fetch itself
  already succeeded — and never raises out of the run; the `ingest_locked`
  / `reconcile_portal_locked` methods exist because `persistence/database.py`'s
  `transaction()` does not support nesting, so `IngestionService` opens the
  one outer transaction itself and calls the lock-free variants. Every
  attempt — success, failure, or ingestion failure — is recorded through
  `RunRepository` (including `new`/`changed` counts from `IngestionOutcome`
  when ingestion ran), and so is the overall run: `SUCCESS` when every
  attempted portal was conclusive (`success`/`empty`), `PARTIAL` when only
  some were, `FAILED` when none were conclusive or no portal was enabled at
  all — this status reflects portal fetch outcomes only, not ingestion.
- `scrapyrealestate/scrapyrealestate/notifiers/`: provider implementations share
  the synchronous `Notifier`/`DeliveryResult` boundary and the bounded plain-text
  formatter. `domain.notification.NotificationEvent` is the provider-neutral read
  model for all four event types; notifier adapters must not enable HTML/Markdown
  parsing for listing-supplied text. `domain.notification.NotificationPreferences`
  enables new-listing and price-drop events by default; overrides for all four event
  types are stored per search in `search_notification_preferences` and applied by
  `NotificationRepository.select_enabled_events`. `notifiers/telegram.py` sends
  that shared plain text through a user-configured bot and chat only; it returns a
  classified `DeliveryResult` and never exposes provider exception text.
  `notifiers/ntfy.py` publishes the same content as JSON to a configurable public
  or self-hosted server/topic, with optional bearer authentication and a mandatory
  bounded timeout; response bodies and exception text never enter diagnostics.
  `notifiers/webhook.py` posts a stable `1.0` JSON envelope to an HTTP(S) endpoint,
  supports an optional secret `Authorization` value, and follows the same timeout
  and safe-diagnostic rules. `notifiers/registry.py` builds these adapters from
  persisted channel configuration. `services/notification_routing.py` applies the
  originating search's event preferences and routes only to its enabled assigned
  channels, isolating configuration/provider failures per channel. Raw credentials
  are exposed only by the explicitly delivery-scoped repository read and remain
  excluded from object representations. `services/notification_delivery.py` is the
  durable path: it idempotently creates one initial event/channel attempt, acquires
  an atomic token/lease claim, hydrates and sends the event, records success or a
  bounded failure diagnostic, and schedules at most three exponential-backoff
  attempts by default. Successful pairs are never claimable again; an expired
  in-flight lease is reclaimable after restart (external providers remain
  inherently at-least-once if a process dies after sending but before recording).
  Both immediate and durable paths defensively redact every provider-controlled
  result field against all nested channel secrets before returning or persisting
  it; ordinary channel reads stay masked for logs, status views, and templates.
  Ingestion creates the first eligible delivery attempts in the same SQLite
  transaction as each new event. `SearchOrchestrationService` then drains durable
  attempts through the provider registry after conclusive ingestion; delivery
  failures are retained for retry but never change portal or search-run status.
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
5. randomizes all configured raw portal URLs and resolves each one's spider and
   recent-sort request through `portals.build_default_registry()` (hostname lookup,
   Idealista's proxy/Playwright choice still config-driven via `proxy_idealista`);
   a URL with no registered hostname or an unresolvable transaction type is logged
   and skipped rather than raised;
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

Legacy imports record SHA-256 rollback-source markers in `legacy_import_reports` and
leave `config.json` and `ids.json` untouched. `legacy_seen_ids` is intentionally
portal-unscoped: matching a numeric external ID suppresses a repeat first
notification conservatively, but never creates a normalized listing or assigns the
legacy ID to a portal.

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
the fallback. Validate this contract at the spider/adapter boundary. SQLite enforces
partial unique indexes on `(portal_key, external_id)` and
`(portal_key, canonical_url)` when those values are present, keeping both identities
portal-scoped.

The initial contract is implemented in `domain/`. `NormalizedListing` requires a
typed portal and transaction, a non-empty title, and either an external ID or an
absolute HTTP(S) canonical URL. It stores aware timestamps in UTC, preserves legacy
source values as read-only diagnostics, and represents unknown amenities with
`TriState.UNKNOWN`. `SearchFilters` keeps absent constraints as `None`; local
evaluation reports `match`, `no_match`, or `unknown` per active filter and gives a
definite non-match precedence over unknown fields. Use `map_legacy_item` while
spiders still emit `ScrapyrealestateItem`.

## Portal adapter conventions

The adapter layer lives in `portals/`; `main.py` dispatches through it (see below)
rather than a domain `if/elif` chain. `PortalAdapter` (`portals/base.py`) declares a
stable key, display name, domains, spider name, transaction types, transport
(`PortalTransport.HTTP` / `PLAYWRIGHT` / `ROTATING_PROXY_HTTP`, which also implies
the browser requirement), operational caveats, a `degraded` flag for portals with
no anti-bot bypass guarantee, and `FilterCapabilities`. `BasePortalAdapter` shares
domain/transaction validation and recent-sort URL construction so each concrete
adapter only supplies metadata plus its two portal-specific hooks; it also
delegates result normalization to `map_legacy_item`. Until per-portal remote URL
filter encoding exists (a later `TASKS.md` item), every adapter declares
`ALL_LOCAL_CAPABILITIES` rather than guessing ahead of an actual request builder.

`portals/registry.py` provides `PortalRegistry` for lookup by stable key
(`get`) or normalized hostname (`get_by_hostname`, case-insensitive and
`www.`-agnostic); registration rejects a duplicate portal key or a domain
already claimed by another adapter. `portals.build_default_registry(*,
idealista_proxy=False)` builds the registry `main.py` actually consults:
`PisoscomAdapter`, `HabitacliaAdapter`, `FotocasaAdapter`, `YaencontreAdapter`,
and one Idealista adapter chosen by the `idealista_proxy` flag (never both —
they share `idealista.com`). `main.py`'s `scrap_realestate` resolves each raw
URL's adapter with `registry.get_by_hostname`, builds its request with
`adapter.build_request`, and skips (with a logged warning) a URL whose
hostname is unregistered or whose transaction type the adapter cannot infer,
rather than raising out of the run. Adding a portal must not add a new
central `if/elif`; register its adapter in `build_default_registry` instead.
Explicitly report unsupported filters and distinguish:

- remote filters encoded in a portal URL/request;
- local filters applied to normalized results;
- unsupported filters that cannot be evaluated reliably.

Do not silently ignore a requested filter. Keep raw search-URL overrides during the
legacy migration, but validate that their host matches the selected adapter. New
portal work requires saved response fixtures, parser/contract tests, metadata and
capability tests, status reporting, and an update to the portal table in this file.
Assess a portal before integrating it; strong anti-bot bypasses and paid solving
services are out of scope by default.

`domain.capabilities.report_capabilities(filters, capabilities)` (also exposed as
`PortalMetadata.report_capabilities(filters)`) classifies only the
`SearchFilterKey`s a given `SearchFilters` actually constrains (`None`, or an
empty `property_types`, means "not requested" and is omitted, never implied);
`domain.capabilities.active_filter_keys` returns that requested set on its
own. Use this — not `FilterCapabilities.to_dict()`, which describes a
portal's full capability independent of any one search — wherever code needs
to say what will actually happen to a particular search's filters.

`BasePortalAdapter.build_request_from_search(search: NormalizedSearch)` builds a
crawl-ready `PortalRequest` directly from a normalized search, with no
pre-existing raw URL: it validates the search's transaction type against
`metadata.transaction_types`, requires a non-empty `filters.location`, slugifies
it with `portals.location.slugify_location` (a best-effort, accent-stripping,
hyphenating transform — accurate for a plain municipality name, not for
portal-specific taxonomy codes such as Fotocasa's provincial-capital
`<city>-capital` slugs), and delegates the fixed URL template to each adapter's
`_build_search_url(transaction_type, location_slug)` hook. Only `location` is
encoded remotely this way; every filter, including location once results come
back, still goes through local evaluation, so an imprecise slug degrades to a
smaller/larger local-filtered result set rather than a silently wrong one. An
adapter that has not implemented `_build_search_url` raises `PortalRequestError`
explicitly (the shared default) instead of guessing.

`portals/pisoscom.py`, `habitaclia.py`, `fotocasa.py`, `yaencontre.py`, and
`idealista.py` implement one adapter per row of the portal table above, each with
its own fixture-backed contract tests
(`tests/test_<portal>_adapter.py`). `idealista.py` defines both `IdealistaAdapter`
(Playwright) and `IdealistaProxyAdapter` (rotating public proxies); both default
`degraded=True` and promise no anti-bot bypass. They intentionally share the
`idealista.com` domain, so `PortalRegistry.get_by_hostname` cannot resolve between
them; portal selection stays a config-driven choice (the current `proxy_idealista`
flag), never hostname routing, exactly as `main.py` already does today. Neither
Idealista adapter overrides `_build_search_url`, so `build_request_from_search`
raises the shared "not implemented" `PortalRequestError` for both: Idealista's
location taxonomy is a `<province>-<municipality>` pair (e.g. `madrid-madrid`
in its own fixtures), not one this codebase can safely derive from a single
free-text location without a province lookup table it does not have — guessing
`<slug>-<slug>` would silently misroute any municipality whose province is
named differently. The legacy-compatible `build_request(raw_url)` path is
unaffected.

### The complete adapter contract

Every portal integration is one `PortalAdapter` (in practice, one
`BasePortalAdapter` subclass) plus its wrapped spider, nothing else:

- `metadata: PortalMetadata` — `key` (`PortalKey`), `display_name`, `domains`,
  `spider_name` (must match the wrapped `scrapy.Spider.name`), `transaction_types`,
  `transport` (`PortalTransport`; `PLAYWRIGHT` implies `requires_browser`),
  `capabilities` (`FilterCapabilities`; use `ALL_LOCAL_CAPABILITIES` unless the
  adapter genuinely encodes a filter remotely), `caveats` (free text), and
  `degraded` (no anti-bot bypass guaranteed).
- `_transaction_type(raw_url) -> TransactionType | None` — mirror the wrapped
  spider's own URL parsing exactly; return `None` for anything the spider
  would not recognize.
- `_apply_recent_sort(raw_url) -> str` — return the most-recent-first URL,
  matching (or, where documented, deliberately fixing — see Pisos.com's
  double-slash fix) the legacy suffix.
- `_build_search_url(transaction_type, location_slug) -> str` *(optional)* —
  override to support `build_request_from_search`; the shared default raises
  `PortalRequestError` explicitly for adapters that do not (see Idealista).
- `build_request(raw_url)`, `normalize_result(item)`, and
  `build_request_from_search(search)` come from `BasePortalAdapter` and should
  not be overridden.

### Tested steps to add a new portal

1. Capture a sanitized response fixture under `tests/fixtures/<portal>/` and
   write `tests/test_<portal>_spider.py` locking down the spider's normalized
   fields and missing-field behavior (Phase 0 pattern) before touching the
   adapter layer.
2. Add `scrapyrealestate/spiders/<portal>_spider.py` if it does not exist yet,
   with a `name` matching the intended `spider_name`.
3. Add `portals/<portal>.py` with one `BasePortalAdapter` subclass implementing
   `metadata`, `_transaction_type`, and `_apply_recent_sort`; add `_build_search_url`
   too if a plain municipality slug is a safe enough location translation (skip
   it, explicitly, if the portal needs taxonomy data this codebase cannot derive
   — see Idealista).
4. Register the new adapter in `portals.build_default_registry()`
   (`portals/__init__.py`); never add a new central `if/elif` to `main.py`.
5. Add `tests/test_<portal>_adapter.py` covering: metadata identity/transport,
   `build_request` for every transaction type plus wrong-domain and
   unresolvable-transaction-type errors, `build_request_from_search` (or its
   explicit "not implemented" error, with a test proving that is deliberate),
   and `normalize_result` against `map_legacy_item` for the fixture's items.
6. Add the portal to the table in "Spiders and current portal behavior" above
   and to this contract's "adding a portal" list if it needs a documented
   exception (e.g. sharing a domain with another adapter, as Idealista does).
7. Run `python -m pytest`, `python -m ruff check .`, and (from
   `scrapyrealestate/`) `scrapy list` before checking off the task.

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
- SQLite listing ingestion and event creation (`services/ingestion.py`) are
  transactional today. The still-outstanding piece is Phase 6: routing those
  persisted events to notifiers with durable delivery/retry, replacing the
  legacy direct-Telegram, non-atomic JSON dedup path.
- User-facing README, template, and runtime text is UTF-8 and has encoding regression
  coverage. Keep new text UTF-8 and do not mix broad wording cleanup into unrelated
  behavior tasks.
