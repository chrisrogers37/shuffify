---
title: "[plan] One credential seam, failures that keep their cause, one refresh token — auth, worker"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, auth]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: XL, shipped as two
PRs of size L each, with a check against real Spotify between them. There's no
visible change. This phase does three things:
- routes every place that gets Spotify app credentials through one function;
- makes Spotify's failures keep their cause (revoked access, rejected app,
  account not allowed) all the way to the code that will act on it, without
  changing which handlers catch them today, and gives the client a hook that
  is called where a typed failure is raised, so phase 08 can detect a broken
  app without relying on the failure surviving the call stack;
- makes the database the only home of the refresh token: the web and the
  worker both refresh inside a short row-locked transaction, and the session
  stops keeping its own copy.

It ships as two PRs so the riskier half can be reverted alone:
- **02a, the credential seam and typed failures** (steps 1 to 6): low
  risk, nothing users see changes.
- **A check against real Spotify** (step 7, the owner, nothing merged): run
  before 02b is built.
- **02b, the refresh-token move** (step 8): changes how the live site's web
  requests stay signed in, on main, where every merge deploys production.
  It is revertible without touching 02a.

Phases 06 and 08 build on all three, and they're safe on main today.

## Evidence

**Where credentials are read:**
- `shuffify/services/auth_service.py:66-82`: `_credentials()` and
  `_auth_manager()`, which fall back to `from_env` at L76-77;
- `shuffify/services/executors/base_executor.py:95-96`;
- docstring examples in `shuffify/spotify/api.py:86`, `auth.py:149` and
  `spotify/__init__.py:28`.

**What failures lose:**
- `shuffify/spotify/auth.py:302-312` (refresh) and L247-249 (exchange)
  raise a generic `SpotifyTokenError` and drop the OAuth `error` field.
- `shuffify/spotify/http_client.py:322-337` logs the refresh failure and
  raises `SpotifyTokenExpiredError("Token expired or invalid")`, losing the
  cause.
- A 403 always becomes `SpotifyForbiddenError` (L410).
- Spotify's "not registered" text is already matched at
  `shuffify/routes/core.py:220`.
- The worker wraps a failed refresh in `JobExecutionError`
  (`base_executor.py:135-136`, `raise ... from e`) and the web wraps it in
  `AuthenticationError` (`auth_service.py:222-224`, with no `from`). Nothing
  that catches those two reads the cause.

**Typed failures don't survive the call stack.** `SpotifyUserNotAllowed` will
subclass `SpotifyForbiddenError`, and services catch that class, or
`SpotifyError`, long before a Flask handler could see it:
- `playlist_service.py:235, 303`, `workshop_commit_service.py:115, 140, 169,
  251, 392` and `workshop_finalize_service.py:374` re-map a 403 to
  `PlaylistAccessError`;
- `routine_activation_service.py:51, 154, 351, 382, 482, 557` and
  `playlist_setup_service.py:990, 1085, 1138, 1185` catch `SpotifyError`
  broadly and turn it into a 503 or a per-item error.

Flask picks a handler by the class that reaches it, and a 403 raised at
`http_client.py:410` never passes through a token refresh at all, so nothing
on that path could attach a credential generation to it either. Detection has
to happen where the failure is raised (step 3), not in a handler.

**The existing hierarchy** (`shuffify/spotify/exceptions.py`):
- `SpotifyError` → `SpotifyAuthError` → `SpotifyTokenError` →
  `SpotifyTokenExpiredError`;
- `SpotifyError` → `SpotifyAPIError` → `SpotifyForbiddenError`;
- `SpotifyAPIError` → `SpotifyReadTransientError`, the repo's transient
  family. It is recognised by class in three places: the 401 retry's
  re-raise tuple (`http_client.py:332`, which lists only the deadline,
  rate-limit and read-transient classes, so any other refresh failure becomes
  "Token expired or invalid"), a 5xx from the token endpoint
  (`auth.py:308-309`), and the worker's `read_retry_delay`
  (`shuffify/services/operations/control.py:6-28`), which defers a run only
  for rate-limit, read-transient and deadline errors, found along the cause
  chain (`worker.py:204`).

Catchers that must keep matching:
- `except SpotifyTokenError` in `auth_service.py:222` and `auth.py:260, 327`;
- `SpotifyTokenExpiredError` in `http_client.py:442` (re-raised untouched)
  and `handle_spotify_token_expired` (`error_handlers.py:329, 621`);
- `SpotifyForbiddenError` handling for playlist 403s.

