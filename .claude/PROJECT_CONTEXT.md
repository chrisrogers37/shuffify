# Shuffify - Project Context

**Copy this into Claude web/phone sessions for context.**

---

## What This Project Does

Shuffify is a web application that provides advanced playlist management for Spotify users:
1. Users connect their Spotify account via OAuth 2.0
2. They select a playlist from their library
3. They choose a shuffle algorithm with parameters
4. The application reorders the playlist on Spotify
5. Multi-level undo allows stepping back through changes
6. **Playlist Workshop** provides track management, playlist merging, and external raiding
7. **Scheduled operations** allow automated shuffle/raid jobs on a recurring basis

---

## Architecture (4-Layer)

```
┌─────────────────────────────────────┐
│  Presentation Layer                 │
│  • routes.py    - Flask routes     │
│  • templates/   - Jinja2 HTML     │
│  • static/      - CSS, JS          │
│  • schemas/     - Pydantic schemas │
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│  Services Layer (10 services)       │
│  • auth_service.py    - OAuth flow │
│  • playlist_service.py - Playlists │
│  • shuffle_service.py  - Shuffling │
│  • state_service.py    - Undo/redo │
│  • token_service.py    - Encryption│
│  • scheduler_service.py - Sched.   │
│  • job_executor_service.py - Jobs  │
│  • user_service.py     - User mgmt│
│  • workshop_session_service.py     │
│  • upstream_source_service.py      │
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│  Business Logic Layer               │
│  • shuffle_algorithms/ - 7 algos  │
│  • spotify/     - Modular API      │
│  • models/     - Data + DB models  │
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│  External Services                  │
│  • Spotify Web API                  │
│  • OAuth 2.0 Provider              │
│  • Redis (sessions + caching)      │
│  • SQLite/PostgreSQL (database)    │
└─────────────────────────────────────┘
```

**STRICT RULE**: Never violate layer boundaries. Routes call services, services call business logic, business logic calls external APIs.

---

## Key Technologies

| Component | Technology |
|-----------|-----------|
| **Backend** | Flask 3.1.x (Python 3.12+) |
| **Frontend** | Tailwind CSS, vanilla JavaScript |
| **API Client** | spotipy (Spotify API wrapper) |
| **Validation** | Pydantic v2 (request/response validation) |
| **Database** | SQLAlchemy + SQLite (User, Schedule, JobExecution) |
| **Scheduler** | APScheduler (background job execution) |
| **Server** | Gunicorn (prod), Flask dev server (local, port 8000) |
| **Session** | Flask-Session 0.8.x (Redis-based, filesystem fallback) |
| **Caching** | Redis for Spotify API response caching |
| **Security** | Fernet encryption for stored refresh tokens |
| **Containerization** | Docker with health checks |

---

## Shuffle Algorithms (7 total, 6 visible)

| Algorithm | Description |
|-----------|-------------|
| **BasicShuffle** | Random reordering with optional fixed tracks at start |
| **BalancedShuffle** | Round-robin selection from all playlist sections |
| **PercentageShuffle** | Keep top N% fixed, shuffle remainder |
| **StratifiedShuffle** | Shuffle within sections independently |
| **ArtistSpacingShuffle** | Ensure same artist doesn't appear back-to-back |
| **AlbumSequenceShuffle** | Keep album tracks together, shuffle albums |
| **TempoGradientShuffle** | Sort by BPM for DJ-style transitions *(hidden — needs Audio Features API)* |

All algorithms inherit from `ShuffleAlgorithm` base class and auto-register via registry pattern.

---

## Key Files

