---
title: "[plan] Google sign-in and the workspace in the session — routes, session, harness"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, auth]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: L.

People sign in with Google. The first sign-in creates the person and their
first workspace, and the session holds the person and the current workspace.
Spotify stops being a way to sign in; from here on it's only a connection
(phase 06). The test harness gets a fake Google.

## Evidence

**Today's sign-in** (`shuffify/routes/core.py`):
- `/login` (L71-98) refuses `Sec-Fetch-Site: cross-site`, requires
  `legal_consent`, sets `oauth_state`, and redirects to Spotify.
- `/callback` (L101-230) compares the state, regenerates the session id, and
  restores the previous token when sign-in fails (#135, #150).

**Who's signed in**
- `routes/contexts.py:61-76` (`/api/session`) returns the Spotify profile.
- `get_db_user` (`routes/__init__.py:231-260`) and the rate-limit key
  (`shuffify/__init__.py:141-148`) use the Spotify id.

**The rest**
- CSP: `web/security-headers.ts:13` allows only `accounts.spotify.com`.
- The reference implementation is storydump's
  `src/services/target/google_oidc.py`.
- The harness: `scripts/harness.sh:501-523` (`check_login`),
  `tests/e2e/conftest.py:150-168` (`App._log_in`), and `HarnessConfig.assert_safe`
  (`config.py:332-390`).

## Implementation Plan

### Dependencies
04.

### Blocks
06, 07.

### Steps

1. **Port storydump's OIDC module** as `shuffify/services/google_oidc.py`:
   - `authorization_url(client_id, redirect_uri, state)`;
   - `exchange_code`;
   - `verify_id_token`, which checks `iss`, `aud`, `exp` and `iat` with 60s
     skew, the nonce (SHA-256 of the state) and the presence of `sub`;
   - `GoogleIdentity(sub, email, email_verified, name, picture)`.

   Base URLs come from `GOOGLE_BASE_URL` (default Google, loopback only,
   refused in production) and `GOOGLE_ACCOUNTS_URL`.
2. **Add settings:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
   `GOOGLE_REDIRECT_URI` (`https://raccoon.fm/auth/google/callback`) in
   `config.py`, `.railway/railway.ts`, `.env.example` and the docs. They're
   required in production for the api role.
3. **Make the routes.** These keep every property of today's `/login` and
   `/callback`: refuse cross-site starts, require consent, compare state
   with `hmac.compare_digest`, regenerate the session id on success, and
   leave the session untouched on failure.
   - `GET /login` is now Google sign-in.
   - `GET /auth/google/callback`:
     - finds the person by `google_sub`, or creates them and a first
       workspace named "My workspace";
     - sets `session["user_id"]` and `session["workspace_id"]` (that
       person's first workspace);
     - records login history and activity;
     - redirects to `/`.
   - Add the callback path to the Vercel forwarding list
     (`web/scripts/build-vercel-output.mjs:56-57`) and the harness preview
     proxy (`tests/frontend_preview.py:13`).
4. **Change session helpers.**
   - `require_session_and_db` and its relatives resolve the person and the
     workspace from the session.
   - `require_auth` (anything that calls Spotify) also requires a connected
     Spotify connection. Without one, the route returns
     `spotify_not_connected`.
   - The rate-limit key becomes `user:{user_id}`.
   - `/api/session` returns:
     - `signed_in`;
     - `user {id, display_name, image_url}`;
     - `workspace {id, name}`;
     - `setup {spotify_app: none|unchecked|ok|broken, spotify_connected: bool}`;
     - `csrf_token` and `notice`.

     Update `web/src/api/contracts.ts:43-64`.
5. **Update the web app.**
   - The landing page and the signed-out panel say "Continue with Google".
   - The consent checkbox stays as it is.
   - Add `https://accounts.google.com` to CSP `form-action`
     (`web/security-headers.ts`, plus the rewrite in `frontend_preview.py`).
   - The legal copy describes Google sign-in and bring-your-own Spotify
     app; update the hash pins.
6. **Logout** clears the whole session, as it does today.
6a. **A follow-up migration makes `users.google_sub` NOT NULL.** The
    phase 03 stopgap stops creating people; in this phase it only attaches a
    Spotify connection to the signed-in person's workspace. Phase 06
    replaces it.
7. **Build a fake Google in the harness:** `harness/fake_google/`, on its
   own loopback port.
   - It has `/o/oauth2/v2/auth` with a `fake_user=` shortcut, `/token`
     (which returns an unsigned ID token with the correct claims) and seeded
     people.
   - `HarnessConfig.assert_safe` requires `GOOGLE_BASE_URL` on loopback and
     the fake Google client credentials, and the netguard allows the port.
   - `check_login` and `App._log_in` sign in through fake Google.
   - Tests that need Spotify still call the old Spotify path, kept in the
     harness only until phase 06 replaces it.

## Test Plan
- **Unit tests for `google_oidc`,** ported from storydump: a wrong `iss`,
  `aud`, nonce or `exp`, or a missing `sub`, is refused by name; an
  unverified email isn't stored as verified.
- **Route tests:**
  - a cross-site start is refused;
  - missing consent is refused;
  - a state mismatch is refused;
  - the first sign-in creates a person and a workspace;
  - a second sign-in reuses them;
  - a failed callback leaves the session as it was.
- **`/api/session`** contract tests; the web contract tests and Playwright
  session mocks updated to the new shape.
- **Harness e2e:** sign in through fake Google and land on the app with
  `setup.spotify_app = none`.

## Verification Checklist
- [ ] The harness `check_login` passes through fake Google.
- [ ] `git grep -n "accounts.spotify.com" -- web/security-headers.ts` still
      matches (the Spotify connect flow needs it) next to
      `accounts.google.com`.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't key identity on email.
- Don't verify ID tokens with a JWKS fetch (F3).
- Don't drop any of the existing sign-in protections.
- Don't build workspace switching. The session always holds the person's
  first workspace.

## Context
- Source skill: forge · Area: routes, session, web auth, harness · Effort: L · Risk: High · Priority: High