**Two copies of the refresh token.**
- The worker takes `credential:{user_id}` for the whole run
  (`shuffify/worker.py:94-95`, with a deadline of up to 240s at `worker.py:33`)
  and stores a rotated token in the database (`base_executor.py:112-125`). That
  closure also keeps a rotated token in memory when the save fails, so the
  next refresh in the same run doesn't use a token Spotify has already
  invalidated.
- A web refresh reads the refresh token from the session, and
  `_persist_token_to_session` (`auth_service.py:230-255`) writes only the
  session. The database copy goes stale, and so does the session's after a
  worker rotation. A PKCE refresh can invalidate the old token (phase 06), so
  one side would then fail with `invalid_grant`.
- Sign-in already stores the database copy (`routes/core.py:175`,
  `UserService.store_refresh_token(user, refresh_token)`).

**Why the existing lock can't protect the token.**
- `OperationRepository.target_locks` takes session-level advisory locks on the
  repository's engine (`shuffify/services/operations/repository.py:509-543`).
  Production's api and worker both connect through Neon's pooled host
  (checked read-only 2026-11-10, although the guides describe the direct
  host). A session advisory lock is not reliable through a transaction-mode
  pooler, so it can't be what stops two refreshes racing. A transaction-scoped
  row lock is correct through any pooler.
- `target_locks` waits 2 seconds by default, and the worker holds
  `credential:{user_id}` for a whole run. A web refresh waiting on that lock
  would time out during any long run.
- `get_authenticated_api` turns every non-deadline error into
  `AuthenticationError` (`auth_service.py:225-227`), and `require_auth`
  (`routes/__init__.py:60-73`) turns that into "signed out". A lock or database
  timeout would therefore sign the person out.

**What the repo already does for a row lock and its timeout.**
- `_lock_user` (`shuffify/services/upstream_source_service.py:42-50`) locks a
  `users` row with `.with_for_update(key_share=True)`, which is `FOR NO KEY
  UPDATE`: "NO KEY UPDATE conflicts with itself but not with inserts that
  reference the user, such as activity log rows." Thirteen tables have an FK
  to `users.id`, so a plain `FOR UPDATE` on that row would make those inserts
  wait for the lock.
