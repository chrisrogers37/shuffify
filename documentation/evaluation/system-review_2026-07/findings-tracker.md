# System Review Findings Tracker — July 2026

**Review date:** 2026-07-07
**Baseline commit:** `f038083` (main)
**Baseline health:** `flake8 shuffify/` — 0 errors. `pytest tests/ -m "not integration"` — **1,894 passed, 2 failed** (see SR-020), 22 network-integration tests deselected.

This tracker triages findings from a full-system review covering the services/executor layer, presentation layer (routes/schemas/templates), Spotify integration, data models, infrastructure, tests, CI/CD, and documentation. Each finding has a stable ID (`SR-NNN`) so it can be referenced from commits, PRs, or promoted into GitHub issues.

**Verification legend:**
- ✅ **Verified** — traced through the code by hand during this review.
- 🔍 **Reported** — identified by systematic review; high confidence but should be confirmed with a failing test before fixing.

**Status values:** `open` | `in-progress` | `fixed` | `wont-fix` | `duplicate`

Companion document: [`enhancement-plan.md`](./enhancement-plan.md) sequences these into actionable workstreams.

---

## Summary

| Severity | Count | IDs |
|----------|-------|-----|
| Critical | 4 | SR-001, SR-002, SR-003, SR-004 |
| High | 12 | SR-005 … SR-016 |
| Medium | 21 | SR-017 … SR-037 |
| Low | 8 | SR-038 … SR-045 |

| Category | Count |
|----------|-------|
| Bug (correctness) | 13 |
| Security | 6 |
| Architecture | 8 |
| Tech debt | 10 |
| Testing / CI | 5 |
| Docs / tooling drift | 3 |

---

## Critical

### SR-001 — Scheduled search-query raids never execute
- **Category:** Bug · **Status:** open · ✅ Verified
- **Files:** `shuffify/services/executors/raid_executor.py` (~48–61), `shuffify/services/raid_sync_service.py` (~450–519)
- `execute_raid()` returns immediately when `schedule.source_playlist_ids` is empty. `RaidSyncService.watch_search_query()` always creates raid schedules with `source_playlist_ids=[]` because search sources live only in `UpstreamSource` rows. The result: any user who watches a search query gets a schedule that silently does nothing on every run. Compounding this, `watch_search_query()` never calls `add_job_for_schedule()`, so even the no-op schedule isn't registered with APScheduler until the next app restart (unlike `watch_playlist()`, which registers immediately).
- **Fix:** Remove/loosen the early return in `execute_raid()` — treat "no playlist IDs but search sources exist for this target" as a valid raid by loading `UpstreamSource` rows for the target before deciding to skip. Register the APScheduler job in `watch_search_query()` (or better, centralize registration — see SR-013). Add an end-to-end test: watch a search query → run schedule → tracks staged.

### SR-002 — `_load_sources()` pulls search sources across all target playlists
- **Category:** Bug · **Status:** open · ✅ Verified
- **Files:** `shuffify/services/executors/raid_executor.py` (~304–320)
- The DB query filters on `user_id` and `source_type == "search_query"` but **not** `target_playlist_id`. A user with search sources configured on playlist A will have those queries raided into playlist B whenever B's raid schedule runs. Wrong tracks land in the wrong playlist.
- **Fix:** Pass `target_playlist_id` into `_load_sources()` and add `UpstreamSource.target_playlist_id == target_id` to the filter for all source types. Add a regression test with two targets and distinct search sources.

### SR-003 — Expired session tokens block client creation instead of refreshing
- **Category:** Bug · **Status:** open · ✅ Verified
- **Files:** `shuffify/spotify/client.py` (~117–137), `shuffify/services/auth_service.py` (~127–145)
- `SpotifyClient._initialize_with_token()` calls `TokenInfo.validate()` (which raises on expiry) *before* constructing `SpotifyAPI` — the component that knows how to auto-refresh. Meanwhile `AuthService.validate_session_token()` only checks token *structure*, not expiry. Net effect: after the ~1 hour access-token lifetime, `is_authenticated()` still returns `True` but every `get_authenticated_client()` call raises, so users are effectively logged out hourly even though a perfectly good `refresh_token` sits in their session.
- **Fix:** Remove the pre-validation (or replace it with refresh-first logic mirroring `SpotifyAPI.__init__`), and let the auth manager refresh. Pair with SR-004 so the refreshed token is persisted.

