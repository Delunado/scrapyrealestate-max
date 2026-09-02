# ScrapyRealEstate Improvement Plan

This is the canonical execution plan. Complete tasks in order unless a discovery or
explicit user priority requires the future plan to change. Each checkbox is intended
to be one coherent, tested commit. Complexity labels describe engineering effort;
`Externally unreliable` describes dependence on third-party portal behavior rather
than code complexity.

## Phase 0 — Repository baseline and working agreements

- [x] **Easy** — Audit the repository and relevant Git history, then document the current architecture, operational constraints, migration hazards, agent workflow, and ordered roadmap in `AGENTS.md` and `TASKS.md`.
- [x] **Easy** — Add an offline pytest scaffold, shared temporary-data fixtures, and smoke tests for importing application modules without performing network calls.
- [x] **Easy** — Add focused Ruff configuration for the supported Python version, fix only baseline violations needed to enable it, and document the exact lint command.
- [x] **Easy** — Add a minimal CI workflow that runs offline unit tests, lint, and Compose configuration validation without launching live spiders.
- [x] **Medium** — Capture a sanitized Pisos.com HTML fixture and add parser tests that lock down its current normalized fields and missing-field behavior.
- [x] **Medium** — Capture a sanitized Habitaclia HTML fixture and add parser tests that lock down its current normalized fields and related-ad cutoff behavior.
- [x] **Medium** — Capture a sanitized Fotocasa embedded-JSON fixture and add parser tests for its current normalized fields and malformed/missing payload behavior.
- [x] **Medium** — Capture a sanitized Yaencontre rendered-HTML fixture and add parser tests that lock down its current normalized fields and missing-field behavior.
- [x] **Externally unreliable** — Add an Idealista blocked/challenge fixture and parser/error-path tests without requiring a successful live crawl.
- [x] **Easy** — Make `test_spider.sh` clearly separate successful non-empty output, valid empty output, parser failure, transport failure, and likely blocking while remaining an opt-in live tool.

## Phase 1 — Runtime configuration, paths, and security baseline

- [x] **Easy** — Introduce one runtime settings/path module with a configurable absolute data directory and compatibility defaults for local and container execution.
- [x] **Easy** — Route config, ID history, User-Agent, crawl output, and test output paths through the shared path module without changing behavior.
- [ ] **Medium** — Define and test typed legacy configuration loading, defaults, validation errors, and conversion of the current string booleans/numbers and list-valued URL fields.
- [ ] **Easy** — Remove the shared Telegram fallback credential, require an explicit user token or environment override, and redact secrets from logs and object representations.
- [ ] **Easy** — Replace direct `sys.exit` configuration paths with structured validation errors that a future web UI can display without killing the service.
- [ ] **Easy** — Correct mojibake in user-facing templates, README text, and runtime messages in a behavior-neutral encoding cleanup.
- [ ] **Medium** — Add atomic file-write helpers and use them for legacy configuration/ID files until SQLite becomes authoritative.

## Phase 2 — Normalized domain model and filtering

- [ ] **Medium** — Define transaction type, property type, tri-state amenity, portal key, and run-status value objects/enums with serialization tests.
- [ ] **Medium** — Define the normalized listing model, required/optional fields, UTC timestamp conventions, canonical URL rules, and raw-source diagnostics.
- [ ] **Medium** — Add locale-aware normalization helpers for euro prices, square metres, room/bath counts, floors, and nullable booleans with edge-case tests.
- [ ] **Medium** — Add a compatibility mapper from every current `ScrapyrealestateItem` field to the normalized listing model and reject records without usable identity.
- [ ] **Medium** — Define the normalized search/filter model, including price, area, rooms, bathrooms, location, neighbourhood, floor, elevator, terrace, garage, property type, and maximum price/m².
- [ ] **Medium** — Implement local filter evaluation over normalized listings with explicit `match`, `no match`, and `unknown/not evaluable` outcomes.
- [ ] **Easy** — Define filter-capability metadata that distinguishes remote, local, and unsupported filters and add serialization/contract tests.
- [ ] **Medium** — Update the Pisos.com spider to emit values compatible with the normalized boundary while retaining the legacy mapper during transition.
- [ ] **Medium** — Update the Habitaclia spider to emit values compatible with the normalized boundary while retaining the legacy mapper during transition.
- [ ] **Medium** — Update the Fotocasa spider to emit values compatible with the normalized boundary while retaining the legacy mapper during transition.
- [ ] **Medium** — Update the Yaencontre spider to emit values compatible with the normalized boundary while retaining the legacy mapper during transition.
- [ ] **Externally unreliable** — Update the Idealista spiders to emit values compatible with the normalized boundary using fixture-based coverage when live access is blocked.
- [ ] **Medium** — Fix Habitaclia identity to prefer a stable listing URL identifier and preserve a documented fallback that does not include mutable price.