- The repo sets a lock timeout inside the transaction, never on the session:
  `set_config('lock_timeout', :timeout, true)` at
  `shuffify/services/operations/repository.py:540`, and `SET LOCAL
  lock_timeout` at `shuffify/services/playlist_lock.py:190-193` ("without
  LOCAL the timeout bleeds into the pool").

**What #143 left in the session** (7b56eaa). Live sessions carry
`encrypted_refresh_token`, not `refresh_token`:
- `encrypt_session_token` and `decrypt_session_token`
  (`auth_service.py:32-58`);
- the upgrade path in `is_authenticated` that re-encrypts a plaintext token
  (`routes/__init__.py:46-56`) and `require_auth`'s decrypt call;
- sign-in's write (`routes/core.py:151`) and `_persist_token_to_session`
  (`auth_service.py:251`);
- `tests/routes/test_session_refresh_token.py`, which asserts the behaviour
  this phase removes;
- the comment at `shuffify/worker.py:94` ("User credential serialization also
  protects rotated refresh tokens between workers"), which stops being true.

**The session doesn't reliably carry the user's row id.** `_db_user_id` is a
lazy cache written only by `get_db_user()`'s slow path
(`shuffify/routes/__init__.py:248-258`). In `require_auth_and_db`,
`require_auth()` runs before `get_db_user()` (`routes/__init__.py:159, 169`),
and the sign-in callback doesn't write it (`routes/core.py:150-170`).

## Implementation Plan

### Dependencies
None. 02b follows 02a, and step 7 runs between them.

### Blocks
03, 04 (re-points the refresher), 06, 08.

### Steps

**02a: the credential seam and typed failures**

1. **Add `shuffify/spotify/credentials.py: credentials_for(owner) -> SpotifyCredentials`.**
   - In this phase it returns `SpotifyCredentials.from_flask_config(current_app.config)`
     and raises `SpotifyAppUnavailable` when the ID or secret is empty.
   - Remove `from_env` (`credentials.py:73-89`) and the fallback in
     `auth_service.py:76-77`. Update the three docstring examples to use
     `credentials_for`.
   - `owner` is the `User` today; phase 06 changes it to the workspace's
     Spotify connection and drops the secret.
2. **Give `AuthService.get_auth_url`, `exchange_code_for_token`,
   `get_authenticated_api` and `revoke_access_token` an explicit credentials
   argument.** Routes get the credentials from `credentials_for`.
   `base_executor.py:95` calls `credentials_for(user)`.
3. **Add typed failures in `shuffify/spotify/exceptions.py`, under the
   existing classes.** Each keeps the OAuth `error` and `error_description`:
   - `SpotifyAppRejected` (`invalid_client`) subclasses
     `SpotifyTokenExpiredError`;
   - `SpotifyGrantRevoked` (`invalid_grant`) subclasses
     `SpotifyTokenExpiredError`;
   - `SpotifyUserNotAllowed`, a 403 whose body says the user isn't
     registered for the app, subclasses `SpotifyForbiddenError`.

   The first two are "a token that can't be refreshed", so
   `except SpotifyTokenExpiredError` and `except SpotifyTokenError` keep
   matching them. The third is still a 403, so existing 403 handling matches;
   new code tests for the subclass first. None of them subclasses
   `SpotifyAuthError` alone.

   Raise them from `auth.py` (refresh and exchange) and from
   `http_client.py`. In the 401 retry, add `SpotifyAppRejected` and
   `SpotifyGrantRevoked` to the re-raise tuple (`http_client.py:332`), so the
   refresh's typed failure reaches the caller instead of a generic
   `SpotifyTokenExpiredError`. Playlist-level 403s stay
   `SpotifyForbiddenError`.

   **Add a failure hook, called where the failure is raised.**
   `SpotifyHTTPClient` and `SpotifyAPI` accept an optional
   `on_typed_failure(failure)` callable, wired like `on_token_refresh`
   (`SpotifyAPI` hands it to the `SpotifyHTTPClient` it builds).
   - The HTTP client calls it once, with the failure, immediately before it
     raises `SpotifyUserNotAllowed` (`http_client.py:410`), and never for a
     playlist 403.
   - A hook that raises is logged and swallowed, so it can't change the
     failure the caller sees. With no hook, nothing happens.
   - Nothing in phase 02 supplies a hook. Phase 08 builds one per client from
     the connection, capturing the connection's `credential_generation` when
     the client is built, and calls `mark_broken` from it. The refresh
     failures get the same hook in 02b, from the refresher.
   - This is how a broken app is seen even when a service catches the failure
     (Evidence): the hook fires at the raise, before any catch.
4. **Carry the cause through both wrappers.**
   - `JobExecutionError` and `AuthenticationError` gain a `cause` attribute
     set from the Spotify failure that triggered them, and `auth_service.py`
     raises its `AuthenticationError` `from` the original. Both keep their
     messages and base classes.
   - Add `spotify_failure_of(exc)`, which returns the first
     `SpotifyAppRejected`, `SpotifyGrantRevoked` or `SpotifyUserNotAllowed`
     found on `exc` or its `cause`/`__cause__` chain, or `None`. The worker's
     error codes (step 5) and the web's response (phase 08) use it; nothing
     parses messages. It does not carry a credential generation: the hook
     does that job.
5. **Map the new failures to error codes** the worker records on a failed
   run: `spotify_app_rejected`, `spotify_grant_revoked` and
   `spotify_user_not_allowed`. In `operations/commands/routines.py:359-415`,
   use these in place of the generic `write_rejected` when
   `spotify_failure_of` finds one.
6. **Teach the fake Spotify to fail on demand.** In
   `harness/fake_spotify/app.py`, add `/_fake/faults` modes that return
   these three failures, so the harness can produce each one. Add a mode that
   makes the refresh endpoint rotate the refresh token and invalidate the
   previous one, as PKCE refreshes do, so 02b's tests can see a stale token
   fail. After step 7, make these modes match what real Spotify did.

**Between 02a and 02b: check real Spotify's PKCE behaviour** (the owner; a
throwaway script, not committed, with no token pasted anywhere)

7. **F8 (refresh with a client ID only) and F14 (a refresh may rotate the
   refresh token and invalidate the old one) rest on Spotify's
   documentation, and the fake Spotify enforces those same assumptions**, so
   no test could contradict them. The first real contact would otherwise be
   staging in phase 06, after four more phases. Before 02b is built, run a
   script against the existing Spotify app and write down what Spotify does:
   - authorize with PKCE (`code_challenge_method=S256`) and exchange the code
     with `client_id` and `code_verifier` in the body and no authorization
     header, using a redirect URI already registered on the app (phase 01b
     adds `https://raccoon.fm/spotify/callback`; if 01b hasn't happened yet,
     register a temporary one and remove it afterwards);
   - refresh twice with `client_id` only, and note whether each response
     carries a new `refresh_token`, and whether the previous refresh token
     still works once a new one has been issued;
   - refresh with a made-up refresh token and, separately, with a made-up
     client ID, and note the status and `error` code each returns (phase 08's
     matrix expects `invalid_grant` and `invalid_client`).

   Put the outcome in 02b's PR description. If Spotify refuses a
   client-ID-only exchange or refresh, stop and take it to the owner: F8
   rests on it.

**02b: the refresh-token move** (its own PR, revertible on its own)

8. **Make the database the only home of the refresh token.** Spotify
   rotates a PKCE refresh token and may invalidate the old one, so a refresh
   must read the current stored token, refresh, and store the new one as one
   serialised step. That step is a short database transaction, never a lock
   around a whole run.
   - **A refresher around `SpotifyAuthManager`** that `get_authenticated_api`
     and the worker's `_get_spotify_api` hand to `SpotifyAPI` in place of the
     bare auth manager (`SpotifyAPI` only calls `refresh_token` and
     `ensure_valid_token`, `api.py:141, 187`). Each refresh, in its own
     session that commits before the refresh returns:
     1. begins a transaction and sets the lock timeout **for that transaction
        only** (10 seconds): `SELECT set_config('lock_timeout', '10000ms',
        true)`, the pattern at `repository.py:540` (or `SET LOCAL`, as
        `playlist_lock.py:190-193`). Never a session-level `SET`: production's
        api and worker connect through Neon's pooled host, where a session
        setting stays on the server connection and reaches whichever client
        gets it next;
     2. locks the row that holds the encrypted refresh token with `FOR NO KEY
        UPDATE`, `.with_for_update(key_share=True)`, as `_lock_user` does
        (`upstream_source_service.py:42-50`): the `users` row on main, and the
        `spotify_connections` row once phase 04 moves the columns and drops
        them from `users`. A plain `FOR UPDATE` would make every insert that
        references the user wait for the Spotify call, and a refresh issued
        from a request or run whose own open transaction has already inserted
        such a row would wait on that transaction until the timeout;
     3. reads and decrypts the stored token, and refreshes with it;
     4. stores the rotated token, encrypted, and commits, which releases the
        row lock.
   - **The row lock is the only protection for the token.** It is correct
     through Neon's pooled host, because the lock lives inside one
     transaction. The worker's `credential:{user_id}` advisory lock stays for
     what it was for, keeping one user's runs from overlapping, but the
     refresher doesn't take it and nothing relies on it to protect the token.
     A web refresh therefore never waits on a worker run. Rewrite the comment
     at `worker.py:94` to say so.
   - **A failed save keeps the rotated token, within that process.** If
     Spotify has rotated the token and the commit fails, retry the commit
     once. If it still fails, keep the rotated token in memory in the
     refresher (keyed by the row) and use it for the next refresh while the
     stored token is still the one it replaced, retrying the store each time.
     This replaces the closure at `base_executor.py:112-125`. The save
     failure itself is transient and is logged.
     - **The guarantee holds per process.** The kept token is invisible to
       the other process: if the web process rotates a token and can't store
       it, the worker's next refresh reads the stale stored token and gets
       `invalid_grant`, which phase 08 marks as `access_revoked`, pausing the
       workspace's schedules until one reconnect. The trigger is rare (a
       commit failing right after a successful locked read and refresh), and
       nothing more is built for it. Don't test for more than the
       process-local guarantee.
   - **A lock or database timeout is transient, never "signed out".**
     Add `SpotifyRefreshUnavailable` as a subclass of
     `SpotifyReadTransientError`, so it is a `SpotifyAPIError` and outside
     `SpotifyAuthError`, `SpotifyTokenError` and `SpotifyTokenExpiredError`.
     It is handled as transient in the three places that recognise that
     family:
     - **The 401 retry** (`http_client.py:322-337`) re-raises it instead of
       turning it into "Token expired or invalid". The subclass already
       matches the existing `SpotifyReadTransientError` entry; add
       `SpotifyRefreshUnavailable` to the tuple by name as well, so a later
       change to that tuple can't turn a lock timeout back into a sign-out.
     - **The web path.** `get_authenticated_api` re-raises it untouched, as it
       does `SpotifyDeadlineError`, instead of letting `except Exception`
       (`auth_service.py:225-227`) wrap it in `AuthenticationError`.
       `require_auth` lets it through. A Flask handler registered for
       `SpotifyRefreshUnavailable`, modelled on `handle_spotify_rate_limit`
       (`error_handlers.py:336`), returns 503 with `Retry-After`, never 401,
       and leaves the session as it is. It is more specific than
       `handle_spotify_api_error` (`error_handlers.py:383, 626`), so it wins.
     - **The worker.** `read_retry_delay` recognises
       `SpotifyReadTransientError` by class along the cause chain, so a run
       whose refresh times out is deferred under the existing read-retry
       bounds (`worker.py:204`) instead of ending `failed`. `read_retry_delay`
       needs no change; the worker test below proves it.
   - **The refresher calls the failure hook** (step 3; it takes the same
     optional `on_typed_failure` callable) for
     `SpotifyAppRejected` and `SpotifyGrantRevoked`, with the failure, after
     its transaction has ended and the row lock is released: phase 08's
     `mark_broken` updates the same row in its own transaction and would
     otherwise wait for the lock its own caller still holds. No hook, nothing
     happens. The bare `SpotifyAuthManager`, which the connect callback uses
     for the code exchange (phase 06), has no hook.
   - **The access token is minted on demand.** The session's access token is
     a cache. When the session has no access token, or it has expired, build
     the client from the stored refresh token with an already-expired access
     token so it refreshes on first use, as `_get_spotify_api` does for the
     worker. A person with a healthy stored token is never treated as "not
     connected" because the session lacks an access token; without a stored
     token, the request is signed out. This is what lets a fresh Google
     sign-in (phase 05) work without reconnecting Spotify.
   - **The web path resolves the signed-in user's row id itself.** Don't rely
     on `_db_user_id`: it is only a lazy cache, and `require_auth()` runs
     before `get_db_user()` writes it (Evidence). When it is absent, look the
     user up the way `get_db_user()` does (the Spotify id in
     `session["user_data"]`), cache the id in `_db_user_id`, and use it. A
     session with no resolvable row is "no stored token", that is, signed out.
   - **Remove #143's session refresh-token handling**, which the session no
     longer needs:
     - delete `encrypt_session_token` and `decrypt_session_token`
       (`auth_service.py:32-58`);
     - delete the upgrade path in `is_authenticated`
       (`routes/__init__.py:46-56`) and make it drop both `refresh_token` and
       `encrypted_refresh_token` from a live session that still carries one;
     - stop writing a refresh token at sign-in (`routes/core.py:151`) and in
       `_persist_token_to_session`, which writes the access token and its
       expiry only;
     - delete `tests/routes/test_session_refresh_token.py` and cover the
       replacement behaviour in the tests below.


