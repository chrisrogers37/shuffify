---
title: "[plan] Attach and check a Spotify app; connect Spotify through it — routes, credentials, harness"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, auth, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: L.

A workspace can save its Spotify app's client ID and secret, check them with
Spotify, and connect a Spotify account through that app. Every Spotify call
then uses the workspace's app, and the site-wide Spotify app settings are
removed.

## Evidence

- After 02, `credentials_for` (`shuffify/spotify/credentials.py`) is the
  only source of credentials, and `AuthService` takes them as an argument.
- After 03, `SpotifyConnection` and `AppSecretService` exist.
- The fake Spotify (`harness/fake_spotify/app.py:43-44, 256-312`) has one
  hard-coded client and no `client_credentials` grant. Its `/authorize`
  checks `redirect_uri` and offers seeded users (`seed.json:4-11`).

## Implementation Plan

### Dependencies
02, 05.

### Blocks
08, 09.

### Steps

1. **`credentials_for(connection)`** returns the connection's `client_id`,
   its decrypted secret and the site's `SPOTIFY_REDIRECT_URI`. It raises
   `SpotifyAppUnavailable` when there's no connection or its status is
   `broken`. Delete `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` from:
   - `config.py` (L52 validation, L165-170, L244-250);
   - `.railway/railway.ts`;
   - `.env.example`;
   - the docs;
   - CI env (`ci.yml:73-75, 143-145`).
2. **`PUT /api/workspace/spotify-app` `{client_id, client_secret}`**, with
   CSRF:
   - validates the formats;
   - creates or replaces the workspace's connection;
   - stores the secret through `AppSecretService`;
   - sets `status = unchecked`;
   - returns `{client_id, status}`, never the secret.

   A `client_id` already used by another workspace returns
   `app_in_use_elsewhere`.

   Replacing the app keeps the connection row, clears its refresh token and
   leaves `spotify_id` as it is. Under the ratified binding rule, the next
   connect must be the same Spotify account.
3. **`POST /api/workspace/spotify-app/check`** requests a
   `client_credentials` token from Spotify with the saved credentials.
   - Success sets `status = ok`.
   - `invalid_client` returns `app_rejected` and leaves the status
     `unchecked`.
   - Rate-limit it per person.
4. **Connect Spotify.**
   - `GET /spotify/connect` requires a signed-in person and an `ok` app.
     It sets `oauth_state` together with the workspace id, and redirects to
     Spotify with that workspace's client ID.
   - Rename today's Spotify `/callback` to `GET /callback`, kept at the
     same path for the redirect URL. It runs only for a signed-in person,
     and it must match the state and the workspace the state was issued for.
     - The code exchange uses `credentials_for(connection)`.
     - The profile's `spotify_id` must equal the connection's bound
       `spotify_id` when one is set (`wrong_spotify_account`), and must not
       belong to another workspace's connection
       (`spotify_account_in_use_elsewhere`).
     - On success it stores the encrypted refresh token, sets `spotify_id`
       and the profile, and sets the session's Spotify token.
     - Spotify's "not registered" error becomes `spotify_user_not_allowed`.
       A `redirect_uri` mismatch becomes `redirect_uri_mismatch`.
   - Add the notice codes to `shuffify/services/auth_notice.py`.
5. **Delete the phase 03 stopgap** (the site app's credentials copied into a
   connection).
6. **Remove the old Spotify sign-in path from the harness.** Give the fake
   Spotify:
   - several apps (`/_fake/apps` to register a client ID, secret and
     redirect URIs, plus a per-app allow-list);
   - the `client_credentials` grant;
   - a deleted-app mode.

   `HarnessConfig.assert_safe` now checks that every connection's client ID
   is registered with the fake. The fake already refuses unknown clients.
   `check_login` and `App._log_in` become: fake Google, save fake app keys,
   check, then connect as `fake_user`.

## Test Plan
- **Route tests:**
  - saving keys never echoes the secret;
  - a duplicate client ID is refused;
  - the check maps success and `invalid_client`;
  - connect is refused without an `ok` app;
  - the callback is refused for a mismatched state or workspace, a wrong
    bound account, an account already used elsewhere, a user not on the
    allow-list, and a redirect mismatch;
  - replacing the app keeps the workspace's data and requires reconnecting.
- **Harness e2e:** the full first-ten-minutes flow, from Google sign-in to
  the person's own playlists.
- **Isolation:** two workspaces with two fake apps; each one's Spotify calls
  use its own client ID (the fake records which client called).

## Verification Checklist
- [ ] `git grep -n "SPOTIFY_CLIENT_ID\|SPOTIFY_CLIENT_SECRET" -- shuffify config.py .railway .github`
      returns nothing.
- [ ] The harness e2e first-run test passes.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't return the secret, or anything derived from it, from any endpoint.
- Don't let connect run when the app status isn't `ok`.
- Don't fall back to any default client ID, including in the harness.

## Context
- Source skill: forge · Area: routes, credentials, harness/fake_spotify · Effort: L · Risk: High · Priority: High