## Phase 3 — SQLite persistence and explicit migrations

- [ ] **Medium** — Introduce a small SQLite connection layer with foreign keys, busy timeout, WAL behavior where supported, transaction helpers, and temporary-database tests.
- [ ] **Medium** — Add a transactional, forward-only migration runner with a schema-version table and failure/rollback tests.
- [ ] **Medium** — Add the migration for application settings, searches, and per-search schedules with constraints and UTC timestamps.
- [ ] **Medium** — Add the migration for search/portal selection, raw URL override, adapter options, and enabled state.
- [ ] **Medium** — Add the migration for normalized listings with portal-scoped identity, canonical URL uniqueness strategy, first/last-seen, and active state.
- [ ] **Medium** — Add the migration for many-to-many search/listing matches and per-search first/last-seen metadata.
- [ ] **Medium** — Add the migration for price history with currency, observed time, and uniqueness that prevents duplicate observations.
- [ ] **Medium** — Add the migration for search runs and per-portal attempts, including timing, counts, status, error category, and redacted diagnostic text.
- [ ] **Medium** — Add the migration for notification channels, search/channel assignments, provider-neutral events, and delivery attempts.
- [ ] **Medium** — Implement and test search CRUD repositories, including enable/disable and per-search schedule/portal updates.
- [ ] **Medium** — Implement transactional listing upsert and search-match repositories that return new, changed, reappeared, and unchanged outcomes.
- [ ] **Medium** — Implement price-history recording and price-drop/price-increase detection with idempotency tests.
- [ ] **Medium** — Implement disappearance/inactive detection scoped to successful search/portal runs so failed or blocked crawls never mark listings absent.
- [ ] **Medium** — Implement run/portal-attempt persistence and queries for latest status, duration, result counts, errors, and next scheduled execution.
- [ ] **Medium** — Implement notification channel/event/delivery repositories with masked secret reads and retry-safe uniqueness.
- [ ] **Hard** — Build an idempotent legacy importer for `config.json` that creates a named search, portal selections, schedule, filters, and Telegram channel while preserving the source file.
- [ ] **Hard** — Import `ids.json` into a conservative legacy-seen table, document its missing portal scope, and prevent repeat first-run floods without fabricating listing records.
- [ ] **Easy** — Add a migration report/backup marker and tests proving reruns do not duplicate imported configuration or history.

## Phase 4 — Portal adapter and registry architecture