## Test Plan

**02a**
- Unit tests:
  - each OAuth error body maps to its typed exception;
  - a typed refresh failure is still caught by `except SpotifyTokenError`
    and `except SpotifyTokenExpiredError`, and `handle_spotify_token_expired`
    still returns its 401; a 403 "not registered" body is caught by
    `except SpotifyForbiddenError` and maps to `SpotifyUserNotAllowed`, while
    a playlist 403 stays `SpotifyForbiddenError`;
  - a 401, then a failed refresh, re-raises the typed cause;
  - `spotify_failure_of` finds the cause through `JobExecutionError` and
    `AuthenticationError`, and returns `None` for an unrelated error;
  - `credentials_for` raises `SpotifyAppUnavailable` with empty config.
- **The failure hook:** a recording hook passed to `SpotifyAPI` is called once,
  with the failure, when a 403 "not registered" raises
  `SpotifyUserNotAllowed`; it is not called for a playlist 403; a hook that
  raises doesn't change the exception the caller gets; with no hook nothing
  changes.
- **Through a catching service:** with a recording hook, a "not registered"
  403 raised under `PlaylistService.get_playlist` (which re-maps
  `SpotifyForbiddenError` to `PlaylistAccessError`) still calls the hook once.
- A worker test: a run whose refresh fails with `invalid_client` ends
  `failed` with `spotify_app_rejected`.

