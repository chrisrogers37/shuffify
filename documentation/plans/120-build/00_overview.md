---
title: "[plan] Workspaces and bring-your-own Spotify app — build plan (#120)"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
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
- one groundwork phase on main with no visible change;
- six phases on an integration branch that build the new model and sign-in,
  run against real Spotify and Google on staging from phase 06;
- the cutover, which merges that branch and wipes production.

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
  and Vercel.
- **Schema.**
  - `flask create-schema` (`shuffify/cli.py:15-53`) builds a fresh
    database and stamps head. It leaves a database that already has a
    revision alone, and refuses one that has the tables but no revision.
  - `migrations/env.py:54-66` refuses `upgrade` on a database with no
    revision.
  - `scripts/pre-deploy.sh` runs `flask db upgrade` with
    `SHUFFIFY_MIGRATION_STEP=1`; the app's startup schema check
    (`shuffify/__init__.py:214, 284`) otherwise refuses a database not at
    head.
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
- Security fixes #126 and #128 to #131 (draft PRs #144 and #145) merged to
  main before `feat/workspaces` is cut.

### Blocks

- #121 (MCP server) and #123 (public launch).

### Steps

| # | Phase | Lands on | Size |
|---|---|---|---|
| 01 | [Rename what users see to Raccoon](01_rename-raccoon.md) | main | S |
| 01b | [Move the domain to raccoon.fm](01b_move-domain.md) | main | S |
| 02 | [One credential seam, failures that keep their cause, one refresh token](02_credential-seam-and-failures.md) | main | L |
| 03 | [Person, workspace and Spotify connection tables](03_identity-schema.md) | integration | M |
| 04 | [Move product data from the Spotify account to the workspace](04_rekey-to-workspace.md) | integration | XL |
| 05 | [Google sign-in and the workspace in the session](05_google-sign-in.md) | integration | L |
| 06 | [Attach a Spotify app; connect Spotify through it with PKCE](06_byo-app-and-connect.md) | integration | L |
| 07 | [Guided setup in the web app](07_setup-wizard.md) | integration | M |
| 08 | [Broken app: detect, pause, guide, resume](08_broken-app.md) | integration | M |
| 09 | [Cutover: wipe, merge, owner acceptance](09_cutover.md) | main | S |

Phase 01b is a second main-branch phase rather than a second PR of 01. The
rename has no external dependency and should merge as soon as it's ready;
the domain move waits for the owner to buy raccoon.fm, touches DNS, Vercel,
Railway and Spotify rather than application code, and needs its own rollback.
Keeping them apart stops one holding up the other. The numbering of the
other phases doesn't change.

The integration branch is `feat/workspaces`, cut from main after phase 02
merges and after #126 and #128 to #131 have landed (they touch code that
phases 04 and 05 rewrite). Phase PRs target it with full CI. Main is merged
into it at the start of every phase.

## Architecture

```
users (person, Google login)
  └─1:N─ workspaces ──────────────── source of truth for product data
           ├─1:0..1─ spotify_connections   (one per workspace; other services later)
           │           client_id            unique site-wide (constraint added in 06)
           │           spotify_id           unique site-wide, set on first connect, then fixed
           │           refresh_token        encrypted (TOKEN_ENCRYPTION_KEY); the only copy
           │           status               unchecked | ok | broken (+ reason)
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
  `spotify_id`, profile fields and the refresh token. The connection holds
  the app's client ID too, because a workspace has one Spotify app and the
  account connected through it. The Spotify account's id keeps the name
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
  worker refresh under the per-connection credential lock: read the stored
  token, refresh, store the rotated token, release. The session holds the
  access token and its expiry only.
- **The session holds `user_id` and the current `workspace_id`.** The
  Spotify access token is present only once Spotify is connected. Google
  sign-in and Spotify connect use separate session keys (`google_state`,
  `spotify_connect`). `/api/session` reports the person, the workspace and
  its setup state, so the web app knows whether to show the setup steps,
  the "uh oh" page or the app.
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
wipe. The owner sets it up as part of phase 06.

- **Railway:** a second environment in project `shuffify` with its own
  `api`, `worker` and `redis`, built from `feat/workspaces`. Its shared
  variables give it its own `PUBLIC_ORIGIN`, `SPOTIFY_REDIRECT_URI` and
  `GOOGLE_REDIRECT_URI` on the staging address, and its own `SECRET_KEY`,
  `TOKEN_ENCRYPTION_KEY` and `WORKER_CONTROL_TOKEN`. `.railway/README.md:9`
  already requires a nonproduction environment to have its own Neon branch
  and never to point at the production URL.
- **Neon:** a new branch of `shuffify-db`, emptied (drop and recreate the
  public schema, then `create-schema`), so no production data or tokens are
  copied. That is also a rehearsal of the cutover's database step.
- **Vercel:** the build reads `RAILWAY_API_URL` when it runs
  (`web/scripts/build-vercel-output.mjs:13-22, 74`), so staging needs a variable
  scoped to the `feat/workspaces` branch pointing at the staging API, plus a
  stable address assigned to that branch: `staging.raccoon.fm` if the owner
  has the domain, otherwise Vercel's own branch URL. Check that Vercel's
  deployment protection lets Google and Spotify redirect back to that
  address without a Vercel login.
- **Spotify:** staging reuses the owner's existing app. The staging
  callback (`https://<staging address>/callback`) is added to its redirect
  URIs; an app can have several. Spotify allows one development-mode app per
  developer, so a second app isn't an option.