- [ ] **Medium** — Define the portal adapter interface and metadata contract for identity, domains, spider, transaction types, browser requirement, capabilities, request building, and result normalization.
- [ ] **Medium** — Implement a registry with duplicate-key/domain validation and lookup by stable portal key or normalized hostname.
- [ ] **Medium** — Add the Pisos.com adapter around the existing spider, including URL validation, recent-sort construction, metadata, and contract tests.
- [ ] **Medium** — Add the Habitaclia adapter around the existing spider, including URL validation, recent-sort construction, metadata, and contract tests.
- [ ] **Medium** — Add the Fotocasa adapter around the existing Playwright spider, including embedded-JSON expectations, metadata, and contract tests.
- [ ] **Medium** — Add the Yaencontre adapter around the existing Playwright spider, including rendered-card expectations, metadata, and contract tests.
- [ ] **Externally unreliable** — Add the Idealista adapter and optional proxy transport metadata with a default degraded/unreliable status and no promise of anti-bot bypass.
- [ ] **Medium** — Replace domain parsing and the central portal `if/elif` dispatcher with registry lookup while keeping legacy raw URLs working.
- [ ] **Medium** — Add adapter capability reporting that identifies which requested filters are encoded remotely, evaluated locally, or unavailable.
- [ ] **Hard** — Implement and fixture-test normalized URL/request construction for common location and transaction filters in Pisos.com.
- [ ] **Hard** — Implement and fixture-test normalized URL/request construction for common location and transaction filters in Habitaclia.
- [ ] **Hard** — Implement and fixture-test normalized URL/request construction for common location and transaction filters in Fotocasa.
- [ ] **Hard** — Implement and fixture-test normalized URL/request construction for common location and transaction filters in Yaencontre.
- [ ] **Externally unreliable** — Implement only currently verifiable Idealista request translation; keep unsupported or blocked capabilities explicit.
- [ ] **Easy** — Document the final adapter contract and update `AGENTS.md` with the tested steps for adding a portal.

## Phase 5 — Isolated scraping execution and ingestion

- [ ] **Medium** — Define a portal execution request/result contract with status categories for success, empty, timeout, transport error, parser error, blocked, and unavailable.
- [ ] **Medium** — Replace append-and-repair JSON output with a unique per-attempt JSON Lines/temp output path and strict decoding tests.
- [ ] **Medium** — Add subprocess timeouts, return-code handling, bounded stderr capture, and child cleanup to the spider runner.
- [ ] **Medium** — Ensure one portal attempt failure is recorded and returned without raising out of the overall search run.
- [ ] **Medium** — Build a search orchestration service that resolves adapters, runs enabled portals, normalizes results, applies local filters, and records every attempt.
- [ ] **Hard** — Ingest a successful portal result transactionally into listings, search matches, active state, price history, and provider-neutral change events.
- [ ] **Medium** — Preserve randomized portal order and introduce configurable, respectful inter-portal delays without slowing offline tests.
- [ ] **Medium** — Add concurrency/locking guards that prevent overlapping executions of the same search while allowing independent searches to progress.
- [ ] **Medium** — Add orchestration tests covering mixed portal success/failure, duplicate results, unknown filter values, and transactional rollback.

## Phase 6 — Provider-neutral notifications

- [ ] **Medium** — Define notifier and notification-event interfaces for new listing, price drop, price increase, and reappearance, with shared safe formatting rules.
- [ ] **Medium** — Implement event selection/preferences per search so new listings and price drops are enabled by default and optional events remain configurable.
- [ ] **Medium** — Move Telegram delivery and message formatting behind the notifier interface using only user-supplied configuration.
- [ ] **Medium** — Implement an ntfy notifier with configurable server/topic/auth, HTTP timeouts, safe error handling, and mocked tests.
- [ ] **Medium** — Implement a generic HTTP webhook notifier with a versioned JSON payload, optional authorization header, timeouts, and mocked tests.
- [ ] **Medium** — Add a notifier registry/router that sends an event only to enabled channels assigned to the originating search.
- [ ] **Hard** — Implement durable delivery claiming, success/failure recording, bounded retry/backoff, and restart-safe duplicate prevention.
- [ ] **Easy** — Add redaction tests proving Telegram, ntfy, and webhook secrets never appear in logs, status data, template context, or exception strings.
- [ ] **Medium** — Switch search orchestration from direct Telegram calls to persisted provider-neutral events and notifier routing.

## Phase 7 — Persistent application and scheduler lifecycle