**02b** (real Postgres, as the operation lock tests use)
- A web refresh after a worker rotation uses the rotated token, and a web
  refresh stores its rotated token for the worker to use.
- Two refreshes at once against the fake Spotify, which invalidates the
  previous refresh token on rotation: both succeed, one after the other, and
  neither gets `invalid_grant`.
- **A refresh during a long worker run:** with the worker holding
  `credential:{user_id}` for several seconds, a web refresh still succeeds
  and the person is not signed out.
- **The lock is transaction-local and doesn't block child inserts:**
  - `SHOW lock_timeout` on a pooled connection is unchanged after a refresh
    (and after one that timed out);
  - while a refresh holds the row lock, an insert into a table that references
    the user (an activity row) for the same user doesn't wait.
- **A lock timeout is transient, on every path:** with another transaction
  holding the row lock past `lock_timeout`, the refresh raises
  `SpotifyRefreshUnavailable` and
  - at client construction, the request returns 503 with `Retry-After` and not
    401, and the session is unchanged;
  - **inside a 401 retry** (the access token is rejected mid-request, then the
    refresh times out), the request returns 503 and not 401 "Session
    expired", and the session is unchanged;
  - **in the worker,** a run whose refresh times out is deferred through
    `read_retry_delay` and not recorded `failed`, and
    `read_retry_delay(SpotifyRefreshUnavailable(...))` is not `None`.
