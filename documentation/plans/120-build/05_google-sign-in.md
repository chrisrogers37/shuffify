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
- `/callback` (L101-230) compares the state, calls `session.clear()` on
  success (L150), and restores the previous token when sign-in fails (#135,
  #150). Both routes share the one `oauth_state` session key.

**Who's signed in**
- `routes/contexts.py:61-76` (`/api/session`) returns the Spotify profile.
- `get_db_user` (`routes/__init__.py:231-260`) and the rate-limit key
  (`shuffify/__init__.py:141-148`) use the Spotify id.

**The rest**
- CSP: `web/security-headers.ts:13` allows only `accounts.spotify.com`.
- The reference implementation is storydump's
  `src/services/target/google_oidc.py`.
- Vercel forwards only single-segment paths (`web/scripts/build-vercel-output.mjs:56-57`),
  and the harness preview proxy matches an exact set (`tests/frontend_preview.py:13`).
- Secret scrubbing: `GOOGLE_CLIENT_SECRET` is a new secret. Config values are
  registered for free-text redaction in `_SENTRY_SECRET_CONFIG_ATTRS`
  (`shuffify/__init__.py:485-490`), and
  `tests/test_sentry_init.py:547-573` fails for a secret-shaped config
  attribute that is in neither registered list nor the acknowledged
  non-secret set.
- Logs: #138's rule is internal ids in logs, never an email or a name.
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
   - **Register the secret for scrubbing.** Add `GOOGLE_CLIENT_SECRET` to
     `_SENTRY_SECRET_CONFIG_ATTRS` (`shuffify/__init__.py:485`), the list
     of config values redacted from free text. The key name `client_secret`
     is already in `_SENTRY_PII_DENYLIST`, which covers the token request's
     form body.
   - Add `GOOGLE_REDIRECT_URI`, `GOOGLE_BASE_URL` and `GOOGLE_ACCOUNTS_URL`
     to the acknowledged non-secret set in
     `tests/test_sentry_init.py` (`NOT_SECRETS`), each as a decision.
3. **Make the routes.** These keep every property of today's `/login` and
   `/callback`: refuse cross-site starts, require consent, compare state
   with `hmac.compare_digest`, regenerate the session id on success, and
   leave the session untouched on failure.
   - `GET /login` is now Google sign-in. Its state is kept under the session
     key `google_state`, and the nonce is derived from it. Spotify's flow
     uses a different key, `spotify_connect` (phase 06), so neither flow can
     consume or overwrite the other's state. `oauth_state` is retired.
   - `GET /auth/google/callback`:
     - finds the person by `google_sub`, or creates them and a first
       workspace named "My workspace";
     - sets `session["user_id"]` and `session["workspace_id"]` (that
       person's first workspace);
     - records login history and activity, describing them by internal ids
       only: no log line or activity text contains the email or display name
       (#138);
     - redirects to `/`.
   - **Add `/auth/google/callback` and `/spotify/connect` to the Vercel
     forwarding rule** (`web/scripts/build-vercel-output.mjs:56-57`; the
     current pattern matches single segments, so these two-segment paths
     need their own alternatives) **and to the harness preview proxy**
     (`tests/frontend_preview.py:13`). Without them the browser never reaches
     the API for either.
   - **`GET /spotify/connect` and `/callback`, as a stopgap until 06.**
     `/spotify/connect` requires a signed-in person, refuses a cross-site
     start like `/login`, stores `{state, workspace_id}` under
     `spotify_connect`, and redirects to Spotify with the site app. `/callback`
     requires a signed-in person, compares that state, and attaches the
     Spotify connection to that person's workspace. **It never calls
     `session.clear()`:** a signed-in person stays signed in, and only
     `spotify_connect` and the Spotify token entries change. Phase 06
     replaces the stopgap's body with the per-workspace PKCE flow and keeps
     the paths.
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
     - `setup {spotify_app: none|unchecked|ok|broken, spotify_connected: bool, redirect_url}`,
       where `redirect_url` is the site's `SPOTIFY_REDIRECT_URI`, so the web
       app shows it without hard-coding a domain (phase 07 needs it);
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
    phase 03 stopgap stops creating people; from here it only attaches a
    Spotify connection to the signed-in person's workspace, as step 3
    describes. Phase 06 replaces it.
7. **Build a fake Google in the harness:** `harness/fake_google/`, on its
   own loopback port.
   - It has `/o/oauth2/v2/auth` with a `fake_user=` shortcut, `/token`
     (which returns an unsigned ID token with the correct claims) and seeded
     people.
   - `HarnessConfig.assert_safe` requires `GOOGLE_BASE_URL` on loopback and
     the fake Google client credentials, and the netguard allows the port.
   - `check_login` and `App._log_in` sign in through fake Google.
   - Tests that need Spotify use the stopgap `/spotify/connect` and
     `/callback` against the fake Spotify until phase 06 replaces them.

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
- **Scrubbing:** a `_strip_pii` test puts the configured
  `GOOGLE_CLIENT_SECRET` value into an exception message, a breadcrumb and a
  request body, and asserts it is redacted in each; and
  `TestSecretPolicyDoesNotRot` passes with the new settings.
- **Logs:** a sign-in test asserts the captured logs and the activity row
  contain no email address or display name.
- **Session keys:** a Google callback with a `spotify_connect` entry in the
  session leaves it alone, and a Spotify callback with a `google_state` entry
  leaves that alone; a stopgap `/callback` never signs the person out.
- **`/api/session`** contract tests, including `setup.redirect_url`; the web contract tests and Playwright
  session mocks updated to the new shape.
- **Harness e2e:** sign in through fake Google and land on the app with
  `setup.spotify_app = none`.

## Verification Checklist
- [ ] The harness `check_login` passes through fake Google.
- [ ] `git grep -n "oauth_state" -- shuffify` returns nothing.
- [ ] `git grep -n "GOOGLE_CLIENT_SECRET" -- shuffify/__init__.py` shows it in
      `_SENTRY_SECRET_CONFIG_ATTRS`.
- [ ] `git grep -n "accounts.spotify.com" -- web/security-headers.ts` still
      matches (the Spotify connect flow needs it) next to
      `accounts.google.com`.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't key identity on email.
- Don't verify ID tokens with a JWKS fetch (F3).
- Don't drop any of the existing sign-in protections.
- Don't call `session.clear()` in any Spotify route.
- Don't put an email or display name in a log line or activity text.
- Don't build workspace switching. The session always holds the person's
  first workspace.

## Context
- Source skill: forge · Area: routes, session, web auth, harness · Effort: L · Risk: High · Priority: High