- [ ] **Medium** — Refactor Flask into an application factory with injected repositories/services and persistent availability independent of first-run configuration.
- [ ] **Easy** — Add `/healthz` liveness and `/readyz` readiness endpoints that do not disclose secrets or depend on unreliable portals.
- [ ] **Medium** — Implement a lightweight in-process scheduler that loads enabled searches, computes UTC next-run times, and reacts to schedule changes without polling aggressively.
- [ ] **Medium** — Persist scheduler activity and next-run state, and recover cleanly after process restarts or a missed interval.
- [ ] **Medium** — Route scheduled and manual triggers through the same non-overlapping search orchestration API.
- [ ] **Hard** — Add scheduler tests with a controllable clock for independent intervals, disabled searches, missed runs, lock contention, failure isolation, and clean stop.
- [ ] **Medium** — Create a new bootstrap entrypoint that runs migrations/import once, starts the persistent web server and scheduler, and no longer waits on `config.json`.
- [ ] **Medium** — Add SIGTERM/SIGINT lifecycle handling that stops new scheduling, terminates child crawls after a grace period, and closes the web/database services cleanly.
- [ ] **Medium** — Run the Flask application under a lightweight production WSGI server suitable for the single-service Compose deployment.
- [ ] **Hard** — Cut over from the legacy infinite loop to the new bootstrap only after migration, orchestration, notification, and lifecycle integration tests pass.
- [ ] **Easy** — Remove retired first-run subprocess, JSON array repair, direct Telegram, and sleep-loop code after the cutover, preserving migration readers until their deprecation window ends.

## Phase 8 — Search and notification web UI

- [ ] **Easy** — Add a simple shared Jinja layout, navigation, flash messages, accessible form styles, and reusable validation-error rendering.
- [ ] **Medium** — Add a dashboard showing application/scheduler health, enabled searches, last and next runs, recent discoveries, and actionable degraded states.
- [ ] **Medium** — Add the search list page with enabled state, transaction type, interval, selected portals, latest status, and safe actions.
- [ ] **Medium** — Add search creation with name, transaction type, interval, and normalized filters, including server-side validation and Post/Redirect/Get.
- [ ] **Medium** — Add search editing with optimistic/concurrent-update protection and preservation of unknown future configuration fields where applicable.
- [ ] **Easy** — Add enable/disable and delete flows with CSRF protection, confirmation, and explicit behavior for retained listing history.
- [ ] **Medium** — Add portal selection/configuration UI driven entirely by registry metadata and capability information.
- [ ] **Medium** — Add raw URL override fields as an advanced legacy-compatible option with adapter-domain validation and clear warnings.
- [ ] **Medium** — Show remote, local, and unsupported filter coverage before a search is saved; never imply unsupported portal filtering.
- [ ] **Medium** — Add a manual-run action with CSRF protection, lock/conflict feedback, immediate run creation, and status-page redirect.
- [ ] **Medium** — Add notification channel list/create/edit/enable/disable/delete pages with provider-specific validation and masked secrets.
- [ ] **Medium** — Add per-search notification assignment and event preference forms.
- [ ] **Medium** — Add safe “send test notification” actions that record delivery outcome and never reveal provider responses containing credentials.
- [ ] **Easy** — Add route/form/template tests for successful CRUD, validation failures, CSRF, missing records, masked secrets, and manual-run conflicts.

## Phase 9 — Listing history and operational status views

- [ ] **Medium** — Add a paginated recent-listings page with search, portal, event type, and active/inactive filters.
- [ ] **Easy** — Add listing rows/cards with normalized price, area, price/m², rooms, status, first/last seen, source portal, and safe external link.
- [ ] **Medium** — Add a listing detail page showing all matching searches and chronological price history without becoming a CRM.
- [ ] **Medium** — Add dedicated views/filters for newly discovered listings, price drops, reappearances, and inactive listings.
- [ ] **Medium** — Add a search-run detail page with per-portal duration, returned/matched/new counts, status category, and redacted error summary.
- [ ] **Medium** — Add portal health summaries based on recent attempts while distinguishing unavailable, blocked, parser failure, and empty success.
- [ ] **Easy** — Add bounded retention/pruning for verbose run diagnostics and delivery attempts while retaining listing and price history.
- [ ] **Medium** — Add repository/query and web tests for pagination, filters, ordering, price history, status summaries, and retention.

## Phase 10 — Docker persistence and self-hosted deployment