- **A failed save keeps the token:** make the store fail once after Spotify
  rotates the token. The next refresh in the same process uses the in-memory
  token and gets no `invalid_grant`, and the stored token catches up.
- **The user's row id:** a session that has `user_data` and an expired access
  token but no `_db_user_id` refreshes in `require_auth` and then has
  `_db_user_id` set; a session whose user has no row is signed out.
- **Sign-out and back in:** a session with a user id and no access token, and
  a healthy stored refresh token, loads data without reconnecting; the same
  session with no stored token is signed out.
- **The hook runs after the lock:** a refresh that fails with `invalid_grant`
  calls a hook that updates the same row in a second transaction, and the
  update doesn't wait for `lock_timeout`.
- The session never holds a refresh token, plaintext or encrypted, after
  sign-in or any request, and a live session that still carries one has it
  dropped on its next request.
- The design depends only on transaction-scoped state (the row lock and the
  transaction-local timeout), so these tests on plain Postgres cover Neon's
  pooled host too.
- The existing suites stay green.

## Verification Checklist
- [ ] **02a:** `git grep -n "from_env\|SpotifyCredentials(" -- shuffify ':!shuffify/spotify/credentials.py'`
      returns nothing: only `credentials_for` builds credentials.
      `from_flask_config` is deliberately kept here (it's the body of
      `credentials_for`); phase 06 deletes it.
- [ ] **02a:** each of the three faults in the fake Spotify produces its error
      code in the harness.
- [ ] **Step 7:** the observed behaviour (rotation, and the error codes for a
      bad refresh token and a bad client ID) is written in 02b's PR
      description, and the fake Spotify's modes match it.
- [ ] **02b:** `pytest tests/services/test_credential_refresher.py tests/routes/test_session_holds_no_refresh_token.py`
      passes (both files exist and run their tests).
- [ ] **02b:** `git grep -n "encrypt_session_token\|decrypt_session_token" -- shuffify`
      returns nothing, and `tests/routes/test_session_refresh_token.py` no
      longer exists. (`encrypted_refresh_token` still appears once, in
      `is_authenticated`'s drop of a live session's old key; the session test
      above covers that.)
- [ ] **02b:** `git revert` of the 02b merge alone leaves 02a's seam and typed
      failures working (a revert dry run on the branch before merging). A
      revert is a code revert: a session that has already lost its refresh
      token signs in once more at its next expiry.
- [ ] After 02b deploys, the production logs show no `invalid_grant` and no
      503 from a refresh over the next day of scheduled runs. That day is the
      only run through the pooled host before staging.
- [ ] CI is green on each PR.

## What NOT To Do
- Don't add any workspace concept yet.
- Don't change what users see.
- Don't change which handlers catch today's exceptions; new types subclass
  the specific existing ones.
- Don't treat every 403 as an app failure; playlist 403s are ordinary.
- Don't read a refresh token from the session or hold one in a closure
  across a refresh.
- Don't rely on an advisory lock to protect the refresh token, and don't hold
  a lock around a whole run for it. The refresh is its own short transaction.
- Don't use a plain `FOR UPDATE` or a session-level `SET lock_timeout` for it.
- Don't let a lock or database timeout reach `AuthenticationError`: it is not
  "signed out". Don't move `SpotifyRefreshUnavailable` out of the transient
  family.
- Don't call the failure hook while the row lock is held.
- Don't rely on `_db_user_id` being set.
- Don't mix 02a and 02b in one PR.

## Context
- Source skill: forge · Area: shuffify/spotify, services/auth_service.py, executors · Effort: XL · Risk: Medium · Priority: High