- **Google:** an OAuth client in Testing mode with the staging redirect URI
  and the owner (and any tester) listed as test users.
- **After cutover** the owner deletes the old Spotify app, so staging gets
  reconnected through the new one or left idle.

The staging checks are part of the acceptance of phases 06, 07 and 08, and a
gate in the cutover.

## Decision Forks

The design-level decisions are ratified in the design doc and not repeated
here.

### Fork F1: How the work reaches production when every merge deploys
- **Options:**
  - **(a) An integration branch;** one merge to main is the cutover.
  - **(b) A feature flag on main.**
  - **(c) Wipe at the first sign-in phase.**
- **Lean:** (a) · **Ratifier:** owner · **Status:** locked (owner, 2026-11-10, in conversation)

### Fork F2: The identity model
- **Options:**
  - **(a) Keep `users` as the Spotify account,** one per workspace.
  - **(b) Person → workspaces → service connections,** with product data
    re-keyed to the workspace.
- **Decision:** (b), the owner's model. If built fresh, the workspace is the
  source of truth, and Spotify is the first of several services.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F3: How Google sign-in is built
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
- **Options:**
  - **(a) One mechanical rename of `user_id` to `workspace_id`** across the
    affected tables and code, in one PR.
  - **(b) Table by table,** over several PRs.
- **Decision:** (a), in a single sweep. Everything lands on the integration
  branch and there's no data to migrate, so a single consistent rename is
  safer than a long half-renamed state.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F8: How a workspace's app authenticates to Spotify
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
- **Options:**
  - **(a) Staging on real Spotify and Google from phase 06.** More setup,
    but real-service problems surface before the irreversible step.
  - **(b) Fakes only until cutover day.** Cheaper, and the first real run is
    the wipe.
- **Decision:** (a). See Staging.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F11: When the domain moves
- **Options:**
  - **(a) Early, on main (phase 01b),** so cutover day is only the wipe and
    merge. The current site runs on raccoon.fm for a while.
  - **(b) On cutover day,** with the domain variables switched alongside the
    wipe: more to go wrong on the irreversible day, and the CSRF origin check
    rejects writes on the old domain once `PUBLIC_ORIGIN` changes.
- **Decision:** (a), when the owner buys raccoon.fm.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F12: The "Name your workspace" setup step
- **Options:**
  - **(a) Drop it.** The workspace is named "My workspace" and there's no
    endpoint to rename it.
  - **(b) Keep it,** with `PATCH /api/workspace`, for a name nobody sees
    until a person can have several workspaces.
- **Decision:** (a). Naming returns with workspace switching.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F13: Which path receives Spotify's redirect
- **Options:**
  - **(a) Keep `/callback`,** rewritten as the connect callback only. It
    runs for a signed-in person, never clears the session, and uses its own
    session key.
  - **(b) A new path** such as `/spotify/callback`. Cleaner names, but every
    Spotify app (the owner's existing one, staging, every person's guide
    copy) would need a new redirect URL, and the path needs forwarding
    added.
- **Decision:** (a). The redirect URL stays `https://raccoon.fm/callback`,
  which phase 01b already adds to the current app, and Vercel and the
  harness preview already forward `/callback`. The rewrite drops
  `session.clear()`: a person who is already signed in with Google stays
  signed in, and only `spotify_connect` is removed from the session.
- **Ratifier:** owner · **Status:** proposed by the plan author; the owner is
  to confirm.

### Fork F14: Where the refresh token lives
- **Options:**
  - **(a) The database only.** Web and worker both refresh under the
    connection's credential lock. The session keeps only the access token.
  - **(b) Session and database both,** with the web copy written back after a
    refresh. The copies can still drift between a web refresh and a worker
    refresh.
- **Decision:** (a), because a PKCE refresh can invalidate the old token and
  a stale copy would then be read as a broken app (phase 02 builds it).
- **Ratifier:** owner · **Status:** proposed by the plan author; the owner is
  to confirm.

## Companion Plans

- [`../120-workspaces-byo-spotify-app.md`](../120-workspaces-byo-spotify-app.md):
  the approved design.