- [ ] **Medium** — Update Compose to build/use the current application image with one documented persistent data bind mount or named volume and a configurable host web port.
- [ ] **Easy** — Add restart policy, healthcheck wiring, init/reaping behavior, and sane stop-grace configuration without exposing extra ports.
- [ ] **Medium** — Update the image for the new entrypoint, production WSGI server, healthcheck dependency, and least-privileged runtime user compatible with Chromium and the data mount.
- [ ] **Medium** — Document environment variables, data ownership/permissions, secrets behavior, backup/restore, update, rollback, and migration procedures.
- [ ] **Hard** — Add a deployment smoke test that creates configuration/history, recreates the container, and verifies SQLite data and web readiness survive.
- [ ] **Medium** — Verify graceful shutdown during idle scheduling and an active crawl without orphaning Chromium/Scrapy processes.
- [ ] **Easy** — Update README examples to use the persistent Compose workflow and remove claims/instructions for the retired first-run JSON UI.

## Phase 11 — Deliberate portal expansion

- [ ] **Externally unreliable** — Evaluate a short list of additional Spanish portals with reproducible probes and document normal HTML vs JavaScript-heavy vs Playwright-required vs strongly protected behavior, data quality, terms/robots considerations, and maintenance cost.
- [ ] **Medium** — Select the highest-value maintainable candidate, or explicitly record that none meets the threshold, without making an anti-bot service mandatory.
- [ ] **Hard / Externally unreliable** — Add the selected portal's fixtures, parser, normalized identity, adapter metadata/capabilities, request builder, status classification, and opt-in live test as separate coherent tasks recorded here after selection.
- [ ] **Hard / Externally unreliable** — Evaluate and, only if it passes the same maintenance threshold, integrate a second candidate through separately added tasks.
- [ ] **Externally unreliable** — Reassess Idealista with a bounded live probe; keep it disabled/degraded by default if DataDome remains blocking and do not centralize the architecture around bypass attempts.

## Phase 12 — Conservative cross-site duplicate candidates

- [ ] **Medium** — Add normalized address/location tokens and conservative comparison helpers for price, area, bedrooms, and title similarity with Spanish text fixtures.
- [ ] **Medium** — Add schema/repository support for duplicate-candidate groups, scores, reasons, review state, and non-destructive membership history.
- [ ] **Hard** — Implement candidate generation that narrows by location and structural attributes before scoring, with bounded query cost.
- [ ] **Hard** — Implement a conservative weighted score with precision-first thresholds and tests emphasizing false-positive prevention.
- [ ] **Medium** — Compute duplicate candidates asynchronously after ingestion without merging listing identity or blocking notifications.
- [ ] **Medium** — Add a read-only web view showing candidate groups, evidence, source links, and confidence; do not auto-merge properties.
- [ ] **Medium** — Add accept/reject review actions and use rejected pairs as durable exclusions from future suggestions.

## Phase 13 — Hardening and release readiness

- [ ] **Medium** — Add end-to-end offline tests spanning search creation, scheduled fixture ingestion, listing/history persistence, event generation, delivery routing, and web display.
- [ ] **Medium** — Add failure-injection tests for corrupted legacy files, migration rollback, locked SQLite, killed spider subprocesses, malformed portal output, notifier timeouts, and restart recovery.
- [ ] **Easy** — Review logs and status payloads for actionable context, bounded size, stable error categories, and comprehensive secret redaction.
- [ ] **Medium** — Establish and test database backup/restore and pre-migration backup behavior against representative legacy and current data.
- [ ] **Externally unreliable** — Run and record opt-in live smoke tests for each enabled portal, classifying failures as application, parser, site change, or blocking.
- [ ] **Medium** — Run a container soak test across multiple independently scheduled fixture searches and verify no overlapping runs, database corruption, duplicate notifications, or orphan processes.
- [ ] **Easy** — Reconcile `AGENTS.md`, `TASKS.md`, README, configuration examples, portal status, and commands with the shipped architecture.
- [ ] **Easy** — Publish a migration/release checklist with known breaking changes, limitations, supported upgrade path, and rollback instructions.
