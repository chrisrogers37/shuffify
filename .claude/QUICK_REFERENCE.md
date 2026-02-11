# Shuffify - Quick Reference for Claude

## CRITICAL SAFETY RULES

**NEVER run these commands** (they affect production):
- `docker-compose up -d --build`
- `git push origin main`

**SAFE commands** (local development):
- `python run.py` (dev server, port 8000)
- `pytest tests/ -v` (run tests)
- `flask routes` (view routes)
- `flake8 shuffify/` (linting)

---

## Architecture (4-Layer)

```
Routes → Services (10) → Business Logic → External APIs
```

**NEVER violate layer boundaries:**
- Routes call services (auth, playlist, shuffle, state, token, scheduler, job_executor, user, workshop_session, upstream_source)
- Services call business logic (algorithms, spotify client, models)
- Business logic calls external APIs (Spotify Web API, database)
- Templates only handle presentation

---

## Key Directories

| Path | Purpose |
|------|---------|
| `shuffify/` | Main application code |
| `shuffify/services/` | 10 service modules |
| `shuffify/schemas/` | Pydantic validation schemas |
| `shuffify/shuffle_algorithms/` | 7 shuffle algorithms (6 visible, 1 hidden) |
| `shuffify/spotify/` | Modular Spotify client (credentials, auth, api, cache) |
| `shuffify/models/` | Data models (playlist.py) + DB models (db.py) |
| `shuffify/templates/` | Jinja2 templates (5: base, dashboard, index, workshop, schedules) |
| `shuffify/static/` | CSS, JS, images |
| `tests/` | Test suite (690 tests) |
| `documentation/` | All markdown docs |
| `requirements/` | Dependencies (base, dev, prod) |

---

## Common Tasks

| Task | Command/Action |
|------|----------------|
| Run dev server | `python run.py` (port 8000) |
| Run tests | `pytest tests/ -v` |
| Tests with coverage | `pytest tests/ --cov=shuffify` |
| Check linting | `flake8 shuffify/` |
| Format code | `black shuffify/` |
| View routes | `flask routes` |
| Interactive shell | `flask shell` |
| Pre-push check | `flake8 shuffify/ && pytest tests/ -v` |

---

## Key Files to Know

| File | Contains |
|------|----------|
| `shuffify/__init__.py` | Flask app factory, Redis/DB/Scheduler init |
| `shuffify/routes.py` | All HTTP routes |
| `shuffify/services/` | 10 service layer modules |
| `shuffify/schemas/requests.py` | Pydantic validation schemas |
| `shuffify/schemas/schedule_requests.py` | Schedule CRUD validation |
| `shuffify/spotify/api.py` | Spotify API with retry logic + caching |
| `shuffify/spotify/auth.py` | OAuth flow, token management |
| `shuffify/spotify/client.py` | Facade for backward compat |
| `shuffify/shuffle_algorithms/registry.py` | Algorithm registration |
| `shuffify/models/db.py` | SQLAlchemy models (User, Schedule, JobExecution) |
| `shuffify/models/playlist.py` | Playlist data model |
| `shuffify/error_handlers.py` | Global exception handlers |
| `config.py` | Configuration classes (dev/prod) |
| `run.py` | Application entry point |

---

## Shuffle Algorithms (7 total)

| Algorithm | File | Description |
|-----------|------|-------------|
| **Basic** | `basic.py` | Random shuffle |
| **Balanced** | `balanced.py` | Round-robin from sections |
| **Percentage** | `percentage.py` | Keep top N% fixed |
| **Stratified** | `stratified.py` | Shuffle within sections |
| **ArtistSpacing** | `artist_spacing.py` | No back-to-back same artist |
| **AlbumSequence** | `album_sequence.py` | Keep album tracks together |
| **TempoGradient** | `tempo_gradient.py` | Sort by BPM *(hidden)* |

All in: `shuffify/shuffle_algorithms/`

---

## Session Flow

**OAuth** (via AuthService):
1. User clicks "Connect with Spotify"
2. `AuthService.get_auth_url()` → Spotify authorization
3. Callback receives code
4. `AuthService.exchange_code_for_token(code)` → TokenInfo
5. Store token in `session['spotify_token']`
6. Auto-refresh via SpotifyAuthManager when expired

**Undo** (via StateService):
- `session['playlist_states'][playlist_id]` = state history
- `StateService.record_new_state()` before shuffle
- `StateService.undo()` restores previous
- Clear on logout

**Scheduled Jobs** (via APScheduler):
- Schedule definitions stored in `Schedule` DB model
- Jobs executed by `JobExecutorService` with encrypted refresh tokens
- Execution history logged in `JobExecution` model

---

## Testing

```bash
# All tests (690 total)
pytest tests/ -v

# Specific test file
pytest tests/shuffle_algorithms/test_basic.py -v

# With coverage
pytest tests/ --cov=shuffify --cov-report=html
```

---

## Environment Setup

```bash
# Create .env with:
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://localhost:8000/callback
SECRET_KEY=your_secret_key
FLASK_ENV=development
```

Get credentials: https://developer.spotify.com/dashboard

---

## CHANGELOG Reminder

Every PR must update `CHANGELOG.md` under `## [Unreleased]`

---

## Layer Boundaries

```
CORRECT:
routes.py → services/shuffle_service.py → shuffle_algorithms/basic.py
routes.py → services/playlist_service.py → spotify/api.py → Spotify API
routes.py → services/auth_service.py → spotify/auth.py
routes.py → services/scheduler_service.py → models/db.py

INCORRECT:
routes.py → Spotify API directly (bypass services and client)
routes.py → spotify/api.py directly (bypass services)
templates → business logic (should be in services)
```

## Stack Summary (Flask 3.1.x)

- **Flask** 3.1.x + **Flask-Session** 0.8.x
- **Pydantic** v2 for request validation
- **SQLAlchemy** for database (User, Schedule, JobExecution)
- **APScheduler** for background job execution
- **spotipy** for Spotify API
- **690** tests, all passing
- Retry logic with exponential backoff
- Fernet encryption for stored tokens
