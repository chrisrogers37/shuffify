# AGENTS.md

General developer/architecture guidance for this repo lives in `CLAUDE.md`
(setup, commands, layering, models, algorithms). Read it first. This file only
adds Cursor Cloud specific notes.

## Cursor Cloud specific instructions

Shuffify is a single Flask web app (one process; APScheduler runs in-process).
Standard lint/test/build/run commands are documented in `CLAUDE.md` — use those.
Notes below are the non-obvious, environment-specific bits.

### Python env
- Dependencies live in a virtualenv at `/workspace/venv`. Activate it before any
  command: `source venv/bin/activate`. The startup update script refreshes it
  from `requirements/dev.txt`.

### Running the app
- Dev server: `source venv/bin/activate && python run.py` → serves on
  `http://localhost:8000` (binds `0.0.0.0`). Health check: `GET /health`.
- `APP_CONFIG` defaults to `development` (SQLite at `shuffify_dev.db`). `run.py`
  reads env, not `.env`, first; a `.env` file (git-ignored) is created during
  setup with dev config + placeholder Spotify credentials.

### No Redis / Postgres here — this is expected, not a failure
- Neither service runs in this environment. The app auto-falls back: filesystem
  sessions (`./.flask_session/`), no Spotify API caching, in-memory rate limiting,
  and SQLite instead of Postgres. The startup log lines "Redis connection
  failed... Falling back to filesystem sessions" and "Rate limiter using
  in-memory storage" are normal here.

### Spotify OAuth is the one thing you cannot fully exercise
- Login → pick playlist → shuffle in the browser requires real
  `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` from a Spotify Developer app plus
  a real Spotify account. These are not available by default, so `/login` just
  bounces back to `/` with placeholder creds. To test end to end, add those as
  secrets and set the dashboard redirect URI to `http://localhost:8000/callback`.
- The core product logic (the 7 shuffle algorithms in
  `shuffify/shuffle_algorithms/`) is fully testable without Spotify — import
  `ShuffleRegistry` and call `.shuffle(tracks)` on sample track dicts.
- Gotcha when testing OAuth: `GET /login` requires a `legal_consent` query param
  (set by the landing page's Terms checkbox). A bare `GET /login` flashes an
  error and redirects to `/`; use `/login?legal_consent=true` to reach the real
  Spotify authorize redirect. Completing the login itself needs a human Spotify
  account (reCAPTCHA/MFA on `accounts.spotify.com`) and cannot be automated.

### Tests
- `pytest tests/ -m "not integration"` runs without any env vars: `config.py`
  skips `load_dotenv()` under pytest and fixtures force `TestConfig`
  (in-memory SQLite, scheduler off). `@pytest.mark.integration` tests hit live
  public Spotify pages and need network; skip them unless intended.
- `./scripts/build-css.sh --check` (a CI gate) downloads a standalone Tailwind
  CLI binary from GitHub on first run, so it needs outbound network.
