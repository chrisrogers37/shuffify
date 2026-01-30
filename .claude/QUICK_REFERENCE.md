# Shuffify - Quick Reference for Claude

## ⚠️ CRITICAL SAFETY RULES

**NEVER run these commands** (they affect production):
- `docker-compose up -d --build`
- `git push origin main`

**SAFE commands** (local development):
- `python run.py` (dev server)
- `pytest tests/ -v` (run tests)
- `flask routes` (view routes)
- `ruff check shuffify/` (linting)

---

## Architecture (4-Layer)

```
Routes → Services → Business Logic → External APIs
```

**NEVER violate layer boundaries:**
- Routes call services (auth, playlist, shuffle, state)
- Services call business logic (algorithms, spotify client)
- Business logic calls external APIs (Spotify Web API)
- Templates only handle presentation

---

## Key Directories

| Path | Purpose |
|------|---------|
| `shuffify/` | Main application code |
| `shuffify/services/` | Service layer (auth, playlist, shuffle, state) |
| `shuffify/schemas/` | Pydantic validation schemas |
| `shuffify/shuffle_algorithms/` | All shuffle algorithms |
| `shuffify/spotify/` | Modular Spotify client (credentials, auth, api) |
| `shuffify/models/` | Data models |
| `shuffify/templates/` | Jinja2 templates |
| `shuffify/static/` | CSS, JS, images |
| `tests/` | Test suite (mirrors shuffify/) |
| `documentation/` | All markdown docs |
| `requirements/` | Dependencies (base, dev, prod) |

---

## Common Tasks

| Task | Command/Action |
|------|----------------|
| Run dev server | `python run.py` |
| Run tests | `pytest tests/ -v` |
| Tests with coverage | `pytest tests/ --cov=shuffify` |
| Check linting | `ruff check shuffify/` |
| Format code | `black shuffify/` |
| View routes | `flask routes` |
| Interactive shell | `flask shell` |
| Quick commit | `/quick-commit` command |
| Full PR workflow | `/commit-push-pr` command |

---

## Key Files to Know

| File | Contains |
|------|----------|
| `shuffify/__init__.py` | Flask app factory |
| `shuffify/routes.py` | All HTTP routes |
| `shuffify/services/` | Service layer modules |
| `shuffify/schemas/requests.py` | Pydantic validation schemas |
| `shuffify/spotify/api.py` | Spotify API with retry logic |
| `shuffify/spotify/auth.py` | OAuth flow, token management |
| `shuffify/spotify/client.py` | Facade for backward compat |
| `shuffify/shuffle_algorithms/registry.py` | Algorithm registration |
| `shuffify/error_handlers.py` | Global exception handlers |
| `config.py` | Configuration classes |
| `run.py` | Application entry point |

---

## Shuffle Algorithms

| Algorithm | File | Description |
|-----------|------|-------------|
| **Basic** | `basic.py` | Random shuffle |
| **Balanced** | `balanced.py` | Round-robin from sections |
| **Percentage** | `percentage.py` | Keep top N% fixed |
| **Stratified** | `stratified.py` | Shuffle within sections |

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

---

## Testing

```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/shuffle_algorithms/test_basic.py -v

# With coverage
pytest tests/ --cov=shuffify --cov-report=html

# Coverage opens in browser
open htmlcov/index.html
```

---

## Environment Setup

```bash
# Create .env with:
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://localhost:5000/callback
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
✅ CORRECT:
routes.py → services/shuffle_service.py → shuffle_algorithms/basic.py
routes.py → services/playlist_service.py → spotify/api.py → Spotify API
routes.py → services/auth_service.py → spotify/auth.py

❌ INCORRECT:
routes.py → Spotify API directly (bypass services and client)
routes.py → spotify/api.py directly (bypass services)
templates → business logic (should be in services)
```

## Stack Summary (Flask 3.1.x)

- **Flask** 3.1.x + **Flask-Session** 0.8.x
- **Pydantic** v2 for request validation
- **spotipy** for Spotify API
- **315+** tests, all passing
- Retry logic with exponential backoff
