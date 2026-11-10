# Workspaces and bring-your-own Spotify app

Issue: #120 · Epic: #119 · Status: design approved · Build plan: [`120-build/`](120-build/00_overview.md)

The product is being renamed Raccoon, at raccoon.fm (#122). This doc uses the
new name. The rename of what users see ships before this project launches,
because the redirect URL every person pastes into their Spotify app is
`https://raccoon.fm/callback`. The code's internal name stays `shuffify`.

## Why

Spotify keeps every developer app in development mode: the app's owner plus a
handful of allow-listed accounts, and no route to extended quota. One shared
Raccoon app can therefore never serve the public. If each person brings their
own Spotify app, Raccoon can serve anyone, because each app only ever needs to
serve its own owner.

This project adds that layer and nothing else. Once a person has finished
setup, Raccoon behaves exactly as it does today (Workshop, routines,
schedules, restore, the durable queue), except that every Spotify call for
their workspace goes through their own app.

## What the person experiences

1. They sign in to Raccoon with Google.
2. They create a workspace.
3. A guided setup walks them through creating a Spotify developer app, adding
   Raccoon's redirect URL and their own Spotify account to it, and pasting
   the app's client ID and secret into the workspace.
4. Raccoon checks the keys with Spotify.
5. They choose Connect Spotify, sign in on Spotify through their own app, and
   land on their own playlists.

From then on the product is today's product.

Mockups in the production design system:

- [`120-mockups/setup-steps.html`](120-mockups/setup-steps.html): all five
  setup steps, the checking, rejected and accepted states for the keys, the
  two errors people hit coming back from Spotify, and the finished state.
- [`120-mockups/uh-oh.html`](120-mockups/uh-oh.html): the guided setup step
  beside the "uh oh" page for a broken app.

Open them from a checkout so the fonts load from `web/public/fonts`. The
mockups set the look, the order of the steps and the states to cover. Copy and
screenshots are drafts.

## Goals

- Anyone with a Google account and a Spotify account can set up a workspace
  without help, in about ten minutes.
- Every Spotify call for a workspace uses that workspace's app: web requests,
  queued operations, the worker and the scheduler.
- App secrets are protected as carefully as refresh tokens are today, or more
  carefully.
- When an app stops working, the person is told what happened and how to fix
  it, and nothing runs against their playlists in the meantime.

## Non-goals

- Sign-in methods other than Google (email, magic link, others). Later, if
  people ask for them.
- Several people in one workspace, or inviting the other accounts on a
  Spotify app's allow-list. The epic leaves shared workspaces out of scope.
- The MCP server (#121), the product's name (#122), and launch safeguards such
  as abuse limits, account deletion and legal pages (#123).
- Migrating today's accounts (see Launch).
- Creating and switching between several workspaces in the product (the data
  model supports it; see Decisions).
- Moving music between Spotify accounts. That would mean raiding or rotating
  between playlists in two workspaces owned by the same person. It's a later
  feature that this model makes possible, not part of this project.

## Anti-goals

These would make the project a failure even if it technically works:

- Any Spotify call for a workspace that falls back to a site-wide app. If a
  workspace's app can't be resolved, the call fails. It never quietly uses
  someone else's app.
- An app secret that can be read back once saved, by the person, the API, the
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
  - At launch the product shows one workspace per person. Creating and
    switching between several comes later; the data model is ready for it.
- **App secrets are stored encrypted.** Security is a first-class concern for
  this project.
- **Guided setup** explains every step on Spotify's side: creating the app,
  where the redirect URL goes, the allow-list, and where to find the ID and
  secret.
- **No migration.** At launch the production database is wiped and everyone
  signs up fresh, the owner included. Rebuilding the owner's schedules and
  routines through the new flow is part of their acceptance testing.
- **A broken app pauses the workspace's scheduled runs** and sends the person
  to an "uh oh" page with the fix that matches what Spotify reported: paste a
  new secret, start setup again with a new app, or add their account to the
  allow-list and reconnect.
- **Paused schedules resume on their own once the app works again.** They run
  at their next scheduled time. Runs missed while paused are not made up.
- **No sign-up gate.** Sign-up is open when this ships. The owner is the only
  expected user until #123, so the launch safeguards wait for #123.

### Design calls, ratified by the owner

- **A Spotify app belongs to at most one workspace.** The client ID is unique
  across workspaces.
- **App secrets get their own encryption key,** separate from
  `TOKEN_ENCRYPTION_KEY` (which protects refresh tokens), so one leaked
  setting doesn't expose both. Rotating that key must be possible without
  asking everyone to re-enter their secret.
- **One redirect URL for every workspace.** Each person adds the same
  Raccoon callback URL to their own app. The OAuth `state`, already random
  and checked in `shuffify/routes/core.py`, binds each round trip to the
  signed-in person and the workspace they are connecting, and therefore to
  that workspace's app. Per-workspace
  callback URLs were considered and not chosen: they add routing and make the
  setup copy longer, for no security gain over a bound state.
- **"Checking the keys"** means requesting a client-credentials token from
  Spotify with them. That proves the ID and secret match. It can't prove the
  redirect URL or the allow-list, so those are only proven by step 5, and
  step 5's errors point back to step 3.
- **A workspace is bound to the first Spotify account it connects.**
  Reconnecting, including through a replacement app, must use the same
  Spotify account. A different account is refused with an explanation.
  Otherwise saved routines, pairings and snapshots would point at playlists
  the newly connected account doesn't own.
- **A Spotify account connects to at most one workspace, site-wide.** The
  database enforces this. Step 5 gets one more error state: "This Spotify
  account is already connected to another workspace."
- **No self-serve way out of a wrong first connection.** While the owner is
  the only user, it is fixed by hand. Account deletion in #123 becomes the
  way out later.
- **Swapping to a new app keeps the workspace's data.** Routines, schedules
  and history belong to the workspace, not the app. The person goes back
  through steps 2 to 5 and reconnects.
- **Schedules paused by a broken app are kept apart from schedules the person
  turned off.** Resuming after a fix only touches the first kind.

## Constraints

- **Where the site-wide app is used today.** The credentials come from
  `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and `SPOTIFY_REDIRECT_URI`
  through `SpotifyCredentials` (`shuffify/spotify/credentials.py`). Today it
  is built from Flask config or the environment in:
  - `shuffify/spotify/__init__.py`, `auth.py` and `api.py`
  - `shuffify/services/auth_service.py`
  - `shuffify/services/executors/base_executor.py`

  Every one of these must resolve the workspace's app instead. The ID and
  secret settings are retired. The redirect URL stays a site setting, because
  it's the same for every workspace.
- **Identity today.** `users` is keyed by `spotify_id`, and signing in with
  Spotify creates the user. Thirteen tables have a foreign key to `users.id`,
  and `job_executions.user_id` has none. In the new model the person (Google
  login) is the user, product data belongs to the workspace, and today's
  Spotify-account row becomes the workspace's Spotify connection. Because the
  database is wiped, the schema can be reshaped freely. There is no backfill
  to write.
- **A refresh token only works with the app that issued it.** After an app is
  swapped, the old token is useless and must be discarded on reconnect.
- **Recent sign-in hardening** (#135, #139, #143, #150) covers:
  - session handling when sign-in starts or fails;
  - the encrypted session refresh token;
  - the keyed session id;
  - rate limits keyed by user;
  - refusing a cross-site `/login`.

  The new Google sign-in and the Connect Spotify flow keep all of these
  properties.
- **Open security issues touch the same model:** #128, #129, #130, #131 and
  #141. Build so they stay solvable per workspace; don't fix them here unless
  the new model makes one trivial.
- **The test harness** requires fake Spotify credentials (`config.py`, the
  `HARNESS_FAKE_CLIENT_ID` check). It needs to seed a workspace whose app
  carries those fakes, and keep refusing anything real.
- **Commit and PR text** describes behaviour and tests only, in the repo's
  usual style.

## Launch

1. Before the switch, the owner clears their raid inbox and notes their
   schedule settings. Playlists and archive playlists live on Spotify and are
   untouched.
2. Deploy, wipe the production database, and remove the retired Spotify
   settings from Railway.
3. The owner signs up as the first new user. They can reuse their existing
   Spotify app: paste its ID and secret, and add the new redirect URL on
   Spotify's side if it changed.
4. The owner rebuilds their schedules and routines through the product. This
   is the acceptance test.
5. Saved snapshots (restore points) are lost in the wipe. Switch on a quiet
   day and watch the first scheduled run of each routine.

Other people who had accounts sign up fresh like anyone else.

## Left to the builder

- **Schema details:** table and column names, as long as the model and its
  constraints in Decisions hold. The build plan proposes them.
- **Google sign-in:** the library, and how the session works across the
  Vercel web app and the Railway API.
- **Key management:** how the secret-encryption key is stored and rotated.
- **Detecting failures:** how to tell Spotify's failure cases apart (token
  refresh errors, 401/403 responses). Check whether Spotify actually
  distinguishes "secret rejected" from "app deleted". If it doesn't, the
  "uh oh" page offers both fixes together for that case.
- **In-flight work when an app breaks:** what happens to queued and running
  operations. The existing journal and needs-attention handling should carry
  most of it.
- **Copy and screenshots:** final wording, and screenshots checked against
  Spotify's live developer dashboard. That includes any current requirement
  Spotify puts on app owners, which the setup should mention up front.
- **Tests and phasing:** test layout and how the work splits into PRs.