| File | Purpose |
|------|---------|
| `shuffify/__init__.py` | Flask app factory, Redis/DB/Scheduler init |
| `shuffify/routes.py` | All HTTP routes |
| `shuffify/services/` | 10 service modules |
| `shuffify/schemas/` | Pydantic validation schemas (requests.py, schedule_requests.py) |
| `shuffify/spotify/` | Modular Spotify client (credentials, auth, api, cache, client) |
| `shuffify/shuffle_algorithms/registry.py` | Algorithm registration |
| `shuffify/models/playlist.py` | Playlist data model |
| `shuffify/models/db.py` | SQLAlchemy models (User, Schedule, JobExecution) |
| `shuffify/error_handlers.py` | Global exception handlers |
| `config.py` | Configuration (dev/prod) with Redis/DB/Scheduler settings |

---

## Session Management

**Undo System** (via StateService):
- Each shuffle saves state to `session['playlist_states'][playlist_id]`
- States tracked with `current_index` for navigation
- Users can undo/redo multiple times within a session
- Stack cleared on logout or session expiry

**OAuth Tokens**:
- Stored in `session['spotify_token']` as TokenInfo dict
- Never exposed to client-side
- Auto-refresh via SpotifyAuthManager when expired
- Retry logic with exponential backoff for transient errors

**Database-Stored Tokens** (for scheduled jobs):
- Refresh tokens encrypted with Fernet (PBKDF2-derived key from SECRET_KEY)
- Stored in `User.encrypted_refresh_token` column
- Decrypted at job execution time by TokenService

---

## Safety Rules

**NEVER suggest running:**
- `docker-compose up -d --build` (production deployment)
- `git push origin main` (deploys to production)

**SAFE to suggest:**
- `python run.py` (local development, port 8000)
- `pytest tests/ -v` (run tests)
- `flask routes` (view routes)
- `flake8 shuffify/` (linting)

---

## Current Status

**Completed:**
- OAuth 2.0 authentication (Facebook-compatible)
- 7 shuffle algorithms (6 visible, 1 hidden) with comprehensive tests
- Multi-level undo system (StateService)
- Docker containerization with health checks
- Flask 3.x upgrade (3.1.x)
- 10 services (auth, playlist, shuffle, state, token, scheduler, job_executor, user, workshop_session, upstream_source)
- Pydantic validation layer
- Modular Spotify client (credentials, auth, api, cache)
- Retry logic with exponential backoff
- Redis-based sessions and API response caching
- SQLAlchemy database (User, Schedule, JobExecution models)
- APScheduler for background job execution
- Fernet token encryption for stored refresh tokens
- Playlist Workshop (track management, merging, raiding)
- 690 tests, all passing

**Planned:**
- Live playlist preview
- CI/CD pipeline
- Notification system
- Public REST API

---

## Common Patterns

**Adding a new route**:
1. Define in `shuffify/routes.py`
2. Check for `session['access_token']`
3. Create template in `shuffify/templates/`
4. Add to navigation if needed

**Adding a new algorithm**:
1. Create in `shuffify/shuffle_algorithms/`
2. Use `@register_algorithm` decorator or add to registry dict
3. Inherit from `ShuffleAlgorithm`
4. Import in `shuffify/shuffle_algorithms/registry.py`
5. Add tests
6. Update README.md

**Testing**:
- All new features should have tests in `tests/`
- Run with `pytest tests/ -v`
- Coverage report: `pytest tests/ --cov=shuffify`

---

## Environment Variables

Required:
- `SPOTIFY_CLIENT_ID` - From Spotify Developer Dashboard
- `SPOTIFY_CLIENT_SECRET` - From Spotify Developer Dashboard
- `SPOTIFY_REDIRECT_URI` - OAuth callback URL (default: `http://localhost:8000/callback`)
- `SECRET_KEY` - Flask session secret (also used for Fernet key derivation)
- `FLASK_ENV` - `development` or `production`

Optional:
- `REDIS_URL` - Redis connection (default: `redis://localhost:6379/0`)
- `DATABASE_URL` - Database connection (default: `sqlite:///shuffify.db`)
- `SCHEDULER_ENABLED` - Enable/disable APScheduler (default: `true`)

---

## CHANGELOG Reminder

Every PR must update `CHANGELOG.md` under `## [Unreleased]`
