# Workspaces and bring-your-own Spotify app

Issue: #120 · Epic: #119 · Status: design approved, amended 2026-11-10 (PKCE, staging, early domain move, no naming step, a dedicated Spotify callback path, a published Google consent screen) · Build plan: [`120-build/`](120-build/00_overview.md)

The product is being renamed Raccoon, at raccoon.fm (#122). This doc uses the
new name. The rename of what users see ships before this project's code does,
and the domain moves to raccoon.fm on main before the project's code does,
because the redirect URL every person pastes into their Spotify app is
`https://raccoon.fm/spotify/callback`. The code's internal name stays
`shuffify`.

## Why

Spotify keeps every developer app in development mode: the app's owner plus
five allow-listed accounts, and no route to extended quota. One shared
Raccoon app can therefore never serve the public. If each person brings their
own Spotify app, Raccoon can serve anyone, because each app only ever needs to
serve its own owner.

This project adds that layer and nothing else. Once a person has finished
setup, Raccoon behaves exactly as it does today (Workshop, routines,
schedules, restore, the durable queue), except that every Spotify call for
their workspace goes through their own app.

## What the person experiences

1. They sign in to Raccoon with Google. Raccoon creates their workspace,
   called "My workspace".
2. A guided setup walks them through creating a Spotify developer app (a
   Spotify Premium account is required to own one), adding Raccoon's
   redirect URL and their own Spotify account to it, and pasting the app's
   client ID into the workspace. There is no secret to copy.
3. They choose Connect Spotify, sign in on Spotify through their own app, and
   land on their own playlists. Connecting is also the check: Raccoon checks
   the ID's format when it's saved, and Spotify itself shows an error page if
   the ID is wrong.

From then on the product is today's product.

Mockups in the production design system:

- [`120-mockups/setup-steps.html`](120-mockups/setup-steps.html): all four
  setup steps, the saved client ID and the error pages people can hit on
  Spotify's side, and the finished state.
- [`120-mockups/uh-oh.html`](120-mockups/uh-oh.html): the guided setup step
  beside the "uh oh" page for a broken app.

Open them from a checkout so the fonts load from `web/public/fonts`. The
mockups set the look, the order of the steps and the states to cover. Copy and
screenshots are drafts.

## Goals

- Anyone with a Google account and a Spotify Premium account can set up a
  workspace without help, in about ten minutes.
- Every Spotify call for a workspace uses that workspace's app: web requests,
  queued operations, the worker and the scheduler.
- Raccoon never asks for, receives or stores an app's client secret.
  Refresh tokens are protected as carefully as they are today.
- When an app stops working, the person is told what happened and how to fix
  it, and nothing runs against their playlists in the meantime.

## Non-goals

- Sign-in methods other than Google (email, magic link, others). Later, if
  people ask for them.
- Several people in one workspace, or inviting the other accounts on a
  Spotify app's allow-list. The epic leaves shared workspaces out of scope.
- The MCP server (#121), the product's name (#122), and launch safeguards such
  as abuse limits, account deletion and legal pages (#123).
- Removing a Spotify app from a workspace, or deleting a workspace. Both
  belong to account deletion in #123. Until then a person swaps to another
  app (see Decisions) and the owner fixes anything else by hand.
- Launch safeguards for strangers: abuse limits, account deletion and legal
  pages (#123). Sign-up has no gate in #120 and the production Google consent
  screen is published at cutover, so anyone with a Google account can sign
  up. The owner is the only user at first. Until #123 ships, the only
  sign-up safeguard is #139's per-address limit of 10 requests a minute on
  the sign-in views. Google may require brand verification for the consent
  screen; the basic `openid`, `email` and `profile` scopes need no
  sensitive-scope review.
- Migrating today's accounts (see Cutover).
- Creating and switching between several workspaces in the product (the data
  model supports it; see Decisions). Naming a workspace comes back with
  switching.
- Moving music between Spotify accounts. That would mean raiding or rotating
  between playlists in two workspaces owned by the same person. It's a later
  feature that this model makes possible, not part of this project.

## Anti-goals

These would make the project a failure even if it technically works:

- Any Spotify call for a workspace that falls back to a site-wide app. If a
  workspace's app can't be resolved, the call fails. It never quietly uses
  someone else's app.
- A client secret asked for, accepted, stored or logged anywhere.
- A Spotify refresh token that can be read back by the person, the API, the
  logs, error reports or the activity log.
- A scheduled run that keeps firing, or keeps failing noisily, against an app
  Spotify has rejected.
- One workspace able to see or use another workspace's app, Spotify sign-in or
  data.

## Decisions

### The owner's decisions

- **Google sign-in only, at first.**
- **The model: person → workspaces → service connections.**
  - A person, identified by their Google login, is the top-level entity and
    can own several workspaces.
  - The workspace is the source of truth that all product data belongs to:
    routines, schedules, pairings, snapshots and history.
  - A workspace connects to services. Spotify is the only service today, and
    the model is shaped so others such as SoundCloud fit later.
  - A workspace has at most one connection per service. For Spotify that is
    one app and the one Spotify account connected through it.
  - The database enforces each of these rules with constraints, not just
    application code.
  - At cutover the product shows one workspace per person. Creating and
    switching between several comes later; the data model is ready for it.
- **Guided setup** explains every step on Spotify's side: creating the app,
  where the redirect URL goes, the allow-list, and where to find the client
  ID. It says up front that Spotify requires Premium to own an app.
- **No migration.** At cutover the production database is wiped and everyone
  signs up fresh, the owner included. Rebuilding the owner's schedules and
  routines through the new flow is part of their acceptance testing.
- **A broken app pauses the workspace's scheduled runs** and sends the person
  to an "uh oh" page with the fix that matches what Spotify reported: start
  setup again with a new app, reconnect, or add their account to the
  allow-list and reconnect.
- **Paused schedules resume on their own once the app works again.** They run
  at their next scheduled time. Runs missed while paused are not made up.
- **No sign-up gate.** Sign-up is open when this ships and the Google consent
  screen is published, but the owner is the only user. The launch safeguards
  wait for #123.

### Owner's decisions, ratified 2026-11-10

These amend the decisions above.

1. **Accept Spotify's February 2026 development-mode rules.** An app owner
   needs Premium. One development-mode client ID per developer. Five users
   per client ID. Spotify also says development mode shouldn't be the
   foundation of a business. The owner accepts this as a policy risk of the
   whole bring-your-own-app model. Setup step 1 says Premium is required.
   "Use a different app" means deleting the old app in Spotify's dashboard
   first (one client ID per developer), then creating a new one.
2. **PKCE with the client ID only; no client secret anywhere.** This
   replaces "app secrets are stored encrypted" and the separate secret key.
   - Raccoon authorizes with `code_challenge_method=S256`. The code verifier
     is kept in the session next to the connect state.
   - The code exchange and every refresh send `client_id` in the request
     body, with no Basic authorization header.
   - People paste a client ID and nothing else. There is nothing to encrypt,
     rotate, scrub or leak, and no "secret rejected" case.
   - The refresh token stays encrypted with the existing `TokenService`
     (`TOKEN_ENCRYPTION_KEY`).
   - A keyring and a rotate command for secrets would repeat what PR #44
     (d30081a) removed for the token key; PKCE avoids bringing them back.
3. **Staging runs `feat/workspaces` against real Spotify and Google from
   phase 06 onward**, so the first real-service run isn't the wipe. See the
   build plan's Staging section.
4. **The domain moves early, on main.** raccoon.fm serves the current site,
   `PUBLIC_ORIGIN` and `SPOTIFY_REDIRECT_URI` switch to it, the new
   `https://raccoon.fm/spotify/callback` redirect URL is added to the current
   Spotify app, and shuffify.app redirects to raccoon.fm permanently, in a
   small phase right after the rename. Cutover day is then only the wipe and
   the merge.
5. **No "Name your workspace" step.** The workspace is created named "My
   workspace". Naming returns when a person can have several workspaces.

### Design calls, ratified by the owner

- **A Spotify app belongs to at most one workspace.** The client ID is unique
  across workspaces.
- **One redirect URL for every workspace.** Each person adds the same
  Raccoon callback URL to their own app: `https://raccoon.fm/spotify/callback`,
  a dedicated path that only the connect step uses. The
  OAuth `state` and PKCE verifier, both random and kept in the session, bind
  each round trip to the signed-in person and the workspace they are
  connecting, and therefore to that workspace's app. Per-workspace callback
  URLs were considered and not chosen: they add routing and make the setup
  copy longer, for no security gain over a bound state.
- **Checking the app** is two things. Raccoon checks the client ID's format
  when it's saved (Spotify client IDs are 32 hex characters). Connecting is
  the real check: it proves the ID, the redirect URL and the allow-list
  together. A wrong client ID lands on Spotify's own "INVALID_CLIENT" page
  and never comes back to Raccoon, so the setup copy says what that page looks
  like and to return and fix the ID. A connection's status is `unchecked`
  until its first successful connect, then `ok`.
- **A workspace is bound to the first Spotify account it connects.**
  Reconnecting, including through a replacement app, must use the same
  Spotify account. A different account is refused with an explanation.
  Otherwise saved routines, pairings and snapshots would point at playlists
  the newly connected account doesn't own.
- **A Spotify account connects to at most one workspace, site-wide.** The
  database enforces this. The connect step gets one more error state: "This
  Spotify account is already connected to another workspace."
- **No self-serve way out of a wrong first connection.** While the owner is
  the only user, it is fixed by hand. Account deletion in #123 becomes the
  way out later.
- **Swapping to a new app keeps the workspace's data.** Routines, schedules
  and history belong to the workspace, not the app. The person goes back
  through setup steps 1 to 4 and reconnects.
- **Whether schedules are paused is derived, not stored.** A schedule runs
  only while its workspace's Spotify connection is `ok`. Schedules the person
  turned off stay off when the app recovers, because nothing the app does
  touches their on/off switch.
- **The authorize URL always asks Spotify to show its account dialog**
  (`show_dialog=true`), so a browser already signed in to a different
  Spotify account can't silently bind the wrong one for good.

## Constraints

- **Where the site-wide app is used today.** The credentials come from
  `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and `SPOTIFY_REDIRECT_URI`
  through `SpotifyCredentials` (`shuffify/spotify/credentials.py`). Today it
  is built from Flask config or the environment in:
  - `shuffify/spotify/__init__.py`, `auth.py` and `api.py`
  - `shuffify/services/auth_service.py`
  - `shuffify/services/executors/base_executor.py`

  Every one of these must resolve the workspace's app instead. The ID and
  secret settings are retired, and so is Basic authorization on the token
  and revoke calls (`shuffify/spotify/auth.py:236, 297, 382`). The redirect
  URL stays site-wide, because it's the same for every workspace: it is
  `PUBLIC_ORIGIN` plus `/spotify/callback`, so the `SPOTIFY_REDIRECT_URI`
  setting is retired with the ID and secret.
- **Identity today.** `users` is keyed by `spotify_id`, and signing in with
  Spotify creates the user. Thirteen tables have a foreign key to `users.id`,
  and `job_executions.user_id` has none. In the new model the person (Google
  login) is the user, product data belongs to the workspace, and today's
  Spotify-account row becomes the workspace's Spotify connection. Because the
  database is wiped, the schema can be reshaped freely. There is no backfill
  to write.
- **A refresh token only works with the app that issued it.** After an app is
  swapped, the old token is useless and must be discarded on reconnect.
- **PKCE refresh tokens rotate and may be single-use.** The database holds
  the only copy of a connection's refresh token. Whoever refreshes (a web
  request or the worker) does it in a short transaction that locks the
  connection's row (`SELECT ... FOR NO KEY UPDATE`, with a lock timeout that
  lasts only for that transaction), reads the token from the database,
  refreshes, and stores the new one before committing. The lock is around the
  refresh, never around a whole run. A lock timeout is a transient failure, not
  a sign-out. The session keeps no copy of the refresh token.
- **Recent sign-in hardening** (#135, #139, #143, #150) covers:
  - session handling when sign-in starts or fails;
  - the encrypted session refresh token;
  - the keyed session id;
  - rate limits keyed by user;
  - refusing a cross-site `/login`.

  The new Google sign-in and the Connect Spotify flow keep all of these
  properties, except that the session no longer carries a refresh token.
- **Open security issues touch the same model.** #126 and #128 to #131
  (draft PRs #144 and #145) touch code the re-key rewrites. They don't gate
  the branch: it is cut when phase 02 lands, and main is merged into it as
  each fix lands. #141 stays a separate issue and isn't built now: an expired refresh
  token surfaces as the access-removed case with a reconnect fix. Build so
  they stay solvable per workspace; don't fix any of them here unless the new
  model makes one trivial.
- **The test harness** requires fake Spotify credentials (`config.py`, the
  `HARNESS_FAKE_CLIENT_ID` check). It needs to seed a workspace whose app
  carries those fakes, and keep refusing anything real. Its fake Spotify
  must enforce PKCE.
- **Commit and PR text** describes behaviour and tests only, in the repo's
  usual style.

## Cutover

The domain has already moved (phase 01b), the pre-deploy script already knows
how to build a new database (phase 06), and the project has run on staging
against real Spotify and Google. The owner is the only user, so cutover is one
sitting: wipe, merge, then set up from scratch.

1. Before the switch, the owner clears their raid inbox and notes their
   schedule settings. Playlists and archive playlists live on Spotify and are
   untouched.
2. Stop the old worker, snapshot the database, wipe it and merge straight
   away. The new build creates the schema, and the worker is redeployed once
   it has.
3. The owner deletes their old Spotify app in Spotify's dashboard and signs up
   as the first new user. They create a new app by following the in-product
   guide alone, timed, which is the real test of the ten-minute goal (the
   target is under 15 minutes from step 1 to their own playlists), and the
   setup copy gets checked against Spotify's live dashboard.
4. The owner rebuilds their schedules and routines through the product, then
   watches the first scheduled run of each routine and restores one from its
   snapshot. This is the acceptance test.
5. The retired Spotify settings are removed from Railway and the staging
   environment is deleted.

Other people who had accounts sign up fresh like anyone else.

## Left to the builder

- **Schema details:** table and column names, as long as the model and its
  constraints in Decisions hold. The build plan proposes them.
- **Google sign-in:** the library, and how the session works across the
  Vercel web app and the Railway API.
- **Detecting failures:** how to tell Spotify's failure cases apart (token
  refresh errors, 401/403 responses). The build plan maps `invalid_client`,
  `invalid_grant` and a 403 "user not registered" to three broken-app
  reasons, each with its own fix, and detects them where the Spotify client
  raises them, because services catch these errors before a route could see
  them.
- **In-flight work when an app breaks:** what happens to queued and running
  operations. The existing journal and needs-attention handling should carry
  most of it.
- **Copy and screenshots:** final wording, and screenshots checked against
  Spotify's live developer dashboard, including any current requirement
  Spotify puts on app owners.
- **Tests and phasing:** test layout and how the work splits into PRs.
