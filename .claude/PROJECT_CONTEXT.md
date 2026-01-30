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
│  Services Layer                     │
│  • auth_service.py - OAuth flow    │
│  • playlist_service.py - Playlists │
│  • shuffle_service.py - Shuffling  │
│  • state_service.py - Undo/redo    │
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│  Business Logic Layer               │
│  • shuffle_algorithms/ - Algorithms│
│  • spotify/     - Modular API      │
│  • models/     - Data structures   │
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│  External Services                  │
│  • Spotify Web API                  │
│  • OAuth 2.0 Provider              │
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
| **Server** | Gunicorn (prod), Flask dev server (local) |
| **Session** | Flask-Session 0.8.x (filesystem, migrating to Redis) |
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
| `shuffify/services/` | Service layer (auth, playlist, shuffle, state) |
| `shuffify/schemas/` | Pydantic validation schemas |
| `shuffify/spotify/` | Modular Spotify client (credentials, auth, api, client) |
| `shuffify/shuffle_algorithms/registry.py` | Algorithm registration |
| `shuffify/models/playlist.py` | Playlist data model |
| `shuffify/error_handlers.py` | Global exception handlers |
| `config.py` | Configuration (dev/prod) |

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

## Current Status: v2.4.x

**Completed:**
- ✅ OAuth 2.0 authentication (Facebook-compatible)
- ✅ Four shuffle algorithms with comprehensive tests
- ✅ Multi-level undo system (StateService)
- ✅ Docker containerization with health checks
- ✅ Flask 3.x upgrade (3.1.x)
- ✅ Services layer (auth, playlist, shuffle, state)
- ✅ Pydantic validation layer
- ✅ Modular Spotify client (credentials, auth, api)
- ✅ Retry logic with exponential backoff
- ✅ 315+ unit tests, all passing

**Planned:**
- 🔲 Redis session storage
- 🔲 Caching for Spotify API responses
- 🔲 CI/CD pipeline

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
