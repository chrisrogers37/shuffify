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

## Architecture (3-Layer)

```
Routes → Business Logic → External APIs
```

**NEVER violate layer boundaries:**
- Routes call business logic (algorithms, spotify client)
- Business logic calls external APIs (Spotify Web API)
- Templates only handle presentation

---

## Key Directories

| Path | Purpose |
|------|---------|
| `shuffify/` | Main application code |
| `shuffify/shuffle_algorithms/` | All shuffle algorithms |
| `shuffify/spotify/` | Spotify API client |
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
| `shuffify/spotify/client.py` | Spotify API wrapper |
| `shuffify/shuffle_algorithms/registry.py` | Algorithm registration |
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

**OAuth**:
1. User clicks "Connect with Spotify"
2. Redirected to Spotify authorization
3. Callback receives code
4. Exchange code for access token
5. Store token in `session['access_token']`

**Undo**:
- `session['undo_stack']` = list of previous states
- Push before shuffle, pop on undo
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
routes.py → shuffle_algorithms/basic.py → (algorithm logic)
routes.py → spotify/client.py → Spotify API

❌ INCORRECT:
routes.py → Spotify API directly (bypass client)
templates → business logic (should be in routes/algorithms)
```
