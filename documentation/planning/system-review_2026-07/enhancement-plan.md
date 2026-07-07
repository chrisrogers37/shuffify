# Enhancement Plan — July 2026

**Based on:** [`findings-tracker.md`](./findings-tracker.md) (SR-001 … SR-045), the existing product-direction docs (`03_extensibility_evaluation.md`, `04_future_features_readiness.md`, `05_brainstorm_enhancements.md`), and the CHANGELOG's recent reliability/security themes.

## Where the project is

Shuffify has completed its feature build-out era (workshop, scheduling, raiding, snapshots, persistence, v3.0 navigation) and is in an **operational-maturity phase**: recent work is dominated by production reliability (WOOKLYN investigation, post-write verification, per-playlist locks) and security hardening (CSRF/CSP, container hardening, XSS fixes). The review confirms this is the right instinct — the highest-value work right now is **making what exists correct, observable, and enforceable**, then paying down the debt that makes changes risky, and only then resuming feature expansion (notifications, public API).

The plan below is sequenced by dependency and risk, not calendar time. Each workstream is independently shippable; within a workstream, items are ordered.

---

## Workstream 1 — Correctness: fix the broken user-facing paths

*Goal: no feature a user can reach silently does nothing or does the wrong thing.*

1. **Fix the search-query raid pipeline end-to-end** (SR-001, SR-002, SR-006)
   - Make `execute_raid()` load sources from `UpstreamSource` scoped to the schedule's target instead of bailing on an empty `source_playlist_ids`.
   - Filter `_load_sources()` by `target_playlist_id`.
   - Make execution-time source resolution the single source of truth (stop denormalizing IDs onto schedules, or keep them synced).
   - Add end-to-end tests: watch search query → schedule fires → tracks staged with correct provenance (also fixes SR-036).
2. **Fix session token refresh** (SR-003, SR-004)
   - Stop pre-validating expiry in `SpotifyClient._initialize_with_token()`; let the auth manager refresh.
   - Add an `on_token_refresh` callback that persists refreshed tokens to `session["spotify_token"]`.
   - This eliminates the hourly forced re-login — likely the single most user-visible fix in this plan.
3. **Fix preview-shuffle track locks** (SR-012) — forward `locked_positions` through `parse_shuffle_request()`; one-line fix plus a regression test.
4. **Order-aware post-write verification** (SR-007) — strict list comparison for shuffle and drip; completes the WOOKLYN-era verification work.
5. **Repair the 2 failing tests on main** (SR-020) and consolidate their fixtures onto conftest.

**Exit criteria:** full suite green; new regression tests for each fixed bug; search-source raids demonstrably staging tracks.

## Workstream 2 — Enforcement: CI so regressions can't land

*Goal: the documented quality bar is machine-enforced. Do this immediately after (or in parallel with) Workstream 1 so the fixes stay fixed.*

1. **Add `.github/workflows/ci.yml`** (SR-016): `flake8` + `black --check` + `pytest -m "not integration"` on PRs and pushes to main; Python 3.12; pip cache.
2. **Coverage gate** — wire the already-installed `pytest-cov` with a modest `--cov-fail-under` (start at current baseline, ratchet up).
3. **Nightly integration job** for the 22 live-scraper tests (`-m integration`, non-blocking).
4. **Resolve the linter schism** (SR-037): adopt ruff (subsumes flake8/isort), consolidate config into `pyproject.toml`, update CLAUDE.md's command reference.
5. **Docs sync pass** (SR-045): correct test counts, algorithm list, stale infrastructure claims; make CLAUDE.md's CI table describe the real pipeline.

**Exit criteria:** a PR with a failing test or lint error cannot merge; docs match reality.

## Workstream 3 — Reliability & operations hardening

*Goal: scheduled jobs are safe under concurrency, failures are visible, deploys are boring.*