- Open security issues on the same code: #126 and #128 to #131 land on main
  (draft PRs #144 and #145) before the branch is cut; #141 stays open. These
  phases keep them solvable per workspace and don't fix them.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Spotify's February 2026 development-mode rules: Premium to own an app, one client ID per developer, five users per client ID, and Spotify's statement that development mode shouldn't be a business foundation | Spotify could tighten or end development mode, and the bring-your-own-app model stops working | Accepted by the owner (F9, 2026-11-10). Setup says Premium is required. Revisit if Spotify changes the rules |
| The integration branch drifts while parallel sessions merge security fixes into the same code | Conflicts at cutover; a fix lost | #126 and #128 to #131 merge before the branch is cut; merge main into `feat/workspaces` at the start of every phase; phase 09 starts with a final merge and a full CI run |
| The phase 04 re-key misses a query, so one workspace sees another's data | Cross-workspace data leak | Phase 04 adds a two-workspace isolation test per service and a test that only `workspaces` and `login_history` keep a `user_id` column |
| The three broken-app cases look alike to a person | The wrong fix is tried | Phase 08's matrix gives each reason its own fix and says what a successful fix looks like; `invalid_client` after a first connect means the app is gone, and the page says so |
| A wrong client ID never returns to Raccoon (Spotify's own "INVALID_CLIENT" page) | The person is stranded on Spotify's page | The format check catches typos; the setup copy shows the page and says to come back and fix the ID |
| A PKCE refresh rotates the token and a second refresher uses the old one | Spurious `invalid_grant`, an app wrongly marked broken | One home for the refresh token; web and worker both refresh under the credential lock (phase 02) |
| The wrong Spotify account is bound for good | The workspace's data points at another account | Every authorize URL carries `show_dialog=true`; the bound account is checked on reconnect |
| A Google client secret leaks into logs or Sentry | Credential exposure | `GOOGLE_CLIENT_SECRET` is registered in `_SENTRY_SECRET_CONFIG_ATTRS` and covered by a `_strip_pii` test (phase 05) |
| The cutover runs in the wrong order | The deploy fails | Phase 09's runbook orders it, runs `create-schema` through the pre-deploy script, and was rehearsed on staging |
| Harness work (fake Google, several fake Spotify apps with PKCE, error injection) is bigger than it looks | Phases slip | Fake Google lands in phase 05 and fake apps in phase 06, each with its phase |
| Staging hides a real-service problem until phase 06 | A late Google or Spotify surprise | Staging stands up in phase 06, as soon as real Spotify is involved; phase 05's fake-Google tests run against Google's documented behaviour, and the first real Google sign-in is the first staging check |
| Production's Google client stays in Testing mode | Only listed test users can sign in | Intended until #123: it keeps #120 closed to strangers. Publishing the consent screen moves to #123 |
| The domain move changes the origin the CSRF check accepts | Writes on the old domain are refused for a moment | Phase 01b flips the variables and the redirect together on a quiet moment |

## Complexity and Sequencing

| Phase | Size | Depends on | Parallel with |
|---|---|---|---|
| 01 Rename | S | — | 02 |
| 01b Move the domain | S | 01; the owner buys raccoon.fm | 02 |
| 02 Credential seam, failures, one refresh token | L | — | 01, 01b |
| 03 Identity schema | M | 02; #126 and #128–#131 on main | — |
| 04 Re-key to workspace | XL | 03 | — |
| 05 Google sign-in | L | 04 | — |
| 06 BYO app + connect (PKCE) + staging | L | 01b, 02, 05 | 07 (on fixtures) |
| 07 Setup wizard | M | 05 (session shape), 06 (endpoints, redirect URL) | 06 |
| 08 Broken app | M | 02, 06 | 07 |
| 09 Cutover | S | 01, 01b, 03–08 | — |

**Critical path:** 03 → 04 → 05 → 06 → 08 → 09.

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
- The owner's acceptance pass, including creating a brand-new app from the
  in-product guide alone, is in phase 09.

## Verification Checklist

- [ ] All ten phases are merged, with CI green on each.
- [ ] `git grep -n "SPOTIFY_CLIENT_ID\|SPOTIFY_CLIENT_SECRET" -- shuffify config.py scripts harness .railway .github .env.example README.md tests`
      returns nothing after phase 06.
- [ ] `git grep -n "client_secret" -- shuffify/spotify harness/fake_spotify`
      returns nothing after phase 06.
- [ ] The two-workspace isolation suite passes.
- [ ] Staging has run phases 06 to 08 against real Spotify and Google.
- [ ] Phase 09's runbook has run; the owner has signed up on raccoon.fm,
      created a new Spotify app from the in-product guide, connected and
      rebuilt their routines.

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
- Don't fix #126 or #128 to #131 or #141 inside these phases.
- Don't open the product to strangers before #123's safeguards ship.

## Context
- Source skill: forge · Area: auth, models, services, worker, web · Effort: XL · Risk: High · Priority: High