### SR-004 — Refreshed tokens are never written back to the Flask session
- **Category:** Bug · **Status:** open · ✅ Verified
- **Files:** `shuffify/spotify/api.py` (~104–134), `shuffify/routes/core.py` (line 257 is the *only* `session["spotify_token"] = ...` write in the codebase)
- Even when `SpotifyAPI` refreshes a token internally (401 retry or `_ensure_valid_token`), the new `access_token`/`expires_at` live only in memory for that request. The session keeps stale credentials, so every subsequent request repeats the refresh (extra latency, extra load on Spotify's token endpoint) — or fails outright per SR-003.
- **Fix:** Add an `on_token_refresh` callback to `SpotifyAPI`/`SpotifyAuthManager`, wired from `AuthService.get_authenticated_client()`, that writes the refreshed token back to `session["spotify_token"]` with `session.modified = True`.

---

## High

### SR-005 — Redis Spotify cache is fully built but never wired in
- **Category:** Bug / Performance · **Status:** open · ✅ Verified
- **Files:** `shuffify/__init__.py` (`get_spotify_cache()`, ~59–92), `shuffify/services/auth_service.py` (~142), `shuffify/services/executors/base_executor.py`
- `SpotifyCache` (~570 lines, with per-data-type TTLs and invalidation) and `get_spotify_cache()` exist and are tested, but **no production code path passes `cache=` to `SpotifyClient` or `SpotifyAPI`** — the only reference to `get_spotify_cache()` outside its definition is its own docstring. Every dashboard/workshop request re-fetches playlists from Spotify. Documentation (CLAUDE.md, module docstrings) presents caching as live.
- **Fix:** Inject `get_spotify_cache()` in `AuthService.get_authenticated_client()` and in executor `SpotifyAPI` construction. Verify invalidation-after-write paths behave with the cache actually active.

### SR-006 — `raid_now()` uses the schedule's stale source list, breaking manual raids for search-only setups
- **Category:** Bug · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/raid_sync_service.py` (~291–318)
- `raid_now()` computes fresh source IDs from `UpstreamSourceService.list_sources()`, but when a schedule exists it delegates to `_execute_raid_via_scheduler()`, which uses only `schedule.source_playlist_ids`. For search-only setups (empty schedule list per SR-001), manual "Raid Now" is broken the same way scheduled raids are; for playlist sources, added/removed sources drift from what the schedule executes.
- **Fix:** Resolve sources at execution time from `UpstreamSource` (single source of truth) instead of denormalizing IDs onto the schedule; or keep the schedule's list synced on every source add/remove.

### SR-007 — Shuffle/drip verification ignores track order
- **Category:** Bug · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/executors/base_executor.py` (~137–151), `shuffle_executor.py` (~165–168), `drip_executor.py` (~134–141)
- `verify_playlist_state()` compares URI multisets (`Counter`). A shuffle whose write silently failed to reorder (same tracks, original order) passes verification; a drip that appended tracks to the end instead of prepending them passes too. Given the project's recent investment in strict post-write verification (WOOKLYN incident), this is a real gap in the safety rails.
- **Fix:** Add an order-sensitive comparison mode and use it for shuffle and drip target verification.

### SR-008 — Composite jobs (`RAID_AND_SHUFFLE`, `RAID_AND_DRIP`) are not atomic
- **Category:** Bug · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/executors/base_executor.py` (~698–707)
- Steps run sequentially with no compensation. If the second step fails, the raid's side effects persist (pending tracks staged, raid playlist modified) while rollback only restores snapshots from the failing step.
- **Fix:** Snapshot all playlists a composite job will touch before step 1; on any failure, roll back all touched playlists and revert DB staging.

### SR-009 — Raid rollback never covers the raid playlist itself
- **Category:** Bug · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/executors/raid_executor.py` (~144–219), `base_executor.py` (~206–261)
- `_auto_snapshot_before_raid()` snapshots only the target playlist, but `_add_to_raid_playlist()` writes to the linked raid playlist. If verification fails after that write, rollback restores snapshots created during the job — which never include the raid playlist — leaving it mutated with no restore path.
- **Fix:** Auto-snapshot the raid playlist before writing to it, as the drip executor already does for both playlists.

### SR-010 — Inline raid path bypasses executor safety rails
- **Category:** Architecture / Bug · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/raid_sync_service.py` (~362–447)
- `_execute_raid_inline()` calls private raid-executor helpers directly, skipping `playlist_lock`, `JobExecution` records, auto-snapshots, rollback, and activity logging. It also imports private functions (`_fetch_raid_sources_with_limits`, `_build_track_dicts`, `_add_to_raid_playlist`) from the executor module. The same "raid" operation behaves differently depending on entry point.
- **Fix:** Route all raid execution through `JobExecutorService` (e.g. an `execute_raid_for_user()` public API) so locking, snapshots, and audit behavior are consistent everywhere.

### SR-011 — Scheduler advisory lock fails open (duplicate scheduler risk)
- **Category:** Bug / Ops · **Status:** open · ✅ Verified
- **Files:** `shuffify/scheduler.py` (~123–130)
- `_try_acquire_scheduler_lock()` returns `True` on *any* exception. In production with transient PostgreSQL connectivity issues at boot, multiple Gunicorn workers can each start a `BackgroundScheduler` and execute every job N times. The per-playlist execution lock (PR #383) mitigates concurrent writes but not duplicate raids/notifications. Related hazard: the Dockerfile runs `gunicorn --preload`, so the scheduler thread state crosses `fork()` (SR-025).
- **Fix:** Fail closed in production (skip scheduler init, log at ERROR, expose in `/health`); allow fail-open only in development.

### SR-012 — Preview shuffle drops `locked_positions` (track locks ignored)
- **Category:** Bug · **Status:** open · ✅ Verified
- **Files:** `shuffify/schemas/requests.py` (`parse_shuffle_request`, ~167–209), `shuffify/routes/workshop.py` (~179–198)
- The workshop UI sends `locked_positions` in the preview-shuffle JSON body, and the route passes `shuffle_request.locked_positions` into `ShuffleService.execute()` — but `parse_shuffle_request()` only copies algorithm/int/float/string fields and never forwards `locked_positions`, so it is always `None`. Users who lock tracks and preview a shuffle see their locks ignored.
- **Fix:** Forward `locked_positions` in `parse_shuffle_request()` (validating via the existing `ShuffleRequest` field), and add a route test asserting locked positions survive a preview round-trip.

### SR-013 — APScheduler job registration is scattered across callers
- **Category:** Architecture · **Status:** open · ✅ Verified (via SR-001)
- **Files:** `shuffify/services/scheduler_service.py`, `shuffify/routes/schedules.py`, `shuffify/routes/raid_panel.py` (~743–795), `shuffify/services/raid_sync_service.py`
- `SchedulerService` CRUD does not register/unregister APScheduler jobs — routes and some `RaidSyncService` paths do it, each slightly differently, and `watch_search_query()` forgets entirely (SR-001). `delete_schedule` in routes removes the APScheduler job *before* the DB delete, so a failed delete orphans the schedule row with no running job.
- **Fix:** Hook APScheduler sync into `SchedulerService.create/update/delete/toggle` so it is impossible to create an unregistered schedule. Order DB-delete before job-removal.

### SR-014 — No playlist ownership/editability check before Spotify mutations
- **Category:** Security · **Status:** open · 🔍 Reported
- **Files:** `shuffify/routes/shuffle.py` (~43–111), `workshop.py` (~288–313), `snapshots.py` (~157–161), `playlists.py` (~144–158), `schedules.py` (~215–231)
- Mutating routes accept any `playlist_id` from the URL/body and call write operations without verifying the playlist is in the user's editable set. Spotify rejects most unauthorized writes server-side, but this is missing defense-in-depth: schedules can be created against arbitrary playlist IDs, and follower-visible collaborative playlists behave surprisingly.
- **Fix:** Add a shared `assert_user_can_edit(playlist_id)` guard (service-level, cached) invoked by all mutating routes and schedule creation.

### SR-015 — `GET /logout` is a state-changing endpoint without CSRF protection
- **Category:** Security · **Status:** open · ✅ Verified
- **Files:** `shuffify/routes/core.py` (~340–371), `shuffify/templates/partials/settings_sidebar.html`
- Logout (which also best-effort revokes the Spotify access token) is a plain GET link. Any third-party page can force-logout users via `<img src="https://shuffify.app/logout">`. `CSRFProtect` does not cover GET.
- **Fix:** Convert logout to POST with a CSRF token.

### SR-016 — No CI workflows exist despite docs describing a full pipeline
- **Category:** Testing/CI · **Status:** open · ✅ Verified
- **Files:** `.github/` (contains only `dependabot.yml`), `CLAUDE.md` (CI/CD Pipeline table), `documentation/guides/infrastructure_critiques.md`
- CLAUDE.md documents backend lint, backend tests, frontend lint, frontend E2E, and GitGuardian checks running "automatically on push to main" — but no `.github/workflows/` directory exists. Nothing gates merges; the 2 failing tests on main (SR-020) prove it. `infrastructure_critiques.md` conversely still claims "no test files" exist (stale in the other direction).
- **Fix:** Add a `ci.yml` running `flake8`, `black --check`, and `pytest -m "not integration"` with coverage; fix or annotate the docs to match reality.

---

## Medium

### SR-017 — Token encryption key derived from `SECRET_KEY` with a static salt
- **Category:** Security · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/token_service.py` (~19–63)
- The Fernet key for stored refresh tokens is PBKDF2-derived from Flask's `SECRET_KEY` plus a hard-coded salt. Rotating `SECRET_KEY` (a routine security operation) silently invalidates every stored refresh token — all scheduled jobs stop until users re-login. Session-signing and data-encryption concerns are coupled.
- **Fix:** Introduce a dedicated `TOKEN_ENCRYPTION_KEY` env var with a documented rotation/re-encryption path.

### SR-018 — Alembic `upgrade()` runs at every worker startup and failures are swallowed
- **Category:** Architecture / Ops · **Status:** open · ✅ Verified
- **Files:** `shuffify/__init__.py` (~208–249), `Dockerfile`
- Every Gunicorn worker boot runs `flask_migrate.upgrade()`; on failure the error is logged and the app **continues serving traffic against a possibly stale schema**. Concurrent workers can also race on migration.
- **Fix:** Run migrations as a deploy step (entrypoint script or release phase) before workers start; in production, fail fast if the schema is not at head.

### SR-019 — `_restore_job_snapshots()` selects rollback snapshots by time window, not job linkage
- **Category:** Bug · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/executors/base_executor.py` (~206–261)
- Rollback restores all of the user's snapshots with `created_at >= execution.started_at`, unscoped by job or playlist set. A manual snapshot or concurrent schedule during a long job can be swept into the rollback.
- **Fix:** Tag auto-snapshots with `job_execution_id` (new column) and restore only snapshots from that execution.

### SR-020 — Two tests fail on `main` (wrong patch target in activity route tests)
- **Category:** Testing · **Status:** open · ✅ Verified
- **Files:** `tests/routes/test_activity_routes.py` (~54–123)
- `test_renders_activity_page` and `test_passes_stats_and_activities_to_template` patch `shuffify.routes.core.AuthService`, but `/activity` authenticates via `require_auth()` in `shuffify/routes/__init__.py`, which uses *its own* `AuthService` import. The unmocked service constructs a real `SpotifyClient`, which fails with `ValueError: client_id is required`. The tests fail both in isolation and in the full suite — evidence nothing enforces a green suite (SR-16). This file also redefines `test_user`/`auth_client` fixtures instead of using shared conftest fixtures.
- **Fix:** Patch `shuffify.routes.AuthService` (or use the shared `auth_client` fixture + `mock_spotify_client` pattern from `tests/conftest.py`).

### SR-021 — `RaidSyncService` is a ~740-line orchestration god-class
- **Category:** Tech debt · **Status:** open · ✅ Verified (size)
- **Files:** `shuffify/services/raid_sync_service.py`
- Coordinates upstream sources, schedule CRUD, APScheduler, executors, raid links, drip, plus two inline execution paths — with inconsistent behavior between them (SR-010, SR-006). Also mutates ORM state without committing in `drip_now()` (`link.drip_enabled = True` in memory only) as an implicit parameter-passing trick.
- **Fix:** Split into schedule-orchestration vs. execution facades; make all execution flow through `JobExecutorService`.

### SR-022 — Duplicated public-page scraping implementations
- **Category:** Tech debt · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/playlist_service.py` (~26–151), `shuffify/services/source_resolver/public_scraper_pathway.py` (~730 lines)
- Two independent implementations of `__NEXT_DATA__` parsing, embed URLs, browser-mimicking headers, retry/backoff, and caching. They have already diverged (different backoff constants, different cache behavior).
- **Fix:** Extract a shared scraper module; have `PlaylistService` delegate to the resolver pathway.

### SR-023 — Duplicated shuffle logic between interactive service and executor
- **Category:** Tech debt · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/shuffle_service.py` (~108–148), `shuffify/services/executors/shuffle_executor.py` (~89–148)
- Track normalization, lock splitting, algorithm dispatch, and reassembly are implemented twice. Interactive and scheduled shuffles can drift (e.g. lock semantics).
- **Fix:** Extract a shared `apply_shuffle_to_tracks()` used by both paths.

### SR-024 — Duplicated auto-snapshot boilerplate across all four executors
- **Category:** Tech debt · **Status:** open · 🔍 Reported
- **Files:** `raid_executor.py` (~144–178), `shuffle_executor.py` (~204–241), `rotate_executor.py` (~146–170), `drip_executor.py` (~205–257)
- Four nearly identical try/check/create-snapshot/log blocks differing only in snapshot type and trigger text.
- **Fix:** `PlaylistSnapshotService.auto_snapshot_if_enabled(user_id, playlist_id, uris, snapshot_type, trigger)` helper.

### SR-025 — Scheduler starts under `gunicorn --preload` (fork hazard)
- **Category:** Architecture / Ops · **Status:** open · 🔍 Reported
- **Files:** `shuffify/scheduler.py` (~133–160), `Dockerfile`
- `BackgroundScheduler` threads created in the preloaded master do not survive `fork()` cleanly; behavior depends on timing of `create_app()` vs. worker fork. The advisory lock hides most symptoms but the model is fragile (and fail-open per SR-011).
- **Fix:** Start the scheduler in a Gunicorn `post_fork`/`when_ready` hook in exactly one process, or run it as a dedicated sidecar process/container.

### SR-026 — `workshop.html` is a 5,410-line template with ~4,100 lines of inline JS
- **Category:** Tech debt (frontend) · **Status:** open · ✅ Verified (size)
- **Files:** `shuffify/templates/workshop.html`; `static/js/` holds only 327 lines total
- The flagship feature's raids, rotation, schedules, snapshots, search, track locks, and archive logic live in eight inline `<script>` blocks in one template. It cannot be linted, unit-tested, or reviewed safely; every feature PR touches the same giant file.
- **Fix:** Extract into ES modules under `static/js/workshop/` with a single `window.__WORKSHOP_INIT__ = {{ ... | tojson }}` bootstrap; optionally adopt a minimal build step (esbuild) later.

### SR-027 — Dual auth patterns: decorator for JSON routes, hand-rolled checks for HTML pages
- **Category:** Tech debt · **Status:** open · ✅ Verified
- **Files:** `shuffify/routes/__init__.py` (`require_auth_and_db`), `workshop.py` (~53–165), `schedules.py` (~58–154), `settings.py` (~39–119), `activity.py`
- 70+ JSON endpoints use `@require_auth_and_db` (JSON 401), while HTML pages reimplement 40-line try/except auth blocks — and `/activity` uses the JSON decorator so an unauthenticated browser visit renders raw JSON instead of redirecting.
- **Fix:** Add `@require_auth_page` (redirects for HTML) and refactor the four HTML routes onto it.

### SR-028 — `raid_panel.py` is a 1,302-line route module containing scheduler business logic
- **Category:** Tech debt · **Status:** open · ✅ Verified (size)
- **Files:** `shuffify/routes/raid_panel.py`
- 25 endpoints plus `_toggle_schedule()` calling APScheduler registration directly. Raid, drip, pending-track, schedule, and link CRUD all in one module.
- **Fix:** After SR-013 (centralize scheduler registration), split into `raid_links.py`, `raid_execution.py`, `pending_raids.py` mirroring the service layout.

### SR-029 — `models/db.py` is a 1,248-line god-file (15 models)
- **Category:** Tech debt · **Status:** open · ✅ Verified (size)
- **Files:** `shuffify/models/db.py`
- **Fix:** Split into `models/user.py`, `models/scheduling.py`, `models/raid.py`, `models/snapshots.py`, re-exporting from `models/__init__.py` (no migration needed — table definitions unchanged).

### SR-030 — Incomplete Spotify `/tracks` → `/items` endpoint migration
- **Category:** Tech debt / Risk · **Status:** open · 🔍 Reported
- **Files:** `shuffify/spotify/api.py` (writes use `/playlists/{id}/items`; `playlist_remove_items` still DELETEs `/playlists/{id}/tracks`; TODOs at `api.py` ~284 and `routes/playlist_pairs.py` ~328 about the `"track"` → `"item"` response-key migration)
- A half-migrated client risks silent breakage when Spotify completes its deprecation.
- **Fix:** Align all playlist-item endpoints on `/items`, make response parsing accept both `track` and `item` keys, and add contract tests.

### SR-031 — Legacy `SpotifyClient` facade still owns the entire interactive path
- **Category:** Tech debt · **Status:** open · ✅ Verified
- **Files:** `shuffify/spotify/client.py`, `shuffify/services/auth_service.py`, all routes via `require_auth()`
- Executors use the modern `SpotifyAPI` stack; every interactive request goes through the legacy facade, which is the direct cause of SR-003/SR-004 and hides `skip_cache` options.
- **Fix:** Migrate `AuthService` to `SpotifyAuthManager` + `SpotifyAPI` (+ cache per SR-005); keep `SpotifyClient` as a deprecated thin shim, then remove.

### SR-032 — `SpotifyHTTPClient` sessions are never closed (connection leak)
- **Category:** Bug / Performance · **Status:** open · 🔍 Reported
- **Files:** `shuffify/spotify/http_client.py` (~43–73), `shuffify/spotify/api.py` (~92–97)
- Each request constructs a new `requests.Session`; `close()` exists but has no callers. Under Gunicorn this leaks connection-pool state per request.
- **Fix:** Close via Flask `teardown_appcontext`, or share one HTTP client per worker.

### SR-033 — Naive `DateTime` columns storing UTC-aware Python datetimes
- **Category:** Tech debt / Bug risk · **Status:** open · 🔍 Reported
- **Files:** `shuffify/models/db.py` (all `db.DateTime` columns)
- Models default to `datetime.now(timezone.utc)` but columns lack `timezone=True`, so values are stored naive. Comparisons behave differently across SQLite (dev) and PostgreSQL (prod); `TrackLock.is_expired` already carries defensive naive-handling code.
- **Fix:** Migrate to `DateTime(timezone=True)` and normalize reads; one Alembic migration.

### SR-034 — Missing production Redis degrades silently (fail-fast already tried and reverted)
- **Category:** Ops / Observability · **Status:** open · 🔍 Reported
- **Files:** `config.py` (~129–130), `shuffify/__init__.py` (~131–170)
- `ProdConfig` lets `REDIS_URL` be unset, falling back to filesystem sessions (breaks with >1 replica) and in-memory rate limiting, logged only at WARNING. **Important context:** a strict fail-fast guard was already shipped (#339) and deliberately reverted after it blocked DigitalOcean deploys for 3 days (PRs #376–#381) — do **not** reintroduce a hard requirement. The remaining gap is observability.
- **Fix:** Log at ERROR (not WARNING) and expose session-backend/rate-limit-backend status in an authenticated ops view or structured logs, so operators who *intend* to run Redis notice drift. Once a public REST API with Redis-backed rate limits ships, revisit whether that feature should require Redis.

### SR-035 — Lock timeouts silently drop scheduled runs
- **Category:** Bug / Observability · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/executors/base_executor.py` (~329–339)
- When `playlist_lock` times out, the job logs a warning and returns with no `JobExecution` record and no `schedule.last_status` update. Users see nothing; the run just didn't happen.
- **Fix:** Record a `JobExecution` with a `skipped`/`lock_timeout` status so the UI and history reflect contention.

### SR-036 — Raid staging records wrong provenance
- **Category:** Bug · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/executors/raid_executor.py` (~112–117)
- `PendingRaidService.stage_tracks()` receives `source_name=schedule.target_playlist_name` — the *target*, not the upstream source. The pending-track inbox shows incorrect attribution for every staged track.
- **Fix:** Stage per-source inside the fetch loop, passing the actual source name/ID.

### SR-037 — Linter schism: docs say `ruff`, tooling installs only `flake8`
- **Category:** Tooling · **Status:** open · ✅ Verified
- **Files:** `CLAUDE.md`, `requirements/dev.txt`, `.flake8`
- CLAUDE.md's command reference uses `ruff check shuffify/`, but ruff is not in any requirements file; the enforced tool is flake8. `mypy` and `isort` are installed but referenced nowhere. No `pyproject.toml`; config is scattered across `.flake8`, `pytest.ini`, `requirements/`.
- **Fix:** Pick one linter (ruff subsumes flake8+isort and is faster), update docs and CI, consolidate config in `pyproject.toml`.

---

## Low

### SR-038 — `raid_source_count_update` doesn't validate `source_id` belongs to URL's `playlist_id`
- **Category:** Bug (minor) · **Status:** open · 🔍 Reported
- **Files:** `shuffify/routes/raid_panel.py` (~267–291). Ownership (user) is checked; target-playlist consistency is not.

### SR-039 — `validate_json()` reports only the first Pydantic error
- **Category:** UX / Tech debt · **Status:** open · ✅ Verified
- **Files:** `shuffify/routes/__init__.py` (~127–134). Multi-field forms surface one error at a time. Also duplicate JSON error helpers exist (`json_error` vs `error_handlers.json_error_response`).

### SR-040 — CSRF and 404 error handlers always return JSON / default pages for browser requests
- **Category:** UX · **Status:** open · 🔍 Reported
- **Files:** `shuffify/error_handlers.py` (~331–361); `templates/errors/` has only `500.html`. Add `404.html` and content-negotiate CSRF failures.

### SR-041 — `upstream_sources.py` routes appear dead (superseded by raid panel)
- **Category:** Dead code · **Status:** open · 🔍 Reported
- **Files:** `shuffify/routes/upstream_sources.py` (143 lines, no frontend references). Remove or document as external API; it also uses hand-rolled validation instead of the raid schemas.

### SR-042 — CDN dependencies (Tailwind, SortableJS) without SRI or vendoring
- **Category:** Security (supply chain) · **Status:** open · 🔍 Reported
- **Files:** `shuffify/templates/base.html`, `workshop.html`. Pin + SRI, or vendor under `static/`. Tailwind CDN in production is also officially discouraged (runtime JIT cost).

### SR-043 — Missing indexes: `schedules.is_enabled`, `login_history.logged_in_at`
- **Category:** Performance · **Status:** open · 🔍 Reported
- **Files:** `shuffify/models/db.py` (~417, ~547–583). Add in next migration touching these tables.

### SR-044 — Rotate executor: deterministic swap-in vs random swap-out
- **Category:** Bug (minor) · **Status:** open · 🔍 Reported
- **Files:** `shuffify/services/executors/rotate_executor.py` (~459–460). `swap_in_uris = archive_uris[:n]` means deep-archive tracks may never rotate in. Randomize or document FIFO as intentional.

### SR-045 — Documentation drift (counts, removed features, stale claims)
- **Category:** Docs · **Status:** open · ✅ Verified (spot checks)
- CLAUDE.md/README claim 1,714 tests (actual: ~1,918 collected, 1,894 passing non-integration) and 8 algorithms including `TempoGradientShuffle` (removed; registry has 7). `documentation/evaluation/` docs cite v2.4.x-era stats. `infrastructure_critiques.md` claims no tests/CI exist. `run.py` defaults `APP_CONFIG` to development while `create_app()` defaults to production.
- **Fix:** One docs-sync pass; add the test count to CI output instead of hardcoding it in docs.

---

## Explicitly out of scope / already tracked elsewhere

Previous internal evaluations (see `documentation/evaluation/03_extensibility_evaluation.md`, `04_future_features_readiness.md`, `05_brainstorm_enhancements.md`) already track: notification channels, public versioned REST API, plugin architecture, multi-service (Apple Music) abstraction, live preview polish, and PWA. Those remain valid product-direction items and are sequenced in the enhancement plan rather than duplicated as findings here.

## What is in good shape (for balance)

- **Security fundamentals:** CSRF via Flask-WTF, per-request CSP nonces, OAuth state validated with `hmac.compare_digest`, session regeneration on login, Fernet-encrypted refresh tokens, non-root container, consistent `|tojson` (no `|safe`) in templates.
- **Test discipline:** ~1,900 tests, strong service and scraper coverage, clean shared fixtures, network tests properly marked `integration`.
- **Verification/rollback design:** post-write verification, auto-snapshots, `failed_rolled_back` status — unusually mature; the findings above are gaps in an otherwise good system, not an absent one.
- **Code hygiene:** flake8 clean, only 3 TODO comments in the entire app codebase, layered architecture is respected in the large majority of modules.
