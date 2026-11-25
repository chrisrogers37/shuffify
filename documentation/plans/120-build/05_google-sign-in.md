---
title: "[plan] Google sign-in and the workspace in the session (routes, session, harness)"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-25
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

   Both Google hosts take their base URL from one override, `GOOGLE_BASE_URL`.
   It is parsed by the same `local_origin` rule as Spotify's override
   (`spotify/endpoints.py:35-63`: a bare loopback origin and nothing else) and
   refused at boot in production by the same check (`config.py:77-78`). There
   is no separate `GOOGLE_ACCOUNTS_URL` setting. `verify_id_token` accepts
   exactly the two issuer forms Google uses, `https://accounts.google.com`
   and `accounts.google.com`.
2. **Add settings:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
   `GOOGLE_REDIRECT_URI` (`https://raccoon.fm/auth/google/callback`) in
   `config.py`, `.railway/railway.ts`, `.env.example` and the docs. They're
   required in production for the api role. Adding them to
   `.railway/railway.ts` on the branch gives production's services nothing by
   itself: Railway reads that file only when the config is applied (overview,
   Evidence), so phase 09 step 3 applies the references to production before
   the cutover.
   - **Register the secret for scrubbing.** Add `GOOGLE_CLIENT_SECRET` to
     `_SENTRY_SECRET_CONFIG_ATTRS` (`shuffify/__init__.py:485`), the list
     of config values redacted from free text. The key name `client_secret`
     is already in `_SENTRY_PII_DENYLIST`, which covers the token request's
     form body.
   - **Register the flow's other material too.** Add `id_token` and the PKCE
     verifier's key to `_SENTRY_PII_DENYLIST`, and a JWT shape to the shapes
     the scrubber matches, with cases in `tests/test_sentry_egress.py`.
   - Add `GOOGLE_REDIRECT_URI` and `GOOGLE_BASE_URL` to the acknowledged
     non-secret set in `tests/test_sentry_init.py` (`NOT_SECRETS`), each as a
     decision.
