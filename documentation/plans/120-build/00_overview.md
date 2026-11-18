---
title: "[plan] Workspaces and bring-your-own Spotify app — build plan (#120)"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, auth, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

The build plan for #120, part of epic #119. The approved design is
[`../120-workspaces-byo-spotify-app.md`](../120-workspaces-byo-spotify-app.md).
Read it first: it holds the what and the why, and the ratified decisions
(amended 2026-11-10). This plan covers how to build it, in ten phases:
- a user-facing rename (#122) that ships on main on its own;
- the move to raccoon.fm, also on main, as soon as the owner has the domain
  (phase 01b);
- one groundwork phase on main with no visible change, shipped as two PRs
  (02a the credential seam, 02b the refresh-token move) with a check of real
  Spotify's PKCE behaviour between them;
- six phases on an integration branch that build the new model and sign-in,
  run against real Spotify and Google on staging from phase 06;
- the cutover, one sitting: it wipes production, merges that branch, and the
  owner deletes their old Spotify app and sets up from scratch through the
  in-product guide, timed.

The epic (#119) has four projects: #122 the name, #120 this, #121 the MCP
server and #123 the public launch. The last phase is the cutover, not the
public launch.

## Evidence

Verified against main at b65d2d9 (2026-11-10).

- **Spotify clients are built in two places,** and both read the site-wide
  app from config:
  - web requests: `AuthService._auth_manager()` and `get_authenticated_api`
    (`shuffify/services/auth_service.py:66-82, 184-227`), which falls back
    to the environment at L76-77;
  - the worker: `JobExecutorService._get_spotify_api(user)`
    (`shuffify/services/executors/base_executor.py:71-136`, credentials at
    L95-96).
- **The scheduler never builds a client.** `admit_scheduled`
  (`shuffify/services/scheduled_admission.py:141-220`) only queues a
  `schedule_run`, and the worker runs it through the second path.
- **Failure detail is lost.**
  - `SpotifyAuthManager.refresh_token` (`shuffify/spotify/auth.py:302-312`)
    and `exchange_code` (L247-249) collapse every OAuth error into
    `SpotifyTokenError` and drop the `error` code.
  - The 401 retry in `shuffify/spotify/http_client.py:322-337` swallows the
    refresh failure and raises `SpotifyTokenExpiredError`.
  - `SpotifyForbiddenError` (L410) covers both playlist access and app-level
    rejection.
  - The worker wraps a failed refresh in `JobExecutionError`
    (`base_executor.py:135-136`) and the web path in `AuthenticationError`
    (`auth_service.py:222-224`), so neither carries the typed cause to the
    code that would act on it.
  - The callback already special-cases Spotify's "not registered" text
    (`shuffify/routes/core.py:220`).
- **The exception hierarchy** (`shuffify/spotify/exceptions.py`):
  `SpotifyAuthError` → `SpotifyTokenError` → `SpotifyTokenExpiredError`, and
  `SpotifyAPIError` → `SpotifyForbiddenError`. `SpotifyTokenExpiredError` is
  matched by `handle_spotify_token_expired` (`shuffify/error_handlers.py:329,
  621`) and re-raised untouched in `http_client.py:442`.
- **Typed failures don't survive propagation.** Services catch
  `SpotifyForbiddenError` or `SpotifyError` before any Flask handler:
  `playlist_service.py:235, 303`, `workshop_commit_service.py:115, 140, 169,
  251, 392`, `workshop_finalize_service.py:374`,
  `routine_activation_service.py:51, 154, 351, 382, 482, 557` and
  `playlist_setup_service.py:990, 1085, 1138, 1185`. A 403 raised at
  `http_client.py:410` also never passes through a refresh. A handler can't be
  the detector of a broken app.
- **The transient family.** `SpotifyReadTransientError` is recognised by class
  in the 401 retry's re-raise (`http_client.py:332`, deadline, rate-limit and
  read-transient only; any other refresh failure becomes "Token expired or
  invalid", answered as a 401 "Session expired") and in the worker's
  `read_retry_delay` (`shuffify/services/operations/control.py:6-28`), which
  defers a run for those classes found along the cause chain
  (`worker.py:204`).
- **Token endpoint calls use Basic authorization with the site's secret**
  (`shuffify/spotify/auth.py:236, 297, 382`). The revoke call at L382 is
  unsupported by Spotify and silently ignored.
- **Nothing records that an app is broken.** A scheduled run that hits an
  auth error ends `failed`. The schedule stays enabled and fires again at
  its next slot (`shuffify/services/operations/commands/routines.py:359-415,
  505-508`).
- **Two copies of the refresh token drift.** The worker rotates the stored
  token under the `credential:{user_id}` lock (`shuffify/worker.py:94-95`,
  `base_executor.py:112-125`). The web request refreshes from the session's
  own copy and writes only the session (`auth_service.py:230-255`).
  - #143 (7b56eaa) keeps that session copy encrypted
    (`auth_service.py:32-58`, `routes/__init__.py:46-56`,
    `tests/routes/test_session_refresh_token.py`).
- **The credential lock can't protect a refresh token.**
  - It is a session advisory lock on the pooled engine
    (`shuffify/services/operations/repository.py:509-543`), and production's
    api and worker both connect through Neon's pooled host, although the
    guides describe the direct one (checked read-only 2026-11-10). Advisory
    session locks aren't reliable through a transaction-mode pooler.
  - The worker holds it for a whole run, up to 240s (`worker.py:33, 94-100`),
    while a web wait times out after 2s.
  - A non-deadline error in `get_authenticated_api` becomes
    `AuthenticationError` (`auth_service.py:225-227`), which `require_auth`
    reads as signed out.
  - A transaction-scoped row lock on the connection row, held only around
    read, refresh and store, is correct through any pooler. The repo already
    has both parts: `_lock_user` locks a `users` row `FOR NO KEY UPDATE`
    (`.with_for_update(key_share=True)`,
    `shuffify/services/upstream_source_service.py:42-50`), which doesn't block
    inserts that reference the row, and a lock timeout is set inside the
    transaction (`set_config('lock_timeout', ..., true)` at
    `shuffify/services/operations/repository.py:540`, `SET LOCAL` at
    `shuffify/services/playlist_lock.py:190-193`), because a session-level
    setting leaks across the pooled host.
- **Identity today.**
  - `users` (`shuffify/models/db.py:72-174`) is the Spotify account, keyed
    by `spotify_id`.
  - Thirteen tables have an FK to `users.id`. `job_executions.user_id` has
    no FK (`db.py:595`). No FK sets `ondelete`.
  - `user_id` appears in 66 files under `shuffify/`. In `shuffify/spotify/
    cache.py` and `api.py` it means the Spotify account id, not a database
    row.
  - The session identifies the user by the Spotify profile: `get_db_user`
    (`shuffify/routes/__init__.py:231-260`), the rate-limit key
    (`shuffify/__init__.py:141-148`) and `/api/session`
    (`shuffify/routes/contexts.py:61-76`).
  - Several services scope by `spotify_id` (`UpstreamSourceService`,
    `WorkshopSessionService`, `RaidSyncService`,
    `services/base.py:get_user_or_raise`).
- **Encryption.** `TokenService` (`shuffify/services/token_service.py`) is
  one Fernet under `TOKEN_ENCRYPTION_KEY`, with no rotation support. PR #44
  (d30081a) deliberately removed the keyring and rotate command it once had.
- **Schedules.** Turning a schedule back on moves `definition_changed_at`
  (`shuffify/services/schedule_notifications.py:57-100`), so runs missed
  while it was off are not made up (`scheduled_admission.py:95-118`).
- **Hosting is one origin.**
  - Vercel forwards `/api`, `/login`, `/callback`, `/logout` and `/health`
    to Railway (`web/scripts/build-vercel-output.mjs:56-57`); the harness
    preview proxy has the same list (`tests/frontend_preview.py:13`).
  - The domain lives only in environment settings: `PUBLIC_ORIGIN`,
    `SPOTIFY_REDIRECT_URI` and `RAILWAY_API_URL`. The API accepts unsafe
    requests only from `PUBLIC_ORIGIN`.
  - CSP `form-action` allows only `accounts.spotify.com`
    (`web/security-headers.ts:13`).
  - Preview deployments forward to the production API today
    (`documentation/guides/production.md`), and `.railway/README.md:9` says
    a nonproduction environment needs its own Neon branch and never the
    production URL.
- **Every merge to main deploys production:** the Railway api and worker,
  and Vercel. Both Railway services build from `main`
  (`.railway/railway.ts:38`), so a merge redeploys a paused worker too.
- **Staging has existed before.** A Railway and Neon staging copy supported
  the 2026-10-17 cutover and was removed on 2026-10-18.
  `documentation/guides/production.md` and `.railway/README.md:9` therefore
  say there is no staging environment. This plan stands one up for the
  duration of the work and tears it down in phase 09; those two sentences are
  correct again afterwards, so they are not edited.
- **Schema.**
  - `flask create-schema` (`shuffify/cli.py:15-53`) builds a fresh
    database and stamps head. It leaves a database that already has a
    revision alone, and refuses one that has the tables but no revision.
  - `migrations/env.py:54-66` refuses `upgrade` on a database with no
    revision. Today's pre-deploy therefore refuses an empty database, so the
    script gains a `create-schema` step in phase 06 (not at cutover), and
    staging's first deploy rehearses it.
  - `scripts/pre-deploy.sh` runs `flask db upgrade` with
    `SHUFFIFY_MIGRATION_STEP=1`; the app's startup schema check
    (`shuffify/__init__.py:214, 284`) otherwise refuses a database not at
    head. Giving pre-deploy a `create-schema` step reverses a rule pinned since
    the split runtime (05d4262), with two tests and two docs that state it;
    phase 06 step 7 records the decision and names them.
- **The harness signs in through a fake Spotify** (`harness/fake_spotify/app.py`).
  - It has one hard-coded client, no PKCE and no Google.
  - Sign-in is the real OAuth round trip (`scripts/harness.sh:501-523`,
    `tests/e2e/conftest.py:150-168`).
  - A redirect URL mismatch stops on the fake's own error page
    (`app.py:261-262`), as it does on Spotify's.
- **Secret scrubbing** keys on two lists in `shuffify/__init__.py`:
  `_SENTRY_PII_DENYLIST` (keys, L440-453) and `_SENTRY_SECRET_CONFIG_ATTRS`
  (config values registered for exact free-text redaction, L485-490).
  `tests/test_sentry_init.py:547-573` fails when a secret-shaped config
  attribute is in neither the registered lists nor an acknowledged
  non-secret set.
- **A reference implementation exists in the owner's storydump repo:**
  `src/services/target/google_oidc.py`, hand-rolled Google OIDC verifying
  claims and a nonce derived from the state.

## Implementation Plan

### Dependencies

- The approved design (PR #151).
- #122 decided: Raccoon, at raccoon.fm.
- The owner buys raccoon.fm (the trigger for phase 01b).
- Security fixes #126 and #128 to #131 (draft PRs #144 and #145) are **not a
  gate**. `feat/workspaces` is cut when phase 02 lands, and main is merged
  into it as each fix lands, with `python scripts/check-migration-chain.py`
  after each merge. They touch code that phases 04 and 05 rewrite, so the
  later they land, the more conflicts; phase 09's final merge of main and
  full CI run close the gap.

### Blocks

- #121 (MCP server) and #123 (public launch).

### Steps

| # | Phase | Lands on | Size |
|---|---|---|---|
| 01 | [Rename what users see to Raccoon](01_rename-raccoon.md) | main | S |
| 01b | [Move the domain to raccoon.fm](01b_move-domain.md) | main | S |
| 02 | [One credential seam, failures that keep their cause, one refresh token](02_credential-seam-and-failures.md) (two PRs: 02a seam and failures, L; 02b refresh-token move, L) | main | XL |
| 03 | [Person, workspace and Spotify connection tables](03_identity-schema.md) (additive only) | integration | S |
| 04 | [Move product data and the Spotify account from `users` to the workspace](04_rekey-to-workspace.md) | integration | XL |
| 05 | [Google sign-in and the workspace in the session](05_google-sign-in.md) | integration | L |
| 06 | [Attach a Spotify app; connect Spotify through it with PKCE](06_byo-app-and-connect.md) | integration | XL |
| 07 | [Guided setup in the web app](07_setup-wizard.md) | integration | M |
| 08 | [Broken app: detect, pause, guide, resume](08_broken-app.md) | integration | M |
| 09 | [Cutover: wipe, merge, owner sets up from scratch, staging teardown](09_cutover.md) | main | S |

Phase 01b is a second main-branch phase rather than a second PR of 01. The
rename has no external dependency and should merge as soon as it's ready;
the domain move waits for the owner to buy raccoon.fm, touches DNS, Vercel,
Railway and Spotify rather than application code, and needs its own rollback.
Keeping them apart stops one holding up the other. The numbering of the
other phases doesn't change.

Phase 02 is one phase and two PRs, sized L and L, so XL as a phase. 02a (the
credential seam and typed failures) changes nothing users see. 02b (the
refresh-token move) changes how the live site's web requests stay signed in, so
it lands separately and can be reverted without touching 02a. Between them the
owner runs a throwaway script against the existing Spotify app to see how real
Spotify handles a client-ID-only exchange and refresh, so F8 and F14 aren't
first tested on staging in phase 06.

**Phase 03 is purely additive and phase 04 owns the move.** 03 adds the new
tables and two person columns, and `users` keeps its Spotify columns. 04's
migration drops them in the same PR that re-points every reader (about 50
reads in 19 files) and phase 02's refresher to the connection row, so no
phase leaves a reader pointing at a column that's gone. 03 is sized S, and 04
stays XL.

**Two routed stopgaps** keep the harness e2e suite green while sign-in
changes, and phase 06 deletes both. 04's: once `users` has no Spotify columns,
the Spotify `/callback` creates the person, workspace and connection. 05's:
once sign-in is Google, `/spotify/connect` and `/spotify/callback` attach the
site app's connection. Phase 03 needs none, because it moves nothing. Seeding
connections in the harness instead was considered and not chosen: the
stopgaps keep the e2e tests on the real OAuth round trip, and 05's is the
skeleton of 06's final routes.

The integration branch is `feat/workspaces`, cut from main when phase 02
(both PRs) merges. Phase PRs target it with full CI. Main is merged into it at
the start of every phase, and again as each security fix (#126, #128 to #131)
lands.

## Architecture

```
users (person, Google login)
  └─1:N─ workspaces ──────────────── source of truth for product data
           ├─1:0..1─ spotify_connections   (one per workspace; other services later)
           │           client_id            unique site-wide (constraint added in 06)
           │           spotify_id           unique site-wide, set on first connect, then fixed
           │           refresh_token        encrypted (TOKEN_ENCRYPTION_KEY); the only copy
           │           status               unchecked | ok | broken (+ reason)
           │           credential_generation bumped on every connect and replacement
           └─1:N─ schedules, workshop_sessions, upstream_sources, playlist_pairs,
                  raid_playlist_links, playlist_preferences, track_locks,
                  pending_raid_tracks, playlist_snapshots, activity_log,
                  routine_activation_receipts, user_settings → workspace_settings,
                  job_executions
  └─1:N─ login_history (stays on the person)
```

There is no client secret column and no secret key.

- **`users` becomes the person.** It's keyed by Google's stable `sub`, with
  email (trusted only when verified) and display name.
- **`workspaces.name`** defaults to "My workspace". There's no endpoint to
  change it; naming returns with workspace switching.
- **Today's Spotify-account columns move to `spotify_connections`:**
  `spotify_id`, profile fields and the refresh token. Phase 03 only adds the
  table and leaves `users` as it is; phase 04 drops the columns from `users`
  in the same PR that re-points every reader and the refresher. The
  connection holds the app's client ID too, because a workspace has one
  Spotify app and the account connected through it. The Spotify account's id keeps the name
  `user_id` in `shuffify/spotify/cache.py` and `api.py`; those are never
  renamed.
- **Every per-user table is re-keyed to `workspace_id`** (phase 04).
  `login_history` stays on the person.
- **`credentials_for(connection)`** is the only way any code gets Spotify
  app credentials: the connection's client ID and the site's redirect URL.
  It raises `SpotifyAppUnavailable` when there's no connection; it never
  falls back to a site app. It does not check status, because Connect is the
  fix for a broken app and must work while one is broken. Each client-building
  path instead refuses a connection that isn't `ok` before calling Spotify.
- **PKCE.** Connect generates a code verifier and a state, keeps both in the
  session under `spotify_connect`, and authorizes with
  `code_challenge_method=S256` and `show_dialog=true`. The exchange and every
  refresh send `client_id` in the body with no Basic authorization.
- **The refresh token has one home: the database.** Both the web and the
  worker refresh in a short transaction that locks the row holding the token
  with `SELECT ... FOR NO KEY UPDATE` (`with_for_update(key_share=True)`, as
  `_lock_user` does, so inserts into tables that reference the row aren't
  blocked) and sets a transaction-local `lock_timeout` (`set_config(..., true)`,
  as `repository.py:540` does; never a session-level `SET`, which would leak
  across the pooled host), around read, refresh and store, and never around a
  whole run. The row is the `users` row on main and the connection row from
  phase 04. That is correct through Neon's pooled host. The worker's
  credential advisory lock may stay for run serialisation but isn't what
  protects the token. A rotated token whose save failed is kept in memory in
  that process until a store succeeds; the other process doesn't see it, so in
  that rare case the next refresh there gets `invalid_grant` and surfaces as
  `access_revoked`, fixed by one reconnect. An access token is minted from the
  stored refresh token on demand, so a fresh Google sign-in with a healthy
  connection works. A lock or database timeout is `SpotifyRefreshUnavailable`,
  a `SpotifyReadTransientError`: a 503 on the web (inside the 401 retry too), a
  deferral in the worker through `read_retry_delay`, never "signed out". The
  session holds the access token and its expiry only.
- **A credential generation guards the status.** Each connection has a
  `credential_generation`, bumped by every connect and every replacement. A
  client captures it when it is built, and a failure only marks the connection
  broken if that generation is still the current one, so a late failure from a
  client built before a reconnect can't undo it.
- **The session holds `user_id` and the current `workspace_id`.** The
  Spotify access token is present only once Spotify is connected. Google
  sign-in and Spotify connect use separate session keys (`google_state`,
  `spotify_connect`). `/api/session` reports the person, the workspace and
  its setup state, so the web app knows whether to show the setup steps,
  the "uh oh" page or the app.
- **Broken-app detection is at the Spotify client boundary.** A client is
  built from a connection (`credentials_for(connection)` on the web,
  `_get_spotify_api` in the worker), and the builder captures the connection's
  `credential_generation` and gives the client a failure hook. The hook is
  called where a typed failure is raised: by the HTTP client for a 403
  `SpotifyUserNotAllowed` (`http_client.py:410`), and by the refresher for
  `SpotifyAppRejected` and `SpotifyGrantRevoked`, after it has released its
  row lock. It calls `mark_broken(connection, reason, generation)`, which does
  nothing if the connection has been reconnected since. Detection doesn't
  depend on the failure surviving the call stack, which services' broad
  `SpotifyForbiddenError` and `SpotifyError` catches would otherwise break. A
  client with no connection has no hook: the connect callback builds its
  clients without one and handles the typed failures itself (`invalid_grant`
  at its code exchange is a notice and never marks broken). One Flask error
  handler remains for the response only: it answers `spotify_app_broken` for
  the three typed failures that do reach it, so they aren't read as "log in
  again". After that, `require_auth` refuses a `broken` connection before it
  builds a client.
- **A broken app** is `status = broken` plus a `broken_reason` on the
  connection. Nothing is stored on schedules: "paused" means the workspace's
  connection isn't `ok`, checked when a run is admitted and when it is
  claimed. Phase 08 holds the status and action matrix.
- **Later, not built here:** several workspaces per person in the product,
  SoundCloud connections, removing an app or deleting a workspace (#123), and
  moving music between a person's Spotify accounts across workspaces.

## Staging

From phase 06 onward, `feat/workspaces` runs on a staging environment
against real Spotify and real Google, so the first real-service run isn't the
wipe. The owner sets it up in phase 06 step 8 and tears it down in phase 09
step 14, right after the cutover.

**This reverses a recorded decision, for a reason.** A staging environment
existed on 2026-10-17 and was removed on 2026-10-18, and
`documentation/guides/production.md` and `.railway/README.md:9` say there is
none. While this plan's staging exists, this section is the guidance and those
two sentences are superseded; they are correct again once it is torn down, so
neither file is edited. The precedent is to remove staging right after the
cutover it served, and phase 09 does.

- **Address:** `staging.raccoon.fm`, a custom domain on the Vercel project,
  assigned to the `feat/workspaces` branch, with a `staging` record in
  raccoon.fm's DNS. It needs raccoon.fm to be bought, but not phase 01b's
  flip. Vercel's own branch URL is not an option: the project's protection is
  `all_except_custom_domains`, so a branch URL sits behind a Vercel login,
  which Google and Spotify redirects and any tester can't pass. Open
  `https://staging.raccoon.fm` in a private window to confirm there is no
  Vercel login.
- **Neon:** a new branch of `shuffify-db`, emptied (drop and recreate the
  public schema), with `DATABASE_URL` set to **that branch's pooled host**, the
  host type production's api and worker use today (checked read-only
  2026-11-10; the api's pre-deploy reads the same shared variable). Staging
  then differs from production in data, not topology: the first real PKCE
  rotation through the row-locked refresher runs through the pooler here, not
  on cutover day. Staging's first deploy builds the schema through the
  pre-deploy script (phase 06 step 7), which rehearses the cutover's database
  step. Verify the branch identity from Neon's metadata before any migration
  (`.railway/README.md`).
- **Railway:** not a separate project: a `staging` environment inside the
  existing project `shuffify`, for the same `api` and `worker` services, with
  its own variables and its own `redis`, deploying from `feat/workspaces`.
  `.railway/railway.ts` is written for production and refuses to evaluate
  without its source inputs, so link the environment and plan with
  `SHUFFIFY_RAILWAY_GITHUB_REPO=chrisrogers37/shuffify` and
  `SHUFFIFY_RAILWAY_GITHUB_BRANCH=feat/workspaces`, inspect the plan, and
  apply it to the staging environment only
  (`railway link --project shuffify --environment staging`). The file runs
  unchanged; any edit it needs is made in phase 06 step 8 and must leave
  production's plan output the same.
  - **Create the environment empty,** not by duplicating production, and set
    every shared variable by hand: duplicating would copy `DATABASE_URL`, and
    the api's pre-deploy would then run against production. Before the first
    deploy, read back `DATABASE_URL` and check it is the staging branch's
    host.
  - Its own `PUBLIC_ORIGIN` and `GOOGLE_REDIRECT_URI` on
    `staging.raccoon.fm` (the Spotify redirect URL is `PUBLIC_ORIGIN` plus
    `/spotify/callback`), and its own `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`
    and `WORKER_CONTROL_TOKEN`. It never uses the production URL.
- **Vercel:** the build reads `RAILWAY_API_URL` when it runs
  (`web/scripts/build-vercel-output.mjs:13-22, 74`), and today every preview
  forwards to the production API (`documentation/guides/production.md`). Set
  `RAILWAY_API_URL` to the staging API scoped to the `feat/workspaces` branch
  only, and check that the branch's build output rewrites to the staging API
  and that `https://staging.raccoon.fm` reaches it (the staging api's logs
  show the request, production's don't). A staging preview that forwarded to
  the production API would sign people in against production.
- **Spotify:** staging reuses the owner's existing app. The staging callback
  (`https://staging.raccoon.fm/spotify/callback`) is added to its redirect URIs; an
  app can have several. Spotify allows one development-mode app per
  developer, so a second app isn't an option.
- **Google:** an OAuth client with the staging redirect URI. It can stay in
  Testing mode with the owner listed as a test user; staging has no need of a
  published consent screen.
- **Tear-down (phase 09, right after the cutover):** delete the `staging`
  environment in Railway, the Neon branch, the Vercel branch variable and the
  `staging.raccoon.fm` domain with its DNS record, and the staging Google
  client. The cutover deletes the old Spotify app that staging uses, so
  staging would only show `app_rejected` afterwards.

The staging checks are part of the acceptance of phases 06, 07 and 08, and a
gate in the cutover.

## Decision Forks

The design-level decisions are ratified in the design doc and not repeated
here.

### Fork F1: How the work reaches production when every merge deploys
- **Context:** Every merge to main deploys the Railway api and worker and Vercel, and the new model replaces production's data. The work has to reach main without exposing a half-built model.
- **Options:**
  - **(a) An integration branch;** one merge to main is the cutover. Nothing
    half-built deploys, and each phase gets full CI, but the branch drifts
    from main (merged in at every phase) and the real-service run needs a
    staging environment.
  - **(b) A feature flag on main.** Every phase deploys and the old site keeps
    running, but the flag threads through every route and query the re-key
    touches, and the flag's off state would have to keep the old model
    working beside the new tables.
  - **(c) Wipe at the first sign-in phase.** No branch and no flag, but
    production runs a half-built model for the weeks the later phases take,
    and every merge in that time deploys it.
- **Lean:** (a) · **Ratifier:** owner · **Status:** locked (owner, 2026-11-10, in conversation)

### Fork F2: The identity model
- **Context:** Today a user is a Spotify account. The product needs a person who signs in separately from Spotify, and a place for product data that outlives any one Spotify connection.
- **Options:**
  - **(a) Keep `users` as the Spotify account,** one per workspace.
  - **(b) Person → workspaces → service connections,** with product data
    re-keyed to the workspace.
- **Decision:** (b), the owner's model. If built fresh, the workspace is the
  source of truth, and Spotify is the first of several services.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F3: How Google sign-in is built
- **Context:** Spotify stops being a sign-in method, so Raccoon needs its own. The owner already runs a hand-rolled Google OIDC flow in storydump.
- **Options:**
  - **(a) Port storydump's hand-rolled OIDC code flow.** No new dependency
    and a module the owner already runs; it carries its own claim checks,
    which the tests must cover.
  - **(b) An OIDC client library (for example Authlib).** Less code to own
    and maintained discovery and JWKS handling, but a new dependency and a
    session and error model to fit around the existing hardened routes.
  - **(c) Google's browser sign-in button (Google Identity Services).** No
    redirect round trip, but it needs Google's script on the page, a CSP
    change and a server endpoint that accepts a posted ID token, and the
    session and consent checks have to be reshaped around it.
- **Decision:** (a):
  - scopes `openid email profile`;
  - the ID token is checked by its `iss`, `aud`, `exp` and nonce claims
    after a direct TLS exchange with Google's token endpoint (OIDC Core
    §3.1.3.7), so no JWKS fetch is needed;
  - nonce = SHA-256(state);
  - identity is keyed on `sub`.

  A loopback-only `GOOGLE_BASE_URL` override lets the harness use a fake,
  the same way `SPOTIFY_BASE_URL` does.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F4: New schema as a migration, or a fresh base
- **Context:** The identity tables and the re-key change the schema a great deal, and production is wiped rather than migrated. The repo's chain tests and drift checks assume an ordinary migration chain.
- **Options:**
  - **(a) Ordinary migrations on the existing chain.** The chain tests and
    drift checks keep working, and databases that aren't wiped (development,
    the staging branch before it is emptied) can still upgrade. The
    migrations are never run on production, which is built by
    `create-schema`.
  - **(b) A fresh base revision** replacing the chain. Smaller, with nothing
    to keep honest, but it rewrites history, breaks rollback to earlier
    releases and the chain tests, and gives development databases nothing to
    upgrade through.
  - **(c) No migration at all,** relying on `create-schema` and the wipe.
    The least work, but CI's migration drift and chain checks would go red or
    be switched off, and the head would have no revision to stamp.
- **Decision:** (a). The wipe runs `create-schema` once at cutover.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F5: How app secrets are encrypted
- **Context:** The first design stored each workspace's client secret. F8 removed the need before it was built.
- **Superseded 2026-11-10 by F8 (PKCE): no app secret is stored.**
- **What it was:** a `MultiFernet` keyring in `APP_SECRET_KEYS` (newest
  first; storydump's `src/utils/encryption.py` pattern), with each
  ciphertext bound to its connection's id, a `flask rotate-app-secrets`
  command, and a key separate from `TOKEN_ENCRYPTION_KEY`. Not chosen then:
  a cloud key manager.
- **Why it went:** it would have brought back the keyring and rotate command
  PR #44 (d30081a) removed, to protect a value Raccoon doesn't need.
- **Ratifier:** owner · **Status:** superseded (owner, 2026-11-10)

### Fork F6: One table or two for the person and the workspace
- **Context:** The model has a person who signs in and a workspace that owns product data. They could be one row or two.
- **Options:**
  - **(a) One table,** where the person is the workspace. Fewest tables,
    but a second workspace per person would mean a second login row, and
    sign-in identity and product data stay tangled.
  - **(b) Two tables,** `users` (the person) and `workspaces`, one to many.
  - **(c) Three tables,** adding a membership table between people and
    workspaces. It would allow shared workspaces, which the epic excludes, at
    the cost of a join on every query.
- **Decision:** (b), following F2.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F7: How the re-key in phase 04 is done
- **Context:** Phase 04 renames the owning column on twelve tables plus `job_executions`, across about 200 files, on a branch no user runs.
- **Options:**
  - **(a) One mechanical rename of `user_id` to `workspace_id`** across the
    affected tables and code, in one PR.
  - **(b) Table by table,** over several PRs.
- **Decision:** (a), in a single sweep. Everything lands on the integration
  branch and there's no data to migrate, so a single consistent rename is
  safer than a long half-renamed state.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F8: How a workspace's app authenticates to Spotify
- **Context:** Each workspace brings its own Spotify app, and Spotify allows PKCE with a client ID alone. The alternative is storing every person's client secret.
- **Options:**
  - **(a) Authorization Code with PKCE, client ID only.** Nothing secret to
    ask for, store, encrypt, rotate or scrub, and Raccoon never holds
    another party's secret. Refresh tokens rotate and may be single-use, so
    refresh must be serialised.
  - **(b) Authorization Code with a stored client secret** (the old F5).
    Familiar, but it needs an encryption key, rotation, scrubbing and a
    "secret rejected" case, and people paste a credential they don't need
    to.
- **Decision:** (a). Authorize with `code_challenge_method=S256`; exchange
  and refresh send `client_id` in the body; no Basic authorization anywhere
  (`auth.py:236, 297, 382`).
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F9: Spotify's February 2026 development-mode rules
- **Context:** Spotify's rules limit development-mode apps, and the bring-your-own-app model depends on them staying as they are.
- **Options:**
  - **(a) Accept them** as a risk of the model: Premium to own an app, one
    development-mode client ID per developer, five users per client ID, and
    Spotify's statement that development mode shouldn't be a business
    foundation.
  - **(b) Pause the project** until Spotify offers a path for apps like this.
- **Decision:** (a). Setup step 1 says Premium is required, and "use a
  different app" means deleting the old app first. The policy risk is in the
  Risks table.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F10: Whether there's a staging run before the wipe
- **Context:** The cutover wipes production and deletes the owner's only Spotify app in one sitting. Nothing else in the plan runs against real Spotify and Google before it.
- **Options:**
  - **(a) Staging on real Spotify and Google from phase 06.** More setup,
    but real-service problems surface before the irreversible step.
  - **(b) Fakes only until cutover day.** Cheaper, and the first real run is
    the wipe.
- **Decision:** (a). See Staging. Staging is `staging.raccoon.fm` with its own
  Railway environment and Neon branch, and is torn down in phase 09.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F11: When the domain moves
- **Context:** The redirect URL every person pastes is on raccoon.fm, and the current site is on shuffify.app. Moving the domain touches DNS, Vercel, Railway and Spotify.
- **Options:**
  - **(a) Early, on main (phase 01b),** so cutover day is only the wipe and
    merge. The current site runs on raccoon.fm for a while.
  - **(b) On cutover day,** with the domain variables switched alongside the
    wipe: more to go wrong on the irreversible day, and the CSRF origin check
    rejects writes on the old domain once `PUBLIC_ORIGIN` changes.
- **Decision:** (a), when the owner buys raccoon.fm.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F12: The "Name your workspace" setup step
- **Context:** The mockups had a naming step. With one workspace per person and no way to switch, the name is never shown.
- **Options:**
  - **(a) Drop it.** The workspace is named "My workspace" and there's no
    endpoint to rename it.
  - **(b) Keep it,** with `PATCH /api/workspace`, for a name nobody sees
    until a person can have several workspaces.
- **Decision:** (a). Naming returns with workspace switching.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F13: Which path receives Spotify's redirect
- **Context:** Spotify returns to one redirect URL per app, and every person pastes the same one. Today `/callback` is both the sign-in and the connect return, and it clears the session.
- **Options:**
  - **(a) Keep `/callback`,** rewritten as the connect callback only. No new
    redirect URL on any app and no new forwarding, but the old sign-in
    meaning of the path lingers and the Spotify sign-in code and the connect
    code would share one route during the rewrite.
  - **(b) A new path, `/spotify/callback`.** Every Spotify app (the owner's
    existing one, staging, every person's guide copy) gets the new redirect
    URL, and the path needs forwarding added, but the connect flow has its own
    route and the old `/callback`, with its `session.clear()`, can be deleted
    cleanly.
- **Decision:** (b). The redirect URL is `https://raccoon.fm/spotify/callback`
  (staging: `https://staging.raccoon.fm/spotify/callback`), which is
  `PUBLIC_ORIGIN` plus the path, so the `SPOTIFY_REDIRECT_URI` setting is
  retired with the site app's ID and secret (phase 06). Phase 01b adds the new
  URL to the owner's current app. Phase 05 adds `/spotify/callback` to the
  Vercel forwarding rule and the harness preview proxy with the other new
  paths, and builds the stopgap on it. Phase 06 deletes the old `/callback`
  route, its forwarding and the Spotify sign-in code. The new route never calls
  `session.clear()`: a person already signed in with Google stays signed in,
  and only `spotify_connect` is removed from the session.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F14: Where the refresh token lives
- **Context:** A PKCE refresh can invalidate the old refresh token, and two code paths refresh: the web request and the worker. Today they hold separate copies.
- **Options:**
  - **(a) The database only.** Web and worker both refresh in a short
    row-locked transaction (`SELECT ... FOR NO KEY UPDATE` on the connection
    row, with a transaction-local lock timeout) around read, refresh and
    store. The session keeps only the access token.
    One home, and correct through Neon's pooled host, but the web path now
    needs a database read to refresh and a lock timeout has to be handled as
    transient.
  - **(b) Session and database both,** with the web copy written back after a
    refresh. The copies can still drift between a web refresh and a worker
    refresh.
  - **(c) The database only, serialised by the worker's credential advisory
    lock.** Fewer new parts, but it isn't reliable through the pooler, and
    the worker holds that lock for a whole run, so a web refresh would time
    out or sign the person out. Rejected.
- **Decision:** (a), because a PKCE refresh can invalidate the old token and
  a stale copy would then be read as a broken app. It ships as phase 02b,
  separately from the credential seam, so it can be reverted alone. A rotated
  token whose save failed stays in memory, in that process, until a store
  succeeds.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

## Companion Plans

- [`../120-workspaces-byo-spotify-app.md`](../120-workspaces-byo-spotify-app.md):
  the approved design.
- Open security issues on the same code: #126 and #128 to #131 (draft PRs
  #144 and #145) don't gate the branch; main is merged into `feat/workspaces`
  as each lands (see Dependencies). These phases keep them solvable per
  workspace and don't fix them.
- #141 (the refresh-token expiry reminder) stays a separate issue and is not
  built now, because the owner is the only user. A refresh token that expires after its long lifetime fails
  with `invalid_grant`, so it surfaces through phase 08 as the access-removed
  case with a reconnect fix; #141 adds the reminder before that happens.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Spotify's February 2026 development-mode rules: Premium to own an app, one client ID per developer, five users per client ID, and Spotify's statement that development mode shouldn't be a business foundation | Spotify could tighten or end development mode, and the bring-your-own-app model stops working | Accepted by the owner (F9, 2026-11-10). Setup says Premium is required. Revisit if Spotify changes the rules |
| The integration branch drifts while parallel sessions merge security fixes into the same code | Conflicts at cutover, in code that phases 04 and 05 rewrite; a fix lost | The fixes don't gate the branch. Merge main into `feat/workspaces` at the start of every phase and as each security fix lands, with `python scripts/check-migration-chain.py` after each; phase 09 starts with a final merge and a full CI run |
| The phase 04 re-key misses a query, so one workspace sees another's data | Cross-workspace data leak | Phase 04 adds a two-workspace isolation test per service and a test that only `workspaces` and `login_history` keep a `user_id` column |
| The three broken-app cases look alike to a person | The wrong fix is tried | Phase 08's matrix gives each reason its own fix and says what a successful fix looks like; `invalid_client` after a first connect means the app is gone, and the page says so |
| A wrong client ID never returns to Raccoon (Spotify's own "INVALID_CLIENT" page) | The person is stranded on Spotify's page | The format check catches typos; the setup copy shows the page and says to come back and fix the ID |
| A PKCE refresh rotates the token and a second refresher uses the old one | Spurious `invalid_grant`, an app wrongly marked broken | One home for the refresh token, refreshed in a short row-locked transaction (`FOR NO KEY UPDATE`, transaction-local `lock_timeout`) that is correct through Neon's pooled host (phase 02b). A lock timeout is `SpotifyRefreshUnavailable`, a transient: a 503 on the web (inside the 401 retry too) and a deferral in the worker, never a sign-out. A rotated token whose save failed is kept in memory, per process. The owner's throwaway script checks real Spotify's rotation before 02b is built, and staging runs it through the pooled host. 02b is its own PR and reverts alone |
| A failure that started before a reconnect marks the fresh connection broken | Schedules paused again right after the fix | A credential generation bumped on every connect and replacement; the client captures it when it is built and `mark_broken` applies only if it is still current (phases 03, 08) |
| A typed failure is swallowed by a service that catches 403s or Spotify errors broadly, or read as "log in again" | The app is never marked broken, or the person is told a fix that can't help | Detection is at the client boundary: a hook where the failure is raised, built with the generation captured when the client was built. A handler only chooses the response, and `require_auth` refuses a `broken` connection (phases 02, 08) |
| A bad or reused code at the connect callback marks a healthy app broken | Schedules paused by mistake | `invalid_grant` at the exchange is only an `oauth_failed` notice, in every status, and the callback builds its clients without the hook (phases 06, 08) |
| The wrong Spotify account is bound for good | The workspace's data points at another account | Every authorize URL carries `show_dialog=true`; the bound account is checked on reconnect |
| A Google client secret leaks into logs or Sentry | Credential exposure | `GOOGLE_CLIENT_SECRET` is registered in `_SENTRY_SECRET_CONFIG_ATTRS` and covered by a `_strip_pii` test (phase 05) |
| The cutover runs in the wrong order | The deploy fails, or the new migrations run over production's old data | Phase 09's runbook orders it (stop the old worker, snapshot, wipe and merge back to back, check, redeploy the worker), runs `create-schema` through the pre-deploy script that phase 06 ships, and rehearsed that build of an emptied database on staging. Production's `GOOGLE_*` variables and `PUBLIC_ORIGIN` are read back before the wipe |
| The merge builds a new worker before the api has built the schema | The new worker refuses on its startup schema check and uses up its restart retries (the deployment shows FAILED); it can't run against the wrong schema | The old worker's deployment is removed first, and the new one is redeployed by hand once the api is at head (phase 09 step 11) |
| Pre-deploy now builds a new database, reversing the rule that an empty database fails the deploy | An empty database behind a wrong `DATABASE_URL` is built and booted instead of failing the deploy | `create-schema` builds only with no revision and no tables and refuses any tables without a revision; the Neon branch identity is verified before staging's first deploy and the cutover; phase 06 step 7 records keeping it after cutover and rewrites the two tests and two docs that state the old rule |
| The owner deletes their only Spotify app on cutover day, before the guide has been run from scratch | A wiped database, no app and no way back if the dashboard or the copy blocks app creation | Accepted by the owner (2026-11-10): sole user, one sitting. Staging walks the guide against the existing app first, and a friend with Premium may run it from scratch on staging. The Neon snapshot rolls the database back until the old app is deleted (phase 09) |
| Acceptance never exercises restore | The epic's differentiator could be broken by the credential path and nobody would know | The first scheduled run's snapshot is restored through the workspace's app (phase 09 step 13) |
| Harness work (fake Google, several fake Spotify apps with PKCE, error injection) is bigger than it looks | Phases slip | Fake Google lands in phase 05 and fake apps in phase 06, each with its phase |
| Staging hides a real-service problem until phase 06 | A late Google or Spotify surprise | Staging stands up in phase 06, as soon as real Spotify is involved; phase 05's fake-Google tests run against Google's documented behaviour, and the first real Google sign-in is the first staging check |
| Staging can't be deployed as the repo stands, or reaches production | A stalled phase 06, or staging signing people in against production | `staging.raccoon.fm` (a custom domain, past Vercel's branch protection), its own Railway environment created empty with its own `DATABASE_URL`, its own Neon branch on the pooled host, like production, and a branch-scoped Vercel variable checked against the staging API |
| Staging is left running after it has served | Cost, a second place secrets live, and a dead app that shows `app_rejected` | Phase 09 deletes the `staging` environment right after the cutover |
| The Google consent screen is published and sign-up has no gate, so anyone can sign up before #123's safeguards | Unwanted accounts, load | The owner is the only user at first. #139's per-address limit of 10 a minute on the sign-in views is the only sign-up safeguard until #123 (phase 05 applies it to the new views). Google may require brand verification for the consent screen; the basic `openid`, `email` and `profile` scopes need no sensitive-scope review |
| The domain move changes the origin the CSRF check accepts | Writes on the old domain are refused for a moment | Phase 01b flips the variables and the redirect together on a quiet moment |
| A refresh token reaches its long lifetime and stops working | A workspace's schedules pause with no warning | It surfaces through phase 08 as the access-removed case with a reconnect fix. #141 (the reminder) stays a separate issue, not built now: the owner is the only user |

## Complexity and Sequencing

| Phase | Size | Depends on | Can be built alongside (merge order follows Depends on) |
|---|---|---|---|
| 01 Rename | S | — | 02 |
| 01b Move the domain | S | 01; the owner buys raccoon.fm | 02 |
| 02 Credential seam, failures, one refresh token (02a L, then 02b L) | XL | — | 01, 01b |
| 03 Identity schema (additive) | S | 02 (the branch is cut when 02 lands) | — |
| 04 Re-key to workspace, and the Spotify account off `users` | XL | 03, and 02b (it re-points that refresher) | — |
| 05 Google sign-in | L | 04 | — |
| 06 BYO app + connect (PKCE) + pre-deploy build + staging | XL | 02, 05 (step 8, staging, also needs raccoon.fm bought) | 07 (on fixtures) |
| 07 Setup wizard | M | 05 (session shape), 06 (endpoints, redirect URL), 08 (`/setup/problem`) | 06, 08 (built on fixtures; merges after 08) |
| 08 Broken app | M | 02, 06 | 07 (on fixtures) |
| 09 Cutover | S | 01, 01b, 03–08 | — |

Only phase 09 depends on 01b. 06 isn't parallel with 08: 08 needs 06's
connection status, `mark_ok` and connect callback.

**Critical path:** 02 → 03 → 04 → 05 → 06 → 08 → 07 → 09.

## Test Plan

- Each phase brings its own tests.
- At the workstream level, the harness e2e suite signs in through fake
  Google, creates a workspace, attaches a fake Spotify app and connects with
  PKCE. That covers every user-facing flow end to end, including one test per
  broken-app reason.
- A two-workspace isolation suite proves that one workspace can't read or
  change another's data.
- From phase 06, the owner runs the same flows on staging against real
  Spotify and Google.
- The owner's acceptance pass is in phase 09: on cutover day, the owner
  deletes the old Spotify app and creates a new one from the in-product guide
  alone, timed against a 15-minute threshold, rebuilds their routines, and
  restores a playlist from the first scheduled run's snapshot.

## Verification Checklist

- [ ] All ten phases are merged, with CI green on each.
- [ ] `git grep -n "SPOTIFY_CLIENT_ID\|SPOTIFY_CLIENT_SECRET\|SPOTIFY_REDIRECT_URI" -- shuffify config.py scripts harness .railway .github .env.example README.md tests`
      returns nothing after phase 06.
- [ ] `git grep -n "client_secret" -- shuffify/spotify harness/fake_spotify`
      returns nothing after phase 06.
- [ ] `git grep -n "from_env\|from_flask_config" -- shuffify` returns nothing
      after phase 06.
- [ ] The two-workspace isolation suite passes.
- [ ] Staging has run phases 06 to 08 against real Spotify and Google.
- [ ] Phase 09's runbook has run: the owner signed up on raccoon.fm, created
      a new Spotify app from the in-product guide in under 15 minutes,
      connected, rebuilt their routines and restored the first scheduled run
      from its snapshot, and staging was deleted.
- [ ] Phase 02's two PRs merged separately, the throwaway PKCE check against
      the owner's existing app was run between them, and 02b's revert was
      checked.

## What NOT To Do

- Don't fall back to a site-wide Spotify app anywhere, including tests and
  the harness.
- Don't ask for, accept or store a client secret.
- Don't migrate today's data; the design calls for a wipe.
- Don't rename internal identifiers (the `shuffify` package, cookie, cache
  prefixes) in these phases. The owner wants the code renamed from
  `shuffify` to `raccoon` later, in one separate mechanical sweep (see
  #122).
- Don't build workspace switching, workspace naming, removing an app or
  deleting a workspace (#123), SoundCloud, or moving music between accounts.
- Don't fix #126, #128 to #131 or #141 inside these phases.
- Don't protect the refresh token with a lock held around a whole run, or
  with an advisory lock at all, and don't use a plain `FOR UPDATE` or a
  session-level `SET lock_timeout` for the row lock.
- Don't detect a broken app by a failure reaching a Flask handler; detect it
  at the client boundary.
- Don't build launch safeguards here (abuse limits, account deletion, legal
  pages); #123 owns them. #139's per-address limit is the only sign-up
  safeguard until then.

## Later work

- **The #122 rename sweep** includes renaming every service and project on the
  infrastructure providers: the GitHub repository, the Railway project and
  services, the Vercel project, Neon, Redis, Sentry, the Google Cloud project
  and the Spotify app. It is tracked on #122 and isn't part of these phases.

## Context
- Source skill: forge · Area: auth, models, services, worker, web · Effort: XL · Risk: High · Priority: High
