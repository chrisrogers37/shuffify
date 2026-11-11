---
title: "[plan] Attach a Spotify app; connect Spotify through it with PKCE — routes, credentials, harness, staging"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, auth, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: L.

A workspace can save its Spotify app's client ID and connect a Spotify
account through that app using Authorization Code with PKCE. There is no
client secret anywhere (F8). Every Spotify call then uses the workspace's
app, and the site-wide Spotify app settings are removed. The phase also
stands up staging, so the first real Spotify and real Google runs happen
here.

## Evidence

- After 02, `credentials_for` (`shuffify/spotify/credentials.py`) is the
  only source of credentials, `AuthService` takes them as an argument, and
  the database is the only home of the refresh token, refreshed under the
  credential lock.
- After 03 and 05, `SpotifyConnection` exists, and a stopgap
  `/spotify/connect` and `/callback` attach the site app's connection to the
  signed-in person's workspace.
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
01b (the raccoon.fm redirect URL is already on the owner's app), 02, 05.
The owner, for staging: access to Railway, Neon, Vercel, Google Cloud and the
Spotify dashboard.

### Blocks
07 (the setup page calls these endpoints), 08, 09.

### Steps

1. **`credentials_for(connection)`** returns `SpotifyCredentials(client_id,
   redirect_uri)`: the connection's client ID and the site's
   `SPOTIFY_REDIRECT_URI`. `SpotifyCredentials` loses `client_secret`.
   - It raises `SpotifyAppUnavailable` when there's no connection or no
     client ID. It does not look at `status`, because Connect is the fix for
     a broken app and must work while one is broken.
   - Code that calls Spotify on a person's behalf refuses a connection that
     isn't `ok`: `require_auth` returns `spotify_not_connected` for
     `unchecked`, and phase 08 adds the broken case and the worker's
     claim-time refusal.
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
   - creates or replaces the workspace's connection;
   - sets `status = unchecked` and clears `broken_reason`;
   - returns `{client_id, status}`.

   A migration in this phase adds the unique constraint on
   `spotify_connections.client_id`, in the same change that deletes the
   stopgap (step 5). A `client_id` already used by another workspace returns
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
   - `GET /callback` stays at the same path: the redirect URL the person
     pastes is `https://raccoon.fm/callback` (F13). It runs only for a
     signed-in person, **never calls `session.clear()`**, and removes only
     `spotify_connect` from the session.
     - The state must match with `hmac.compare_digest`, and the workspace in
       it must be the session's current workspace.
     - The code exchange uses `credentials_for(connection)` and the stored
       verifier.
     - The profile's `spotify_id` must equal the connection's bound
       `spotify_id` when one is set (`wrong_spotify_account`), and must not
       belong to another workspace's connection
       (`spotify_account_in_use_elsewhere`; the unique constraint is the
       backstop).
     - On success it stores the encrypted refresh token, sets `spotify_id`
       and the profile, sets the session's Spotify access token, and calls
       `SpotifyConnectionHealth.mark_ok(connection)`. The status becomes `ok`
       here and nowhere earlier. Phase 08 extends `mark_ok` to resume
       schedules.
     - Spotify's "not registered" error becomes `spotify_user_not_allowed`.
   - A redirect URL mismatch or a wrong client ID never reaches `/callback`:
     Spotify stops on its own page. There is no notice for it; step 2 and 3
     of the setup copy (phase 07) explain what that page looks like and to
     come back and fix it.
   - Add the notice codes to `shuffify/services/auth_notice.py`.
5. **Delete the phase 03 and 05 stopgap** (the site app's client ID copied
   into a connection). Its migration is the one that adds the unique
   constraint in step 3.
6. **Make the fake Spotify enforce PKCE.** In `harness/fake_spotify/`:
   - a per-app registry (`/_fake/apps`): client ID, redirect URIs and an
     allow-list, with no secret;
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
7. **Stand up staging** (the owner, with the overview's Staging section):
   the Railway environment, the emptied Neon branch, the Vercel branch
   variable and address, the Spotify redirect URI on the owner's existing
   app, and the Google client in Testing mode. Deploy `feat/workspaces` to
   it and run the checks below.

## Test Plan
- **Route tests:**
  - saving an ID rejects a non-hex or wrong-length value, and normalises
    case;
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
  - replacing the app keeps the workspace's data and requires reconnecting;
  - status is `unchecked` after saving and `ok` only after a successful
    connect.
- **Fake Spotify tests:** a missing, wrong or reused verifier is refused; a
  Basic header is refused; a deleted app answers `invalid_client`; a rotated
  refresh token invalidates the old one.
- **Harness e2e:** the full first-ten-minutes flow, from Google sign-in to
  the person's own playlists.
- **Isolation:** two workspaces with two fake apps; each one's Spotify calls
  use its own client ID (the fake records which client called).
- **On staging, against real Spotify and Google** (the owner):
  - sign in with Google;
  - save the existing app's client ID, connect and confirm the playlists
    load, with Spotify's account dialog shown;
  - let a token refresh and confirm the stored token rotated (this is where
    real Spotify's PKCE refresh behaviour is first seen);
  - run a routine once.

## Verification Checklist
- [ ] `git grep -n "SPOTIFY_CLIENT_ID\|SPOTIFY_CLIENT_SECRET" -- shuffify config.py scripts harness .railway .github .env.example README.md tests`
      returns nothing.
- [ ] `git grep -n "client_secret" -- shuffify/spotify harness/fake_spotify`
      returns nothing.
- [ ] `git grep -n "from_env\|from_flask_config" -- shuffify` returns nothing.
- [ ] The harness e2e first-run test passes.
- [ ] The staging checks above pass.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't ask for, accept, store or send a client secret, and don't send a
  Basic authorization header to Spotify.
- Don't refuse connect because the app is `broken` or `unchecked`; it is the
  fix.
- Don't call `session.clear()` in `/callback`.
- Don't add a notice for a redirect URL mismatch; it never arrives.
- Don't fall back to any default client ID, including in the harness.
- Don't point staging at the production database, Redis or URL.

## Context
- Source skill: forge · Area: routes, credentials, harness/fake_spotify, ops · Effort: L · Risk: High · Priority: High
