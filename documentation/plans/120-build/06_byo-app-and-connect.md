---
title: "[plan] Attach a Spotify app; connect Spotify through it with PKCE (routes, credentials, harness, staging)"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-25
tags: [enhancement, auth, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: XL.

A workspace can save its Spotify app's client ID and connect a Spotify
account through that app using Authorization Code with PKCE. There is no
client secret anywhere (F8). Every Spotify call then uses the workspace's
app, and the site-wide Spotify app settings are removed. The phase also
makes the pre-deploy script able to build a new database while a variable is
set, and stands up staging, so the first real Spotify and real Google runs
happen here.

It is XL because it holds six separate pieces of work: the PKCE changes, the
save-app and connect endpoints, the stopgaps' removal with its migration, a
rewrite of the fake Spotify (`harness/fake_spotify/app.py`, 652 lines) into a
per-app registry that enforces PKCE and rotates tokens, retiring the site
credentials (`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and `client_secret`
appear in 44 tracked files: 38 of code, config and tests, 24 of them under
`tests/`, and six documentation files, these plans among them), and the pre-deploy
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
     worker's claim-time refusal. **The refusal is a 401** whose error
     envelope carries `error_code: spotify_not_connected` and
     `retryable: false`. An open page re-reads `/api/session` only after a
     401 (`web/src/api/client.ts`, `web/src/app/services.ts`); on any other
     status it would show a request error and stay where it is.
     `retryable: false` keeps the request out of the retry that live reviews
     give a 401 (`routes/__init__.py:208-216`). The connect, save-app and
     `/api/session` routes check only the signed-in person and their
     workspace, not `require_auth`, so they keep working in every status.
   - Delete `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` from the places
     in Evidence, from `_SENTRY_SECRET_CONFIG_ATTRS`, and from every test
     that sets them; the harness identifies fake apps by the client ID it
     registers with the fake (step 6). Two places Evidence doesn't list go
     too: the startup log line that names `SPOTIFY_REDIRECT_URI`
     (`shuffify/__init__.py:915`), and the harness's fake client secret
     (`HARNESS_FAKE_CLIENT_SECRET` in `config.py`, `CLIENT_SECRET` in
     `harness/fake_spotify/app.py`).
   - **The executor's "Sign in again" message goes.**
     `live_executor_context_service.py:112-117` answers a missing stored
     refresh token with "Sign in again so Raccoon can run this for you".
     Sign-in is Google now and can't restore a Spotify token. Delete the
     message if the refusal above makes the case unreachable; otherwise
     reword it to say "Connect Spotify again".
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
3. **`PUT /api/workspace/spotify-app` `{client_id}`**, with CSRF and a rate
   limit of 10 requests a minute per person (registered under
   `_user_or_address_key`):
   - validates the format: 32 hexadecimal characters, normalised to
     lowercase, so a URL, a stray space or a typo is refused with a message.
     **The format can't tell a client ID from a client secret:** Spotify
     shows both on the app's Settings page, and the secret is understood to
     have the same 32-hex shape (confirm it on the owner's app during the
     staging checks). So the format check is not what keeps a secret out; the
     setup copy is (phase 07 step 3), and the error text doesn't claim that a
     pasted secret would be caught;
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
   `app_in_use_elsewhere`: the save checks first, and the request that loses
   a check-then-write race catches the `IntegrityError` by the constraint's
   name (`uq_spotify_connections_client_id`) and returns the same answer,
   never a 500.

   Replacing the app keeps the connection row, clears its refresh token and
   leaves `spotify_id` as it is. Under the ratified binding rule, the next
   connect must be the same Spotify account.

   There is no key-check endpoint. Connecting is the check.

   **A pasted client secret is accepted for now.** If it has the client ID's
   shape it saves, and Connect then stops on Spotify's "INVALID_CLIENT:
   Invalid client" page, which setup step 4 quotes with its fix (phase 07).
   The staging checks confirm the secret's shape on the owner's app, and a
   probe that tells the two apart is revisited only if someone reaches that
   dead end.
4. **Connect Spotify.**
   - `GET /spotify/connect` requires a signed-in person and a workspace that
     has a connection with a client ID, in any status. It refuses a request
     marked `Sec-Fetch-Site: cross-site`, makes a state and a verifier, stores
     `{state, verifier, workspace_id, client_id, credential_generation}` under
     the session key `spotify_connect` (the connection's client ID and
     generation as they are at that moment) and redirects to Spotify with
     that workspace's client ID. It is a same-tab navigation, so the browser's
     Back button returns to setup from one of Spotify's own pages. Google's
     state lives under a different key (`google_state`), so the flows can't
     interfere.
   - `GET /spotify/callback` (F13) is the redirect URL the person pastes:
     `https://raccoon.fm/spotify/callback`. It runs only for a
     signed-in person, **never calls `session.clear()`**, and takes
     `spotify_connect` out of the session first, on every outcome: the state
     and the verifier are used once. It removes nothing else from the
     session.
     - The stored and returned state must match, compared as UTF-8 bytes with
       `hmac.compare_digest` (#135, `routes/core.py:128-130`), so a non-ASCII
       `state` is an `invalid_state` notice and never a 500 (#124); and the
       workspace in it must be the session's current workspace.
     - **The connection must still be the one Connect started with.** Under
       the connection's row lock, the callback compares the `client_id` and
       `credential_generation` kept with the state against the connection's
       current ones. If the app was replaced in between (two tabs, or the
       Back button), it refuses with the `invalid_state` notice and marks
       nothing: a code issued to one app is never exchanged as another.
     - The code exchange uses `credentials_for(connection)` and the stored
       verifier.
     - The profile's `spotify_id` must equal the connection's bound
       `spotify_id` when one is set (`wrong_spotify_account`), and must not
       belong to another workspace's connection
       (`spotify_account_in_use_elsewhere`). The unique constraint
       (`uq_spotify_connections_spotify_id`) is the backstop: the request
       that loses a check-then-write race catches the `IntegrityError` by
       that constraint's name and returns the same notice, never a 500.
     - On success, in one transaction that first takes the connection row's
       `FOR NO KEY UPDATE` lock (the lock a refresh takes, phase 02), it
       stores the encrypted refresh token, sets `spotify_id`,
       `spotify_display_name` and the account's other columns, bumps
       `credential_generation`, and calls
       `SpotifyConnectionHealth.mark_ok(connection)`; then it sets the
       session's Spotify access token. The status becomes `ok` here and
       nowhere earlier. Phase 08 extends `mark_ok` to resume schedules.
     - **Where it lands.** On success it redirects to `/`, and the guard's
       routing table (phase 07 step 1) sends an `ok` workspace to the app.
       With a notice it redirects to `/setup/4`, or to `/setup/problem` when
       the connection is `broken`.
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
   - A redirect URL mismatch or a wrong client ID never reaches
     `/spotify/callback`: Spotify stops on its own page, and both pages begin
     "INVALID_CLIENT". The fake's texts are "INVALID_CLIENT: Invalid client"
     for an unknown client ID and "INVALID_CLIENT: Invalid redirect URI" for
     an unregistered redirect URI (`harness/fake_spotify/app.py:259, 262`);
     confirm Spotify's live wording on staging. There is no notice for
     either. Setup step 4, where the person presses Connect, quotes both
     pages with the fix for each, and steps 2 and 3 quote them too (phase
     07).
   - **Spotify's error return.** Cancel on Spotify's account dialog, which
     `show_dialog=true` shows every time, comes back as `error=access_denied`.
     The callback answers it with the connect flow's `access_denied` notice
     and marks nothing. Any other `error` value is also a notice and never a
     mark; which code it uses stays open in the table (its last row), and
     this phase's build settles it.
   - **The notice table, Spotify connect.** One row per code. Every notice
     lands on `/setup/4`, or on `/setup/problem` when the connection is
     `broken`, where phase 07 renders it inline with its fix. None of them
     says the person has been signed out, because they haven't. The texts
     are proposals; the final copy follows the mockups.

     | Code | When | What the person reads | The fix it links to |
     |---|---|---|---|
     | `access_denied` | Cancel on Spotify's dialog | "You cancelled before Spotify finished connecting. Nothing has changed." | Connect again |
     | `invalid_state` | The state is missing, doesn't match, names another workspace, or was made for an app that has since been replaced | "That connect attempt expired. Press Connect again." | Connect again |
     | `missing_code` | Spotify returned no code | "Spotify didn't finish connecting. Press Connect again." | Connect again |
     | `oauth_failed` | `invalid_grant` at the code exchange | "Spotify didn't finish connecting. The sign-in expired or was already used." | Connect again; nothing is marked |
     | `spotify_user_not_allowed` | "Not registered" at the profile fetch | "This Spotify account isn't allowed on your app." | Step 2's allow-list panel |
     | `spotify_app_rejected` | `invalid_client` at the exchange | "Spotify didn't recognise this app." | Step 3, or step 1 if the app no longer exists |
     | `wrong_spotify_account` | The account isn't the one the workspace is bound to | "This isn't the Spotify account this workspace started with." | Connect the original account |
     | `spotify_account_in_use_elsewhere` | The account belongs to another workspace's connection | "This Spotify account is already connected to another workspace. Have you used Raccoon with another Google account? Log out and sign in with that one." | Log out, or use a different account |
     | `app_in_use_elsewhere` | Returned by the save in step 3, not by connect | "This app is already used by another workspace." | Step 1 |
     | not settled | A Spotify `error` other than `access_denied` | a notice, never a mark | open |

     Add the codes to `shuffify/services/auth_notice.py` and, in the same
     PR, to `authNoticeCodes` (`web/src/api/contracts.ts`) and the generated
     session schema: the web rejects a session that carries a code it doesn't
     list, and `tests/contracts/test_page_contracts.py` checks the schema
     against `AUTH_NOTICE_MESSAGES`. This phase changes the web contract,
     although phase 07 is the web phase.
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
7. **Make the pre-deploy script able to build a new database, only while a
   variable is set.** This is the change production's cutover depends on, so
   staging's first deploys exercise it, with the variable and without it (it
   lands on `feat/workspaces` before staging stands up).
   - `scripts/pre-deploy.sh` runs `flask create-schema` before
     `flask db upgrade`, with the same
     `SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1` prefix, **only while
     `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA=1` is set on the service.** Without
     it, pre-deploy runs only `flask db upgrade`, as today, so a database with
     no revision still fails the deploy (`migrations/env.py:54-66`). The
     variable is set only on staging's api (step 8) and on production's api
     from phase 09 step 3 to step 11, where it goes.
   - `flask create-schema` (`shuffify/cli.py:15-53`) builds the schema and
     stamps head **only when the database has no Alembic revision and no
     tables at all**. Today it refuses only when the models' tables exist, so
     a wrong `DATABASE_URL` pointing at some unrelated database would be
     built on quietly. Tighten it: no revision and any table at all is
     refused loudly, naming the tables. A database that has a revision is
     left to the upgrade, as today. It logs which revision it stamped and the
     Neon endpoint id it ran on, taken from `DATABASE_URL`'s host (the id
     only, never the URL), so phase 09 step 10 can read both from the
     pre-deploy log.
   - Test the four cases: empty builds and stamps; a revision is left alone;
     no revision with the models' tables is refused; no revision with
     unrelated tables is refused.
   - **This keeps a rule the repo pinned on purpose.** Since the split
     runtime (05d4262, 2026-10-15), pre-deploy has run only `flask db
     upgrade`, `create-schema` has been an explicit one-time bootstrap, and a
     database with no revision has failed the deploy
     (`migrations/env.py:54-66`). The cutover can't work that way: production
     is wiped and rebuilt by its deploy. **Decision: pre-deploy builds an
     empty database only while the variable is set.** The variable is the
     explicit bootstrap, set for the two databases that need one, and the
     rule holds everywhere else, production after phase 09 step 11 included.
     While it is set, an empty database behind a wrong `DATABASE_URL` would be
     built and booted instead of failing the deploy, and production's
     `DATABASE_URL` host has already differed from what the guides described.
     The refusal of any database that has tables and no revision, and the
     Neon-identity check before a first deploy (overview Staging, cutover
     step 6), cover that window.
   - **The tests that pin the rule stay as they are,** because they run with
     the variable unset:
     `tests/test_db_schema_guard.py::TestPredeploy::test_runs_only_the_explicit_upgrade`
     (L337), `::test_missing_schema_is_never_silently_bootstrapped` (L354) and
     `tests/test_runtime_launch.py::test_predeploy_disables_scheduler_and_sets_only_the_migration_process_marker`
     (L40). **Add their counterparts with the variable set:** the stub
     `flask` sees `false|1|create-schema` and then `false|1|db upgrade`, in
     that order; and a `create-schema` that fails blocks the deploy,
     `db upgrade` doesn't run and the exit code passes through.
   - **Document the variable next to the rule,** in this step: README.md's
     "Split runtime" paragraph (L119) and the last paragraph of
     `.railway/README.md` (L21) keep what they say, and each gains a sentence:
     with `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA=1` on the api, pre-deploy runs
     `flask create-schema` first, which builds a database that has no
     revision and no tables, leaves one that has a revision to the upgrade,
     and refuses one that has tables and no revision; the variable is set
     only while a new database is being built. (The "no staging environment"
     sentences are the ones left for phase 09.)
8. **Stand up staging** (the owner, with the overview's Staging section), in
   this order, because `railway config apply` creates the services and that
   is the first deploy:
   1. the staging database, holding no production data, on its pooled host
      (as production's api and worker use; re-check production's host type
      now);
   2. the Railway `staging` environment, created empty, with every shared
      variable set by hand and read back;
   3. `staging.raccoon.fm` on Vercel with its DNS record, the Vercel branch
      variable, and **`staging.raccoon.fm` added to the project's Deployment
      Protection Exceptions** (without it the domain sits behind a Vercel
      login); confirm in a private window;
   4. the Spotify redirect URI on the owner's existing app (the
      `/spotify/callback` URL), and a Google client for staging in its own
      Google Cloud project (Testing mode is fine there);
   5. confirm the link with `railway status`, check that the plan proposes
      only creations in the staging environment, and apply. The apply
      creates the api and the worker, so their first deploys run without
      staging's own settings: step 7's pre-deploy refuses the empty database
      and the api's deploy fails, as the rule says, and the worker refuses to
      start on a database with no schema (phase 09, Evidence), so its
      scheduler can't run. A worker restart that outlasts the schema build
      finds no schedules in the new database, so it has nothing to fire.
      Right after the apply, and before the worker's redeploy, set
      `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA=1` on staging's api and
      `SCHEDULER_ENABLED=false` on its worker, so the scheduler stays off
      except during a check (overview, Staging), and deploy the staged
      changes: pre-deploy builds the schema and stamps head. Redeploy the
      worker once the api is at head. A later apply to staging can undo
      either setting, so read both back after any apply.

   Then run the checks below. Until phase 07's pages are on staging there is
   no page that saves a client ID, and the save is a `PUT` with CSRF that is
   accepted only from `PUBLIC_ORIGIN`. Do it from the browser console on
   `https://staging.raccoon.fm`, signed in: read `csrf_token` from
   `GET /api/session`, send the `PUT` to `/api/workspace/spotify-app` with
   `{client_id}` and that token in the header the web client sends
   (`web/src/api/client.ts`), then open `/spotify/connect` in the same tab.
   A `fetch()` from that console carries the cookie and the right Origin.

   The two lines about staging in `documentation/guides/production.md` and
   `.railway/README.md` ("no staging environment") are superseded for as long
   as staging exists; the overview's Staging section is the guidance, and
   phase 09 tears it down.

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
  - the 11th save in a minute by one person returns 429, and the test that
    every sign-in, connect and save-app view has a registered limit (phase
    05) covers this view;
  - a save that loses a race on the client ID returns `app_in_use_elsewhere`,
    and a connect that loses one on the Spotify account returns
    `spotify_account_in_use_elsewhere`; neither is a 500;
  - `spotify_connect` is gone after every callback outcome (success, a state
    mismatch, Spotify's error return, a failed exchange), and replaying the
    callback gets the invalid-state notice;
  - Cancel on the fake Spotify's dialog shows the connect `access_denied`
    notice and marks nothing;
  - a callback for a connection whose app was replaced after Connect started
    gets the invalid-state notice and marks nothing, for a connection that
    has connected before as well as one that hasn't;
  - a request through `require_auth` with an `unchecked` connection gets a
    401 with `error_code: spotify_not_connected` and `retryable: false`;
  - every notice code in step 4's table is in `authNoticeCodes` and the
    session schema (the contract test), and no connect notice says the
    person has been signed out.
- **Migration test:** a database with two stopgap connections sharing one
  client ID upgrades through the unique-constraint migration, which leaves
  no connections.
- **Pre-deploy tests:** the four create-schema cases in step 7, the
  pre-deploy tests with the variable set, and the three existing ones it
  names, unchanged, with it unset.
- **Fake Spotify tests:** a missing, wrong or reused verifier is refused; a
  Basic header is refused; a deleted app answers `invalid_client`; a rotated
  refresh token invalidates the old one.
- **Harness e2e:** the full first-ten-minutes flow, from Google sign-in to
  the person's own playlists.
- **Isolation:** two workspaces with two fake apps; each one's Spotify calls
  use its own client ID (the fake records which client called).
- **On staging, against real Spotify and Google** (the owner):
  - the first deploy onto the empty database failed at pre-deploy, and the
    deploy after `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA` was set built the schema
    and stamped head (the api log says which revision, and which Neon
    endpoint);
  - a Vercel preview other than `feat/workspaces` still behaves as before,
    and `https://staging.raccoon.fm` reaches the staging API, not production's
    (overview, Staging);
  - sign in with Google;
  - **the per-address limit, as phase 05's test asserts it in the harness:**
    the 11th request in a minute to `/login` from one network is refused, and
    a request from a different network in that same minute is not. If staging
    disagrees with the harness, fix it, with a test, before phase 09 step 2;
  - save the existing app's client ID (by step 8's console recipe until
    phase 07's pages are on staging), connect and confirm the playlists load,
    with Spotify's account dialog shown;
  - let a token refresh and confirm the stored token rotated, through the
    pooled host (phase 02's script already showed how real Spotify rotates;
    this confirms the row-locked refresher against it, on the topology
    production has);
  - run a routine once, by hand, on a copy of a playlist: staging shares the
    owner's Spotify account and playlists with production (overview,
    Staging);
  - write down, for phase 07's copy: the live wording of Spotify's two
    "INVALID_CLIENT" pages, and whether a client secret has the same shape as
    a client ID (the app's Settings page shows both).

## Verification Checklist
- [ ] `git grep -n "SPOTIFY_CLIENT_ID\|SPOTIFY_CLIENT_SECRET\|SPOTIFY_REDIRECT_URI" -- shuffify config.py scripts harness .railway .github .env.example README.md tests`
      returns nothing.
- [ ] `git grep -inE "client.?secret" -- shuffify harness config.py scripts tests .github .railway .env.example`
      lists only the expected matches: Google's client secret setting and its
      tests, and the two redaction keys that stay (`shuffify/__init__.py:448`
      and `shuffify/services/operations/repository.py:38`).
- [ ] `GET /callback` returns 404 on the harness, and the Vercel rule and
      preview proxy tests no longer list it (`/spotify/callback` stays).
- [ ] `git grep -n "from_env\|from_flask_config" -- shuffify` returns nothing
      (step 1 deletes `from_flask_config` from `shuffify/spotify/credentials.py`).
- [ ] `pytest tests/test_runtime_launch.py tests/test_db_schema_guard.py`
      passes with the four create-schema cases, the pre-deploy tests with the
      variable set, and the three existing pre-deploy tests unchanged, and
      `git grep -n "SHUFFIFY_PREDEPLOY_CREATE_SCHEMA" -- scripts/pre-deploy.sh README.md .railway/README.md`
      lists all three files.
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
- Don't let pre-deploy build a database unless
  `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA` is set.
- Don't make `PUT /api/workspace/spotify-app` change anything when the client
  ID is unchanged.

## Context
- Source skill: forge · Area: routes, credentials, harness/fake_spotify, ops · Effort: XL · Risk: High · Priority: High
