# Shuffify - Project Context

**Copy this into Claude web/phone sessions for context.**

---

## What This Project Does

Shuffify is a web application that provides advanced playlist reordering controls for Spotify users:
1. Users connect their Spotify account via OAuth 2.0
2. They select a playlist from their library
3. They choose a shuffle algorithm with parameters
4. The application reorders the playlist on Spotify
5. Multi-level undo allows stepping back through changes

---

## Architecture (3-Layer)

```
┌─────────────────────────────────────┐
│  Presentation Layer                 │
│  • routes.py    - Flask routes     │
│  • templates/   - Jinja2 HTML     │
│  • static/      - CSS, JS          │
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│  Business Logic Layer               │
│  • shuffle_algorithms/ - Algorithms│
│  • spotify/client.py - API wrapper │
│  • models/     - Data structures   │
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│  External Services                  │
│  • Spotify Web API                  │
│  • OAuth 2.0 Provider              │
└─────────────────────────────────────┘
```

**STRICT RULE**: Never violate layer boundaries. Routes call business logic, business logic calls external APIs.

---

## Key Technologies

| Component | Technology |
|-----------|-----------|
| **Backend** | Flask 2.3.3 (Python 3.12+) |
| **Frontend** | Tailwind CSS, vanilla JavaScript |
| **API Client** | spotipy (Spotify API wrapper) |
| **Server** | Gunicorn (prod), Flask dev server (local) |
| **Session** | Flask-Session (filesystem, migrating to Redis) |
| **Containerization** | Docker with health checks |

---

## Shuffle Algorithms

| Algorithm | Description |
|-----------|-------------|
| **BasicShuffle** | Random reordering with optional fixed tracks at start |
| **BalancedShuffle** | Round-robin selection from all playlist sections |
| **PercentageShuffle** | Keep top N% fixed, shuffle remainder |
| **StratifiedShuffle** | Shuffle within sections independently |

All algorithms inherit from `ShuffleAlgorithm` base class and auto-register via registry pattern.

---

## Key Files

| File | Purpose |
|------|---------|
| `shuffify/__init__.py` | Flask app factory |
| `shuffify/routes.py` | All HTTP routes |
| `shuffify/spotify/client.py` | Spotify API wrapper |
| `shuffify/shuffle_algorithms/registry.py` | Algorithm registration |
| `shuffify/models/playlist.py` | Playlist data model |
| `config.py` | Configuration (dev/prod) |

---

## Session Management

**Undo System**:
- Each shuffle saves previous track order to `session['undo_stack']`
- Users can undo multiple times within a session
- Stack cleared on logout or session expiry

**OAuth Tokens**:
- Stored in `session['access_token']`
- Never exposed to client-side
- Refresh handled by spotipy library

---

## Safety Rules

**NEVER suggest running:**
- `docker-compose up -d --build` (production deployment)
- `git push origin main` (deploys to production)

**SAFE to suggest:**
- `python run.py` (local development)
- `pytest tests/ -v` (run tests)
- `flask routes` (view routes)
- `ruff check shuffify/` (linting)

---

## Current Status: v2.3.6

- ✅ OAuth 2.0 authentication (Facebook-compatible)
- ✅ Four shuffle algorithms
- ✅ Multi-level undo system
- ✅ Docker containerization
- ✅ Health check endpoint
- 🔲 Flask 3.x upgrade (planned)
- 🔲 Redis session storage (planned)
- 🔲 Unit tests for algorithms (planned)

---

## Common Patterns

**Adding a new route**:
1. Define in `shuffify/routes.py`
2. Check for `session['access_token']`
3. Create template in `shuffify/templates/`
4. Add to navigation if needed

**Adding a new algorithm**:
1. Create in `shuffify/shuffle_algorithms/`
2. Use `@register_algorithm` decorator
3. Inherit from `ShuffleAlgorithm`
4. Import in `shuffify/shuffle_algorithms/__init__.py`
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
- `SPOTIFY_REDIRECT_URI` - OAuth callback URL
- `SECRET_KEY` - Flask session secret
- `FLASK_ENV` - `development` or `production`

---

## CHANGELOG Reminder

Every PR must update `CHANGELOG.md` under `## [Unreleased]`