3. **Make the routes.** These keep every property of today's `/login` and
   `/callback`: refuse cross-site starts, require consent, compare state,
   regenerate the session id on success, leave the session untouched on
   failure (apart from the flow's own state entry, which is single-use), and
   limit each address to 10 requests a minute (#139).
   - **The state compare** is `hmac.compare_digest` on the stored and the
     returned state **as UTF-8 bytes**, as `routes/core.py:128-130` does since
     #135. `compare_digest` raises on a non-ASCII `str`, which was #124's 500;
     a non-ASCII `state` must reach the invalid-state notice instead. Both new
     callbacks (Google here, and the stopgap Spotify one) need it, and the
     Google port can't copy a comparison to preserve: storydump keeps its state
     in a database table.
   - **The per-address limit.** #139 applies
     `_limiter.limit("10/minute", key_func=get_remote_address)` to the views
     `auth.login` and `auth.callback` by name (`shuffify/__init__.py:937-947`).
     The Google callback, `/spotify/connect` and `/spotify/callback` are new
     view functions, so register the same limit for all three next to those
     two, and add a test that fails when a sign-in, connect or save-app view
     has no registered limit, so a missed registration can't ship.
     `auth.callback` (the old Spotify sign-in return) keeps its limit until
     phase 06 deletes it. If #145 has landed and replaced these per-endpoint
     limits with a `RATE_LIMITS` table, the three new views need rows there
     instead, and phase 06 removes `auth.callback`'s row; merging main in
     shows it.
   - `GET /login` is now Google sign-in. Its state is kept under the session
     key `google_state`, and the nonce is derived from it. Spotify's flow
     uses a different key, `spotify_connect` (phase 06), so neither flow can
     consume or overwrite the other's state. `oauth_state` is retired.
   - `GET /auth/google/callback`:
     - takes `google_state` out of the session first, on every outcome: a
       state is used once;
     - finds the person by `google_sub`, or creates them and a first
       workspace named "My workspace". A second first sign-in that races this
       one (another tab) loses on `uq_users_google_sub`: the callback catches
       that `IntegrityError` by the constraint's name and signs in the person
       the other request created, never a 500;
     - sets `session["user_id"]` and `session["workspace_id"]` (that
       person's first workspace);
     - records login history and activity, describing them by internal ids
       only: no log line or activity text contains the email or display name
       (#138);
     - redirects to `/`.
   - **Add `/auth/google/callback`, `/spotify/connect` and `/spotify/callback`
     to the Vercel forwarding rule** (`web/scripts/build-vercel-output.mjs:56-57`; the
     current pattern matches single segments, so these two-segment paths
     need their own alternatives; `/callback` stays until phase 06 deletes it) **and to the harness preview proxy**
     (`tests/frontend_preview.py:13`). Without them the browser never reaches
     the API for any of them.
   - **`GET /spotify/connect` and `GET /spotify/callback`, as a stopgap until
     06 (F13).** The redirect URL is `PUBLIC_ORIGIN` plus `/spotify/callback`;
     the site app needs it registered (phase 01b adds it, and staging adds its
     own). `/spotify/connect` requires a signed-in person, refuses a
     cross-site start like `/login`, stores `{state, workspace_id}` under
     `spotify_connect`, and redirects to Spotify with the site app.
     `/spotify/callback` requires a signed-in person, takes `spotify_connect`
     out of the session first (it is used once, whatever the outcome),
     compares that state (as bytes, above), and attaches the Spotify
     connection to that person's
     workspace. **It never calls
     `session.clear()`:** a signed-in person stays signed in, and only
     `spotify_connect` and the Spotify token entries change. Phase 06
     replaces the stopgap's body with the per-workspace PKCE flow and keeps
     the paths. The old `/callback` is no longer reachable from any start,
     because `/login` is Google; phase 06 deletes it.
   - **The redirect URI the stopgap sends.** Until phase 06, `credentials_for`
     still builds from config (phase 02), so the stopgap's authorize URL sends
     the `SPOTIFY_REDIRECT_URI` setting. The harness sets it to its own
     `/spotify/callback`, the URI the fake Spotify registers. The harness is
     the only place the stopgap runs: staging doesn't stand up until phase 06,
     which deletes the setting. `/api/session`'s `setup.redirect_url` is
     `PUBLIC_ORIGIN` plus `/spotify/callback` and is the same value in the
     harness (tested). From phase 06 both come from `PUBLIC_ORIGIN` plus the
     path.
   - **The notice table, Google sign-in.** Today's notices were written for
     Spotify sign-in, and four of them name Spotify
     (`shuffify/services/auth_notice.py:3-11`). The list is closed: the web
     rejects a session that carries a code it doesn't list. Google sign-in
     gets its own text for every code it can produce, and each lands on the
     signed-out panel. The texts are proposals; the final copy follows the
     mockups.

     | Code | When | What the person reads |
     |---|---|---|
     | `access_denied` | Cancel on Google's screen | "Google sign-in was cancelled, so you haven't been signed in." |
     | `invalid_state` | The state is missing or doesn't match | "That sign-in attempt expired. Start again." |
     | `missing_code` | Google returned no code | "Google didn't finish signing you in. Try again." |
     | `oauth_failed` | The code exchange or the ID token check failed | "Google sign-in did not complete. Try again." |
     | `authentication_failed` | Sign-in failed for another reason | "We couldn't sign you in. Try again." |

     None of them mentions Spotify. Where a code is shared with the Spotify
     connect flow (phase 06 step 4), each callback picks the text for its own
     flow. Every code the two flows use is in `authNoticeCodes`
     (`web/src/api/contracts.ts`) and in the generated session schema, in the
     PR that introduces it.
4. **Change session helpers.**
   - `require_session_and_db` and its relatives resolve the person and the
     workspace from the session.
   - `require_auth` (anything that calls Spotify) also requires a connected
     Spotify connection. Without one, the route returns
     `spotify_not_connected`: a 401 that carries that error code and
     `retryable: false`, so an open page re-reads `/api/session` (phase 06
     step 1). With one, it builds the client from the stored
     refresh token whenever the session has no usable access token (phase
     02b), so a person who signs in with Google again, in a new session,
     isn't "not connected" while their connection is healthy.
   - The rate-limit key becomes `user:{user_id}`.
   - `/api/session` returns:
     - `signed_in`;
     - `user {id, display_name, image_url}`;
     - `workspace {id, name}`;
     - `setup {spotify_app: none|unchecked|ok|broken, spotify_connected: bool, redirect_url}`,
       where `redirect_url` is `PUBLIC_ORIGIN` plus `/spotify/callback`, so the
       web app shows it without hard-coding a domain (phase 07 needs it);
     - `csrf_token` and `notice`.

     Update the session shape everywhere it is pinned: the session types and
     `authNoticeCodes` in `web/src/api/contracts.ts`,
     `web/schemas/session-context.schema.json`,
     `tests/contracts/test_page_contracts.py` and `assertSession` in
     `web/src/api/client.ts`. The new shape keeps `csrf_token` for a person
     who is signed in but not connected: the save-app `PUT` needs it.
5. **Update the web app.**
   - The landing page and the signed-out panel say "Continue with Google".
   - One line by that button says what's needed: a Google account, Spotify
     Premium and about ten minutes. Setup step 1 still states the Premium
     requirement; this says it before sign-in creates the person and the
     workspace.
   - The consent checkbox stays as it is.
   - Add `https://accounts.google.com` to CSP `form-action`
     (`web/security-headers.ts`, plus the rewrite in `frontend_preview.py`).
   - The legal copy describes bring-your-own Spotify app, and where it
     describes removing access it says "the app you created for Raccoon";
     update the hash pins. The privacy copy that describes Google sign-in
     waits until #123.
6. **Logout** clears the whole session, as it does today.
7. **A follow-up migration makes `users.google_sub` NOT NULL.** It first
   deletes the people whose `google_sub` is null, removing their
   `login_history` rows before them (that foreign key has no `ON DELETE`;
   their workspaces go by cascade). Those people exist only on branch
   development databases, where phase 04's sign-in stopgap created them, and
   phase 04's guard means this delete can never meet production's data.
   Phase 04's stopgap (the old Spotify `/callback`) is no longer reachable,
   because `/login` is Google. The only stopgap left attaches a Spotify
   connection to the signed-in person's workspace, as step 3 describes. Phase
   06 replaces it.
8. **Build a fake Google in the harness:** `harness/fake_google/`, on its
   own loopback port.
   - It has `/o/oauth2/v2/auth` with a `fake_user=` shortcut, `/token`
     (which returns an unsigned ID token with the correct claims) and seeded
     people.
   - `HarnessConfig.assert_safe` requires `GOOGLE_BASE_URL` on loopback and
     the fake Google client credentials, and the netguard allows the port.
   - `check_login` and `App._log_in` sign in through fake Google.
   - Tests that need Spotify use the stopgap `/spotify/connect` and
     `/spotify/callback` against the fake Spotify until phase 06 replaces
     them. The fake's registered redirect URI is the harness's
     `/spotify/callback`.

## Test Plan
- **Unit tests for `google_oidc`,** ported from storydump: a wrong `iss`,
  `aud`, nonce or `exp`, or a missing `sub`, is refused by name; an
  unverified email isn't stored as verified.
- **Route tests:**
  - a cross-site start is refused;
  - missing consent is refused;
  - a state mismatch is refused;
  - **a non-ASCII `state`** gets the invalid-state notice and not a 500, on
    `/auth/google/callback` and on the stopgap `/spotify/callback` (modelled
    on `test_non_ascii_state_gets_the_invalid_state_notice` in
    `tests/routes/test_core_routes.py`);
  - the first sign-in creates a person and a workspace;
  - a second sign-in reuses them;
  - a failed callback leaves the session as it was, apart from the flow's
    state entry.
- **Scrubbing:** a `_strip_pii` test puts the configured
  `GOOGLE_CLIENT_SECRET` value into an exception message, a breadcrumb and a
  request body, and asserts it is redacted in each; and
  `TestSecretPolicyDoesNotRot` passes with the new settings.
- **Logs:** a sign-in test asserts the captured logs and the activity row
  contain no email address or display name.
- **The per-address limit:** the 11th request in a minute from one address
  to each of `/login`, `/auth/google/callback`, `/spotify/connect` and
  `/spotify/callback` returns 429, and a different address is unaffected.
- **Signing in again:** with a connection that is `ok` and has a stored
  refresh token, a person who signs out and signs in with Google loads their
  playlists without reconnecting Spotify.
- **Session keys:** a Google callback with a `spotify_connect` entry in the
  session leaves it alone, and a Spotify callback with a `google_state` entry
  leaves that alone; a stopgap `/spotify/callback` never signs the person out.
- **`/api/session`** contract tests, including `setup.redirect_url` and that
  it equals the redirect URI the stopgap sends in the harness; the web
  contract tests and Playwright session mocks updated to the new shape.
- **Harness e2e:** sign in through fake Google and land on the app with
  `setup.spotify_app = none`.
- **The state is used once:** after any `/auth/google/callback`, successful
  or not, `google_state` is gone, and replaying the same callback gets the
  invalid-state notice. The same holds for `spotify_connect` on the stopgap
  `/spotify/callback`.
- **Two first sign-ins at once** for one `google_sub` (two tabs) leave one
  person and one workspace, and neither request is a 500.
- **Notices:** Cancel on the fake Google's screen shows the Google
  `access_denied` text; no Google sign-in notice mentions Spotify; every code
  is in `authNoticeCodes` and the session schema (the contract test).
- **The override:** a `GOOGLE_BASE_URL` that isn't a bare loopback origin is
  refused, any value is refused at boot in production, and an `iss` other
  than the two accepted forms is refused.
- **Scrubbing, the flow's other material:** `tests/test_sentry_egress.py`
  cases put an `id_token` and a PKCE verifier into an event, by key and (for
  the ID token) by shape, and assert each is redacted.
- **Every view has a limit:** a test walks the sign-in and connect views (and
  the save-app view from phase 06) and fails if one has no registered limit.
- **Migration test,** in the per-revision pattern (phase 04, Evidence): a
  schema from before this revision with a person whose `google_sub` is null,
  and their login history, upgrades; the person is gone and the column is NOT
  NULL.

## Verification Checklist
- [ ] The harness `check_login` passes through fake Google.
- [ ] `git grep -n "oauth_state" -- shuffify` returns nothing.
- [ ] `git grep -n "10/minute" -- shuffify/__init__.py` shows the per-address
      limit applied to `auth.login`, `auth.callback` and the three new views
      (or, if #145 has landed, the same rows in its `RATE_LIMITS` table).
- [ ] `git grep -n "GOOGLE_CLIENT_SECRET" -- shuffify/__init__.py` shows it in
      `_SENTRY_SECRET_CONFIG_ATTRS`.
- [ ] `git grep -n "accounts.spotify.com" -- web/security-headers.ts` still
      matches (the Spotify connect flow needs it) next to
      `accounts.google.com`.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't key identity on email.
- Don't verify ID tokens with a JWKS fetch (F3).
- Don't drop any of the existing sign-in protections, including #139's
  per-address limit and the bytes comparison of the state (#135).
- Don't call `session.clear()` in any Spotify route.
- Don't put an email or display name in a log line or activity text.
- Don't build workspace switching. The session always holds the person's
  first workspace.

## Context
- Source skill: forge · Area: routes, session, web auth, harness · Effort: L · Risk: High · Priority: High
