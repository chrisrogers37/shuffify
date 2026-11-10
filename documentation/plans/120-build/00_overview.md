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
Read it first: it holds the what and the why, and the ratified decisions.
This plan covers how to build it, in nine phases:
- a user-facing rename (#122) that ships on main on its own;
- one groundwork phase on main with no visible change;
- six phases on an integration branch that build the new model and sign-in;
- the launch, which merges that branch and wipes production.

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
    refresh failure.
  - `SpotifyForbiddenError` (L410) covers both playlist access and app-level
    rejection.
  - The callback already special-cases Spotify's "not registered" text
    (`shuffify/routes/core.py:220`).
- **Nothing records that an app is broken.** A scheduled run that hits an
  auth error ends `failed`. The schedule stays enabled and fires again at
  its next slot (`shuffify/services/operations/commands/routines.py:359-415,
  505-508`).
- **Identity today.**
  - `users` (`shuffify/models/db.py:72-174`) is the Spotify account, keyed
    by `spotify_id`.
  - Thirteen tables have an FK to `users.id`. `job_executions.user_id` has
    no FK (`db.py:595`). No FK sets `ondelete`.
  - `user_id` appears in 66 files under `shuffify/`.
  - The session identifies the user by the Spotify profile: `get_db_user`
    (`shuffify/routes/__init__.py:231-260`), the rate-limit key
    (`shuffify/__init__.py:141-148`) and `/api/session`
    (`shuffify/routes/contexts.py:61-76`).
  - Several services scope by `spotify_id` (`UpstreamSourceService`,
    `WorkshopSessionService`, `RaidSyncService`,
    `services/base.py:get_user_or_raise`).
- **Encryption.** `TokenService` (`shuffify/services/token_service.py`) is
  one Fernet under `TOKEN_ENCRYPTION_KEY`, with no rotation support.
- **Schedules.** Turning a schedule back on moves `definition_changed_at`
  (`shuffify/services/schedule_notifications.py:57-100`), so runs missed
  while it was off are not made up (`scheduled_admission.py:101-118`).
- **Hosting is one origin.**
  - Vercel forwards `/api`, `/login`, `/callback`, `/logout` and `/health`
    to Railway (`web/scripts/build-vercel-output.mjs:56-57`).
  - The domain lives only in environment settings: `PUBLIC_ORIGIN`,
    `SPOTIFY_REDIRECT_URI` and `RAILWAY_API_URL`.
  - CSP `form-action` allows only `accounts.spotify.com`
    (`web/security-headers.ts:13`).
- **Every merge to main deploys production:** the Railway api and worker,
  and Vercel.
- **Schema.**
  - `flask create-schema` (`shuffify/cli.py:15-53`) builds a fresh
    database.
  - `migrations/env.py:54-66` refuses `upgrade` on a database with no
    revision.
- **The harness signs in through a fake Spotify** (`harness/fake_spotify/app.py`).
  - It has no `client_credentials` grant and no Google.
  - Sign-in is the real OAuth round trip (`scripts/harness.sh:501-523`,
    `tests/e2e/conftest.py:150-168`).
- **A reference implementation exists in the owner's storydump repo:**
  - `src/services/target/google_oidc.py`: hand-rolled Google OIDC,
    verifying claims and a nonce derived from the state;
  - `src/utils/encryption.py`: a `MultiFernet` keyring.

## Implementation Plan

### Dependencies

- The approved design (PR #151).
- #122 decided: Raccoon, at raccoon.fm.

### Blocks

- #121 (MCP server) and #123 (public launch).

### Steps

| # | Phase | Lands on | Size |
|---|---|---|---|
| 01 | [Rename what users see to Raccoon](01_rename-raccoon.md) | main | S |
| 02 | [One credential seam, and Spotify failures that keep their cause](02_credential-seam-and-failures.md) | main | M |
| 03 | [Person, workspace and Spotify connection tables; app-secret encryption](03_identity-schema-and-secrets.md) | integration | M |
| 04 | [Move product data from the Spotify account to the workspace](04_rekey-to-workspace.md) | integration | L |
| 05 | [Google sign-in and the workspace in the session](05_google-sign-in.md) | integration | L |
| 06 | [Attach and check a Spotify app; connect Spotify through it](06_byo-app-and-connect.md) | integration | L |
| 07 | [Guided setup in the web app](07_setup-wizard.md) | integration | M |
| 08 | [Broken app: detect, pause, guide, resume](08_broken-app.md) | integration | M |
| 09 | [Launch: wipe, cut over, owner acceptance](09_launch.md) | main | S |

The integration branch is `feat/workspaces`, cut from main after phase 02
merges. Phase PRs target it with full CI. Main is merged into it at the start
of every phase.

## Architecture

```
users (person, Google login)
  └─1:N─ workspaces ──────────────── source of truth for product data
           ├─1:0..1─ spotify_connections   (one per workspace; other services later)
           │           client_id            unique site-wide
           │           client_secret        encrypted, write-only, bound to its row
           │           spotify_id           unique site-wide, set on first connect, then fixed
           │           refresh_token        encrypted (TOKEN_ENCRYPTION_KEY)
           │           status               unchecked | ok | broken (+ reason)
           └─1:N─ schedules, workshop_sessions, upstream_sources, playlist_pairs,
                  raid_playlist_links, playlist_preferences, track_locks,
                  pending_raid_tracks, playlist_snapshots, activity_log,
                  routine_activation_receipts, user_settings → workspace_settings,
                  job_executions
  └─1:N─ login_history (stays on the person)
```

- **`users` becomes the person.** It's keyed by Google's stable `sub`, with
  email (trusted only when verified) and display name.
- **Today's Spotify-account columns move to `spotify_connections`:**
  `spotify_id`, profile fields and the refresh token. The connection holds
  the app's credentials too, because a workspace has one Spotify app and the
  account connected through it.
- **Every per-user table is re-keyed to `workspace_id`** (phase 04).
  `login_history` stays on the person.
- **`credentials_for(connection)`** is the only way any code gets Spotify app
  credentials. With no connection or a broken one, it raises; it never falls
  back. Both client-building paths call it. The redirect URL stays a site
  setting.
- **The session holds `user_id` and the current `workspace_id`.** The
  Spotify token is present only once Spotify is connected. `/api/session`
  reports the person, the workspace and its setup state, so the web app
  knows whether to show the setup steps, the "uh oh" page or the app.
- **A broken app** is `status = broken` on the connection, plus
  `paused_reason = 'app_broken'` on each schedule. The scheduler skips paused
  schedules. Fixing the app clears both, and the `definition_changed_at` bump
  stops missed runs from being made up.
- **Later, not built here:** several workspaces per person in the product,
  SoundCloud connections, and moving music between a person's Spotify
  accounts across workspaces.

## Decision Forks

The design-level decisions are ratified in the design doc and not repeated
here.

### Fork F1: How the work reaches production when every merge deploys
- **Options:**
  - **(a) An integration branch;** one merge to main is the launch.
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
- **Decision:** port storydump's hand-rolled OIDC code flow
  (`src/services/target/google_oidc.py`):
  - scopes `openid email profile`;
  - the ID token is checked by its `iss`, `aud`, `exp` and nonce claims
    after a direct TLS exchange with Google's token endpoint (OIDC Core
    §3.1.3.7);
  - nonce = SHA-256(state);
  - identity is keyed on `sub`.

  A loopback-only `GOOGLE_BASE_URL` override lets the harness use a fake,
  the same way `SPOTIFY_BASE_URL` does.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F4: New schema as a migration, or a fresh base
- **Decision:** ordinary migrations on the existing chain. The wipe runs
  `create-schema` once at launch.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F5: How app secrets are encrypted
- **Decision:** a `MultiFernet` keyring in `APP_SECRET_KEYS` (newest first;
  storydump's `src/utils/encryption.py` pattern).
  - Each ciphertext also encrypts the connection's id, and decryption
    refuses a mismatch, so a ciphertext can't be moved to another row.
  - A CLI command re-encrypts every secret under the newest key.
  - The key lives in Railway's environment, apart from the database, and is
    separate from `TOKEN_ENCRYPTION_KEY`.
- **Not chosen:** a cloud key manager (KMS). Revisit if anyone besides the
  owner gets production access.
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F6: One table or two for the person and the workspace
- **Decision:** two tables, `users` (the person) and `workspaces`, one to
  many (F2).
- **Ratifier:** owner · **Status:** locked (owner, 2026-11-10)

### Fork F7: How the re-key in phase 04 is done
- **Options:**
  - **(a) One mechanical rename of `user_id` to `workspace_id`** across the
    affected tables and code, in one PR.
  - **(b) Table by table,** over several PRs.
- **Lean:** (a). Everything lands on the integration branch and there's no
  data to migrate, so a single consistent rename is safer than a long
  half-renamed state.
- **Ratifier:** owner · **Status:** open

## Companion Plans

- [`../120-workspaces-byo-spotify-app.md`](../120-workspaces-byo-spotify-app.md):
  the approved design.
- Open security issues on the same code: #128, #129, #130, #131 and #141.
  These phases keep them solvable per workspace and don't fix them.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The integration branch drifts while parallel sessions merge security fixes into the same code | Conflicts at launch; a fix lost | Merge main into `feat/workspaces` at the start of every phase; phase 09 starts with a final merge and a full CI run |
| The phase 04 re-key misses a query, so one workspace sees another's data | Cross-workspace data leak | Phase 04 adds a two-workspace isolation test per service; `git grep user_id` is empty outside the person tables |
| Spotify gives the same error for "secret rejected" and "app deleted" | The "uh oh" page can't pick one fix | Phase 02 records the raw OAuth error code; phase 08 shows both fixes together when the cases can't be told apart |
| A per-workspace secret leaks into logs or Sentry | Credential exposure | The secret is decrypted only inside `credentials_for`; `client_secret` key redaction; a test asserts that saving and using a secret logs nothing containing it |
| The wipe runs in the wrong order | The deploy fails | Phase 09's runbook orders it; the pre-deploy refusal fails safe |
| Harness work (fake Google, several fake Spotify apps, error injection) is bigger than it looks | Phases slip | Fake Google lands in phase 05 and fake apps in phase 06, each with its phase |
| Google's consent screen stays in "testing" | Only listed test users can sign in | Phase 09 publishes it (basic scopes need no review) |
| A web token refresh doesn't update the stored refresh token (`auth_service.py:230-255`) | The worker uses a stale token and wrongly marks an app broken | Fixed in phase 02 |

## Complexity and Sequencing

| Phase | Size | Depends on | Parallel with |
|---|---|---|---|
| 01 Rename | S | — | 02 |
| 02 Credential seam + failures | M | — | 01 |
| 03 Identity schema + secrets | M | 02 | — |
| 04 Re-key to workspace | L | 03 | — |
| 05 Google sign-in | L | 04 | — |
| 06 BYO app + connect | L | 02, 05 | 07 (on fixtures) |
| 07 Setup wizard | M | 05 (session shape) | 06 |
| 08 Broken app | M | 02, 06 | 07 |
| 09 Launch | S | 01, 03–08 | — |

**Critical path:** 03 → 04 → 05 → 06 → 08 → 09.

## Test Plan

- Each phase brings its own tests.
- At the workstream level, the harness e2e suite signs in through fake
  Google, creates a workspace, attaches a fake Spotify app and connects.
  That covers every user-facing flow end to end, including one test per
  broken-app case.
- A two-workspace isolation suite proves that one workspace can't read or
  change another's data.
- The owner's acceptance pass is in phase 09.

## Verification Checklist

- [ ] All nine phases are merged, with CI green on each.
- [ ] `git grep -n "SPOTIFY_CLIENT_ID\|SPOTIFY_CLIENT_SECRET" -- shuffify config.py .railway`
      returns nothing after phase 06.
- [ ] The two-workspace isolation suite passes.
- [ ] Phase 09's runbook has run; the owner has signed up on raccoon.fm,
      connected Spotify and rebuilt their routines.

## What NOT To Do

- Don't fall back to a site-wide Spotify app anywhere, including tests and
  the harness.
- Don't migrate today's data; the design calls for a wipe.
- Don't rename internal identifiers (the `shuffify` package, cookie, cache
  prefixes).
- Don't build workspace switching, SoundCloud, or moving music between
  accounts.
- Don't fix #128–#131 or #141 inside these phases.

## Context
- Source skill: forge · Area: auth, models, services, worker, web · Effort: XL · Risk: High · Priority: High
