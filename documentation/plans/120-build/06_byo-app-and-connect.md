---
title: "[plan] Attach a Spotify app; connect Spotify through it with PKCE — routes, credentials, harness, staging"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, auth, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: XL.

A workspace can save its Spotify app's client ID and connect a Spotify
account through that app using Authorization Code with PKCE. There is no
client secret anywhere (F8). Every Spotify call then uses the workspace's
app, and the site-wide Spotify app settings are removed. The phase also
makes the pre-deploy script able to build a new database and stands up
staging, so the first real Spotify and real Google runs happen here.

It is XL because it holds six separate pieces of work: the PKCE changes, the
save-app and connect endpoints, the stopgaps' removal with its migration, a
rewrite of the fake Spotify (`harness/fake_spotify/app.py`, 652 lines) into a
per-app registry that enforces PKCE and rotates tokens, retiring the site
credentials (`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and `client_secret`
appear in 44 tracked files, 24 of them under `tests/`), and the pre-deploy
script's database build (step 7), plus the owner's staging setup. It is one
phase and still lands as several PRs where the builder finds a clean seam (for
example the fake Spotify first).

## Evidence

- After 02, `credentials_for` (`shuffify/spotify/credentials.py`) is the
  only source of credentials, `AuthService` takes them as an argument, and
  the database is the only home of the refresh token, refreshed under a row
  lock on the connection (re-pointed there by phase 04).
- After 04 and 05, the Spotify account lives on `SpotifyConnection` (the
  `users` columns are gone), phase 04's sign-in stopgap (the old `/callback`)
  is unreachable from any start (`/login` is Google), and phase 05's stopgap
  `/spotify/connect` and `/spotify/callback` attach the site app's connection
  to the signed-in person's workspace.
- Today the token calls use Basic authorization with the secret
  (`shuffify/spotify/auth.py:236` exchange, `:297` refresh, `:382` revoke).
  The authorize URL (`auth.py:179-204`) has no PKCE and no `show_dialog`.
- The fake Spotify (`harness/fake_spotify/app.py:43-44, 256-312`) has one
  hard-coded client and no PKCE. Its `/authorize` checks `redirect_uri` and
  offers seeded users (`seed.json:4-11`); a redirect mismatch stops on its
  own error page (`app.py:261-262`), as it does on Spotify.
- The site app's ID and secret settings are read in `config.py` (L52, L165-170,
  L244-250, L372), `.railway/railway.ts`, `.env.example`, `README.md`,
  `.github/workflows/ci.yml:73-75, 143-145`, `live-integration.yml`,
  `scripts/harness.sh:213-214, 568` and many tests.
- `.railway/README.md:9` requires a nonproduction environment to have its own
  Neon branch and never the production URL.

## Implementation Plan

### Dependencies
02, 05. It does not depend on 01b: the redirect URL comes from
`/api/session`, the harness uses loopback, and nothing in the code needs the
domain move. Only step 8 (staging) needs raccoon.fm to be bought, because
staging lives at `staging.raccoon.fm`; if the owner hasn't bought it yet, the
code, including step 7's pre-deploy change, lands without step 8 and the
staging checks wait, and phase 09's gate requires them. Phase 09 is the only
phase that depends on 01b.

The owner, for staging: access to Railway, Neon, Vercel, Google Cloud, the
Spotify dashboard and raccoon.fm's DNS.

### Blocks
07 (the setup page calls these endpoints), 08, 09.

### Steps

1. **`credentials_for(connection)`** returns `SpotifyCredentials(client_id,
   redirect_uri)`: the connection's client ID and the site's
   redirect URL. `SpotifyCredentials` loses `client_secret`.
   - **Delete `SpotifyCredentials.from_flask_config`** (and the site
     `client_secret` field): the credentials are built only from the
     connection and the site redirect URL, `PUBLIC_ORIGIN` plus
     `/spotify/callback`, so nothing reads the site app. The
     `SPOTIFY_REDIRECT_URI` setting is retired with the ID and secret.
   - It raises `SpotifyAppUnavailable` when there's no connection or no
     client ID. It does not look at `status`, because Connect is the fix for
     a broken app and must work while one is broken.
   - Code that calls Spotify on a person's behalf refuses a connection that
     isn't `ok`: `require_auth` returns `spotify_not_connected` for
     `unchecked`, and phase 08 adds the `broken` case (its step 4) and the
     worker's claim-time refusal. The connect, save-app and `/api/session`
     routes check only the signed-in person and their workspace, not
     `require_auth`, so they keep working in every status.
   - Delete `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` from the places
     in Evidence, from `_SENTRY_SECRET_CONFIG_ATTRS`, and from every test
     that sets them; the harness identifies fake apps by the client ID it
     registers with the fake (step 6).
2. **Add PKCE to `shuffify/spotify/auth.py`.**
   - A helper makes a code verifier (`secrets.token_urlsafe(64)`) and its
     S256 challenge.
   - `get_auth_url(state, code_challenge)` adds
     `code_challenge_method=S256`, `code_challenge` and `show_dialog=true`.
     The last stops a browser already signed in to a different Spotify
     account from binding the wrong one for good.
   - `exchange_code(code, code_verifier)` sends `client_id` and
     `code_verifier` in the body, with no authorization header (L236).
   - `refresh_token` sends `client_id` in the body, no authorization header
     (L297). A rotated refresh token in the response is stored by the
     phase 02 refresher.
   - `revoke_token` drops the Basic header and sends `client_id` (L382).
     Spotify ignores revocation, as today.
3. **`PUT /api/workspace/spotify-app` `{client_id}`**, with CSRF and a
   per-person rate limit:
   - validates the format: 32 hexadecimal characters, normalised to
     lowercase, so a pasted secret, URL or stray space is refused with a
     message;
   - **saving the client ID the connection already has is a no-op** that
     returns the current status: a double submit, or revisiting step 3, must
     not pause schedules or force a reconnect. The comparison is on the
     normalised value;
   - otherwise creates or replaces the workspace's connection, sets
     `status = unchecked`, clears `broken_reason` and the refresh token, and
     bumps `credential_generation`;
   - returns `{client_id, status}`.

   A migration in this phase adds the unique constraint on
   `spotify_connections.client_id`, in the same change that deletes the
   stopgap (step 5). **The migration first deletes every
   `spotify_connections` row**: until this phase only the stopgap creates
   them, and it gives each one the site app's client ID, so two of them would
   fail the constraint. Development and staging databases therefore lose
   their connections and connect again; production is built fresh by the
   cutover. A `client_id` already used by another workspace returns
   `app_in_use_elsewhere`.

   Replacing the app keeps the connection row, clears its refresh token and
   leaves `spotify_id` as it is. Under the ratified binding rule, the next
   connect must be the same Spotify account.

   There is no key-check endpoint. Connecting is the check.
4. **Connect Spotify.**
   - `GET /spotify/connect` requires a signed-in person and a workspace that
     has a connection with a client ID, in any status. It refuses a request
     marked `Sec-Fetch-Site: cross-site`, makes a state and a verifier, stores
     `{state, verifier, workspace_id}` under the session key `spotify_connect`
     and redirects to Spotify with that workspace's client ID. Google's state
     lives under a different key (`google_state`), so the flows can't
     interfere.
   - `GET /spotify/callback` (F13) is the redirect URL the person pastes:
     `https://raccoon.fm/spotify/callback`. It runs only for a
     signed-in person, **never calls `session.clear()`**, and removes only
     `spotify_connect` from the session.
     - The stored and returned state must match, compared as UTF-8 bytes with
       `hmac.compare_digest` (#135, `routes/core.py:128-130`), so a non-ASCII
       `state` is an `invalid_state` notice and never a 500 (#124); and the
       workspace in it must be the session's current workspace.
     - The code exchange uses `credentials_for(connection)` and the stored
       verifier.
     - The profile's `spotify_id` must equal the connection's bound
       `spotify_id` when one is set (`wrong_spotify_account`), and must not
       belong to another workspace's connection
       (`spotify_account_in_use_elsewhere`; the unique constraint is the
       backstop).
     - On success, in one transaction that first takes the connection row's
       `FOR NO KEY UPDATE` lock (the lock a refresh takes, phase 02), it
       stores the encrypted refresh token, sets `spotify_id` and the profile,
       bumps `credential_generation`, and calls
       `SpotifyConnectionHealth.mark_ok(connection)`; then it sets the
       session's Spotify access token. The status becomes `ok` here and
       nowhere earlier. Phase 08 extends `mark_ok` to resume schedules.
     - **The callback catches every typed failure itself,** so none of them
       reaches a global handler, and it builds its own clients without a
       failure hook: the exchange uses the bare auth manager and the profile
       fetch a client with no hook, so nothing marks the connection from
       inside them (phase 08 gives the hook to the clients the web and worker
       builders make for ordinary calls). It sorts failures by what they say:
       - **`invalid_grant` at the code exchange** (`SpotifyGrantRevoked`: a
         reused or expired code, or a verifier lost with the session) is a
         notice, `oauth_failed`, in every status. It never marks anything
         broken, and the status, the refresh token and the
         `credential_generation` stay as they were. Only a failed refresh
         means "access removed" (phase 08).
       - Spotify's "not registered" error at the profile fetch becomes
         `spotify_user_not_allowed`, and `invalid_client` at the exchange
         becomes `spotify_app_rejected`. In this phase both are notices only.
         Phase 08 decides when they also mark the connection broken: only for
         a connection that has connected before (it has a bound
         `spotify_id`), never for a first connect.
   - A redirect URL mismatch or a wrong client ID never reaches `/spotify/callback`:
     Spotify stops on its own page. There is no notice for it; step 2 and 3
     of the setup copy (phase 07) explain what that page looks like and to
     come back and fix it.
   - Add the notice codes to `shuffify/services/auth_notice.py`.
5. **Delete the old Spotify sign-in and the phase 04 and 05 stopgaps.**
   Remove the old `/callback` route and `auth.callback` (with its #139 limit
   registration), the Spotify sign-in code behind `/login`, and `/callback`
   from the Vercel forwarding rule (`web/scripts/build-vercel-output.mjs`)
   and the harness preview proxy (`tests/frontend_preview.py`). The stopgaps
   are the site app's client ID copied into a connection. Their migration is
   the one that deletes the stopgap rows and then adds the unique constraint
   in step 3.
6. **Make the fake Spotify enforce PKCE.** In `harness/fake_spotify/`:
   - a per-app registry (`/_fake/apps`): client ID, redirect URIs (the
     harness's `/spotify/callback`) and an allow-list, with no secret;
   - `/authorize` requires `code_challenge` with `code_challenge_method=S256`
     and a registered client ID and redirect URI (the existing error page
     otherwise), and records the challenge with the code;
   - the token endpoint requires `client_id` in the body for both grants,
     verifies the S256 challenge against `code_verifier`, and rejects a
     missing or incorrect verifier with `invalid_grant`;
   - it rejects a request that carries an authorization header, so a
     regression to Basic auth fails a test;
   - refresh rotates the refresh token and invalidates the previous one;
   - a deleted-app mode makes the app's client ID answer `invalid_client`;
   - there is no `client_credentials` grant.

   `HarnessConfig.assert_safe` now checks that every connection's client ID
   is registered with the fake. `check_login` and `App._log_in` become: fake
   Google, save the fake app's client ID, connect as `fake_user`.
7. **Make the pre-deploy script able to build a new database.** This is
   the change production's cutover depends on, so staging's first deploy
   exercises it (it lands on `feat/workspaces` before staging stands up).
   - `scripts/pre-deploy.sh` runs `flask create-schema` before
     `flask db upgrade`, with the same
     `SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1` prefix.
   - `flask create-schema` (`shuffify/cli.py:15-53`) builds the schema and
     stamps head **only when the database has no Alembic revision and no
     tables at all**. Today it refuses only when the models' tables exist, so
     a wrong `DATABASE_URL` pointing at some unrelated database would be
     built on quietly. Tighten it: no revision and any table at all is
     refused loudly, naming the tables. A database that has a revision is
     left to the upgrade, as today. It logs which revision it stamped.
   - Test the four cases: empty builds and stamps; a revision is left alone;
     no revision with the models' tables is refused; no revision with
     unrelated tables is refused.
   - **This reverses a rule the repo pinned on purpose.** Since the split
     runtime (05d4262, 2026-10-15), pre-deploy has run only `flask db
     upgrade`, `create-schema` has been an explicit one-time bootstrap, and a
     database with no revision has failed the deploy
     (`migrations/env.py:54-66`). The cutover can't work that way: production
     is wiped and rebuilt by its deploy. **Decision: pre-deploy keeps
     `create-schema` after the cutover.** The tightened command does nothing
     on a database that has a revision, so on production afterwards it is
     inert, and putting the old rule back would take another main PR and
     deploy for a guard whose remaining value is small. What is lost: an
     empty database behind a wrong `DATABASE_URL` is now built and booted
     instead of failing the deploy, and production's `DATABASE_URL` host has
     already differed from what the guides described. What remains is the
     refusal of any database that has tables and no revision, and the
     Neon-identity check before a first deploy (overview Staging, cutover
     step 6).
   - **Rewrite the tests that assert the old rule:**
     - `tests/test_db_schema_guard.py::TestPredeploy::test_runs_only_the_explicit_upgrade`
       (L337) expects the stub `flask` to see only `db upgrade`. It now
       expects `create-schema`, then `db upgrade`, in that order;
     - `::test_missing_schema_is_never_silently_bootstrapped` (L354) uses a
       stub that exits 91 if `create-schema` is called. It becomes: a
       `create-schema` that fails blocks the deploy, `db upgrade` doesn't run
       and the exit code passes through;
     - `tests/test_runtime_launch.py::test_predeploy_disables_scheduler_and_sets_only_the_migration_process_marker`
       (L40) expects exactly `false|1|db upgrade`. It now expects
       `false|1|create-schema` and then `false|1|db upgrade`.
   - **Rewrite the docs that state the old rule,** in this step and not at
     cutover, because they contradict the script from the day it merges to the
     branch: README.md's "Split runtime" paragraph (L119: an empty database
     "requires an explicit one-time `flask create-schema` bootstrap") and the
     last paragraph of `.railway/README.md` (L21: "upgrading never silently
     stamps an existing database"). Both now say that pre-deploy builds a
     database that has no revision and no tables, leaves one that has a
     revision to the upgrade, and refuses one that has tables and no
     revision. (The "no staging environment" sentences are the ones left for
     phase 09.)
8. **Stand up staging** (the owner, with the overview's Staging section):
   the Railway environment, the emptied Neon branch on its pooled host (as
   production's api and worker use), the Vercel branch variable and
   `staging.raccoon.fm`, the Spotify redirect URI
   on the owner's existing app (the `/spotify/callback` URL), and a Google
   client for staging (Testing mode is fine there). Deploy
   `feat/workspaces` to it, so the first deploy onto the emptied branch runs
   step 7's pre-deploy, and run the checks below. The two lines about staging
   in `documentation/guides/production.md` and `.railway/README.md` ("no
   staging environment") are superseded for as long as staging exists; the
   overview's Staging section is the guidance, and phase 09 tears it down.

## Test Plan
- **Route tests:**
  - saving an ID rejects a non-hex or wrong-length value, and normalises
    case;
  - saving the connection's current client ID again, in either case, changes
    nothing: the status, the refresh token and the generation stay;
  - saving a different ID sets `unchecked`, drops the refresh token and bumps
    the generation;
  - a duplicate client ID is refused;
  - connect is refused without a saved app, and works while the connection is
    `broken`;
  - the authorize URL carries `code_challenge_method=S256`, a challenge and
    `show_dialog=true`, and never a secret;
  - the callback is refused for a mismatched state or workspace, a wrong
    bound account, an account already used elsewhere and a user not on the
    allow-list;
  - the callback leaves the Google session in place and removes only
    `spotify_connect`;
  - **a non-ASCII `state`** gets the invalid-state notice and not a 500
    (modelled on `test_non_ascii_state_gets_the_invalid_state_notice` in
    `tests/routes/test_core_routes.py`);
  - **`invalid_grant` at the code exchange** (a reused code, and a wrong
    verifier) shows the `oauth_failed` notice and leaves the status, the
    refresh token and the generation as they were, for an `unchecked`, an `ok`
    and a `broken` connection;
  - replacing the app keeps the workspace's data and requires reconnecting;
  - status is `unchecked` after saving and `ok` only after a successful
    connect, which also bumps the generation;
  - the callback and a refresh don't interleave: with the row lock held by a
    refresh, the callback waits for it and then stores its token.
- **Migration test:** a database with two stopgap connections sharing one
  client ID upgrades through the unique-constraint migration, which leaves
  no connections.
- **Pre-deploy tests:** the four create-schema cases in step 7, and the three
  rewritten pre-deploy tests it names.
- **Fake Spotify tests:** a missing, wrong or reused verifier is refused; a
  Basic header is refused; a deleted app answers `invalid_client`; a rotated
  refresh token invalidates the old one.
- **Harness e2e:** the full first-ten-minutes flow, from Google sign-in to
  the person's own playlists.
- **Isolation:** two workspaces with two fake apps; each one's Spotify calls
  use its own client ID (the fake records which client called).
- **On staging, against real Spotify and Google** (the owner):
  - the first deploy onto the emptied branch built the schema through
    pre-deploy and stamped head (the api log says which revision);
  - a Vercel preview other than `feat/workspaces` still behaves as before,
    and `https://staging.raccoon.fm` reaches the staging API, not production's
    (overview, Staging);
  - sign in with Google;
  - save the existing app's client ID, connect and confirm the playlists
    load, with Spotify's account dialog shown;
  - let a token refresh and confirm the stored token rotated, through the
    pooled host (phase 02's script already showed how real Spotify rotates;
    this confirms the row-locked refresher against it, on the topology
    production has);
  - run a routine once.

## Verification Checklist
- [ ] `git grep -n "SPOTIFY_CLIENT_ID\|SPOTIFY_CLIENT_SECRET\|SPOTIFY_REDIRECT_URI" -- shuffify config.py scripts harness .railway .github .env.example README.md tests`
      returns nothing.
- [ ] `git grep -n "client_secret" -- shuffify/spotify harness/fake_spotify`
      returns nothing.
- [ ] `GET /callback` returns 404 on the harness, and the Vercel rule and
      preview proxy tests no longer list it (`/spotify/callback` stays).
- [ ] `git grep -n "from_env\|from_flask_config" -- shuffify` returns nothing
      (step 1 deletes `from_flask_config` from `shuffify/spotify/credentials.py`).
- [ ] `pytest tests/test_runtime_launch.py tests/test_db_schema_guard.py`
      passes with the four create-schema cases and the three rewritten
      pre-deploy tests, and
      `git grep -nE "never silently|explicit,? one-time" -- README.md .railway/README.md`
      returns nothing.
- [ ] The harness e2e first-run test passes.
- [ ] The staging checks above pass.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't ask for, accept, store or send a client secret, and don't send a
  Basic authorization header to Spotify.
- Don't refuse connect because the app is `broken` or `unchecked`; it is the
  fix.
- Don't call `session.clear()` in `/spotify/callback`.
- Don't keep the old `/callback` route or its forwarding.
- Don't add a notice for a redirect URL mismatch; it never arrives.
- Don't fall back to any default client ID, including in the harness.
- Don't point staging at the production database, Redis or URL.
- Don't let `create-schema` build on a database that has any table and no
  revision.
- Don't make `PUT /api/workspace/spotify-app` change anything when the client
  ID is unchanged.

## Context
- Source skill: forge · Area: routes, credentials, harness/fake_spotify, ops · Effort: XL · Risk: High · Priority: High