1. **Scheduler lifecycle** (SR-011, SR-025): fail-closed advisory lock in production; start the scheduler from a single post-fork process (or dedicated container) instead of under `--preload`.
2. **Migrations as a deploy step** (SR-018): move `flask db upgrade` to the container entrypoint before Gunicorn starts; fail fast on error in production.
3. **Centralize APScheduler registration in `SchedulerService`** (SR-013) — makes unregistered schedules structurally impossible; fixes the delete-ordering bug.
4. **Unify raid execution through `JobExecutorService`** (SR-010, SR-021): retire `_execute_raid_inline()`; every raid gets locking, snapshots, execution records, and activity logs regardless of entry point.
5. **Complete the rollback story** (SR-008, SR-009, SR-019): snapshot every playlist a job will touch (incl. raid playlist and composite-job steps); tag auto-snapshots with `job_execution_id` and restore only those.
6. **Observability gaps** (SR-035, SR-034): record `lock_timeout`-skipped runs as `JobExecution` rows; surface missing Redis loudly in production (a hard requirement was already tried in #339 and reverted after blocking deploys — see SR-034); report scheduler-lock state and schema version in an authenticated ops view or structured logs.

**Exit criteria:** any job failure or skip is visible in execution history; a failed composite job leaves all playlists restored; deploys run migrations exactly once.

## Workstream 4 — Performance & the Spotify integration cleanup

*Goal: finish the half-done client migration and turn on the performance work that's already written.*

1. **Wire the Redis Spotify cache** (SR-005): inject `get_spotify_cache()` in `AuthService.get_authenticated_client()` and executor API construction. This is prebuilt, tested capacity currently doing nothing — likely the cheapest large latency win available.
2. **Retire the legacy `SpotifyClient` facade** (SR-031): migrate `AuthService`/routes to `SpotifyAuthManager` + `SpotifyAPI`; keeps one code path for refresh, caching, and error handling. (Workstream 1's token fix can be done inside the facade first; this item removes the facade.)
3. **HTTP session lifecycle** (SR-032): close/reuse `requests.Session` per worker or via `teardown_appcontext`.
4. **Finish the `/tracks` → `/items` migration** (SR-030) with contract tests accepting both response keys.
5. **Data-layer tidy-up in one migration** (SR-033, SR-043): timezone-aware columns + missing indexes.

**Exit criteria:** one Spotify client stack; dashboard/workshop loads hit cache; no deprecation-exposed endpoints.

## Workstream 5 — Security hardening (defense in depth)

*Ordered by exposure; all are moderate-effort, low-risk changes.*

1. **POST logout with CSRF** (SR-015).
2. **Playlist editability guard** before all mutating operations and schedule creation (SR-014).
3. **Dedicated `TOKEN_ENCRYPTION_KEY`** with a documented rotation path (SR-017) — do before the next `SECRET_KEY` rotation, which currently would strand all scheduled jobs.
4. **Vendored/SRI-pinned frontend assets** (SR-042); replace the Tailwind CDN with a build-time CSS step (pairs with Workstream 6).
5. Small items: scope `raid_source_count_update` to its URL playlist (SR-038); content-negotiated CSRF/404 error pages (SR-040).

## Workstream 6 — Frontend & presentation-layer debt

*Goal: make the workshop maintainable before building more features on top of it.*

1. **Extract workshop JS** (SR-026): move the ~4,100 inline lines into ES modules under `static/js/workshop/` (state, raid panel, snapshots, search, locks), bootstrapped by one `window.__WORKSHOP_INIT__` payload. Do this incrementally, one script block at a time, behind the existing CSP nonce policy.
2. **Unify HTML-page auth** with a `@require_auth_page` decorator (SR-027); fixes `/activity` returning JSON to browsers.
3. **Split `raid_panel.py`** into feature modules after scheduler-registration centralization lands (SR-028).
4. **Split `models/db.py`** into per-domain modules (SR-029) — mechanical, no migration.
5. **Consolidate response helpers and full-error validation** (SR-039); **remove dead `upstream_sources.py` routes** (SR-041).
6. Extract shared scraper + shuffle helpers (SR-022, SR-023) and the auto-snapshot helper (SR-024).

**Exit criteria:** no template over ~1,500 lines; workshop JS unit-testable; one auth pattern per response type.

## Workstream 7 — Product enhancements (resume feature work)

*Unblocked once Workstreams 1–3 land; drawn from the existing roadmap docs, re-prioritized by readiness.*

1. **Notification system** (highest-leverage next feature): scheduled jobs already produce `JobExecution` outcomes and Workstream 3 makes skips/failures visible — a notifier registry (email/Telegram/webhook) following the `ShuffleRegistry` pattern is the natural next step, and was scored the biggest gap in `04_future_features_readiness.md`.
2. **Live shuffle preview polish**: algorithms are already side-effect-free and preview endpoints exist; the remaining work is UX (drag-to-adjust before commit) — much easier after the workshop JS extraction.
3. **Public versioned REST API** (`/api/v1/`) with API-key auth: routes are already JSON-first; needs auth scheme, rate limits (Redis-backed, requires SR-034), and OpenAPI docs.
4. **Quick wins from the brainstorm backlog**: multi-playlist shuffle, per-user algorithm presets (UserSettings already exists), duplicate-track finder (dedupe logic already in raid pipeline).
5. **Longer horizon** (unchanged from prior evaluations): plugin architecture, multi-service abstraction, PWA.

---

## Suggested execution order (dependency graph)

```
WS1 (correctness fixes) ──► WS2 (CI lock-in) ──► everything else
                                   │
        WS3 (reliability) ◄────────┤──► WS5 (security) — parallel-safe
                │                  │
        WS4 (spotify/perf)         └──► WS6 (frontend debt)
                │                              │
                └──────────► WS7 (features) ◄──┘
```

- **WS1 + WS2 first** — small diffs, highest user impact, and they make all later work safer.
- **WS3 and WS5 can proceed in parallel** — they touch different layers.
- **WS4 item 1 (cache wiring) can be pulled forward** any time after WS2; it is low-risk and high-payoff.
- **WS7 should not start before WS3** — notifications depend on trustworthy job outcomes, and a public API should not be built on the legacy client facade.

## Tracking

All findings are filed as GitHub issues (2026-07-07) — see the mapping table at the top of [`findings-tracker.md`](./findings-tracker.md):

- **P0/P1 individual issues:** [#439](https://github.com/chrisrogers37/shuffify/issues/439)–[#454](https://github.com/chrisrogers37/shuffify/issues/454) (SR-001 … SR-016, with SR-020 folded into #454)
- **P2 clusters:** [#455](https://github.com/chrisrogers37/shuffify/issues/455) reliability/ops, [#456](https://github.com/chrisrogers37/shuffify/issues/456) Spotify/data layer, [#457](https://github.com/chrisrogers37/shuffify/issues/457) backend structure, [#458](https://github.com/chrisrogers37/shuffify/issues/458) presentation/tooling
- **P3 cluster:** [#459](https://github.com/chrisrogers37/shuffify/issues/459) low-severity findings
- **P4 cluster:** [#460](https://github.com/chrisrogers37/shuffify/issues/460) nice-to-have product roadmap (Workstream 7)

Conventions:
- Reference the SR-ID and issue number in fix commits/PRs; update the `Status` field in the tracker as items land.
- Check off cluster-issue checklist items as their sub-findings are fixed; close the cluster when all are done.
- Add a CHANGELOG entry per fix, per existing project convention.
