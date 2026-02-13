# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## CRITICAL SAFETY RULES

**THIS SYSTEM CONNECTS TO SPOTIFY. DO NOT TRIGGER PRODUCTION DEPLOYMENTS WITHOUT EXPLICIT USER APPROVAL.**

### NEVER run these commands without approval:
```bash
# DANGEROUS - Affects production
docker-compose up -d --build  # Deploys to production
git push origin main          # Pushes to production branch

# Use with caution
docker build .                # Builds production container
```

### SAFE commands you CAN run:
```bash
# Development server (local only)
python run.py

# Tests (always safe)
pytest tests/ -v

# Code quality checks
ruff check shuffify/
black --check shuffify/

# View routes/status
flask routes
```

### Before ANY production deployment:
1. **STOP** and ask the user for explicit confirmation
2. Explain exactly what will happen (e.g., "This will deploy to production")
3. Wait for user to type "yes" or approve

---

## Pre-Push Checklist (REQUIRED)

**CRITICAL**: Before pushing ANY code to `main`, you MUST run these checks locally and ensure they pass. CI/CD will fail if these are not passing.

### Required Checks

```bash
# 1. Backend Lint (REQUIRED - CI will fail without this)
flake8 shuffify/
# Must have 0 errors. Fix any issues before pushing.

# 2. Backend Tests (REQUIRED)
pytest tests/ -v
# All tests must pass.

# 3. Code Formatting Check (Recommended)
black --check shuffify/
# Run `black shuffify/` to auto-fix formatting issues.
```

### CI/CD Pipeline

The following checks run automatically on push to `main`:

| Check | Command | Must Pass |
|-------|---------|-----------|
| **Backend Lint** | `flake8 shuffify/` | ✅ Yes |
| **Backend Tests** | `pytest tests/ -v` | ✅ Yes |
| **Frontend Lint** | (Tailwind/JS checks) | ✅ Yes |
| **Frontend E2E Tests** | (Template rendering) | ✅ Yes |
| **Security Checks** | GitGuardian | ✅ Yes |

### Quick Pre-Push Command

Run this before every push:
```bash
flake8 shuffify/ && pytest tests/ -v && echo "✅ Ready to push!"
```

### Common Lint Fixes

```bash
# Auto-fix formatting
black shuffify/

# Remove unused imports (manual review needed)
# Look for F401 errors in flake8 output

# Fix line length (E501) - break long lines or use:
# flake8 --max-line-length=100 shuffify/  (if project allows)
```

---

## Project Overview

**Shuffify** is a web application that provides advanced playlist reordering controls for Spotify users.

**Core Philosophy**: Security-first development with OAuth 2.0, multi-level undo, and intuitive UX.

**Tech Stack**:
- **Backend**: Flask 3.1.x (Python 3.12+)
- **Frontend**: Tailwind CSS with custom animations, vanilla JavaScript
- **API**: Spotify Web API (via spotipy library)
- **Database**: SQLAlchemy + PostgreSQL/SQLite (9 models — see Models section)
- **Scheduler**: APScheduler for background job execution
- **Server**: Gunicorn (production), Flask dev server (local)
- **Containerization**: Docker with health checks
- **Session Management**: Flask-Session 0.8.x (Redis-based, filesystem fallback)
- **Caching**: Redis for Spotify API response caching
- **Validation**: Pydantic v2 for request validation
- **Security**: Fernet symmetric encryption for stored refresh tokens

## Architecture at a Glance

### Four-Layer Architecture

```
┌─────────────────────────────────────────┐
│  Presentation Layer                     │
│  • templates/     - Jinja2 templates   │
│  • static/        - CSS, JS, images    │
│  • routes/        - Flask routes (8 modules) │
│  • schemas/       - Pydantic validation │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│  Services Layer (15 services)           │
│  • auth_service.py      - OAuth flow   │
│  • playlist_service.py  - Playlist ops │
│  • shuffle_service.py   - Algorithms   │
│  • state_service.py     - Undo/redo    │
│  • token_service.py     - Encryption   │
│  • scheduler_service.py - CRUD sched   │
│  • job_executor_service - Job runner   │
│  • user_service.py      - User mgmt   │
│  • workshop_session_service.py         │
│  • upstream_source_service.py          │
│  • activity_log_service  - Audit trail │
│  • dashboard_service     - Dashboard   │
│  • login_history_service - Login logs  │
│  • playlist_snapshot_service - Snaps   │
│  • user_settings_service - Preferences │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│  Business Logic & Data Layer            │
│  • shuffle_algorithms/ - Algorithms    │
│  • spotify/           - API client      │
│  • spotify/cache.py   - Redis caching   │
│  • models/            - Data & DB models│
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│  External Services                      │
│  • Spotify Web API                      │
│  • OAuth 2.0 Provider                   │
│  • Redis (sessions + caching)           │
│  • PostgreSQL/SQLite (database)         │
└─────────────────────────────────────────┘
```

### Route Endpoints (42 total)

Routes are split across 8 modules in `shuffify/routes/`:

| Module | Routes | Purpose |
|--------|--------|---------|
| `core.py` | 7 | Home, login/callback/logout, health, terms, privacy |
| `playlists.py` | 4 | Playlist fetch, refresh, stats, user-playlists API |
| `shuffle.py` | 2 | Shuffle execution, undo |
| `workshop.py` | 11 | Workshop UI, preview-shuffle, commit, search, external playlists, session CRUD |
| `settings.py` | 2 | View/update user settings |
| `upstream_sources.py` | 3 | List/add/delete upstream raid sources |
| `schedules.py` | 7 | Schedule CRUD, toggle, manual run, execution history |
| `snapshots.py` | 5 | List/create/view/restore/delete playlist snapshots |

### Key Design Principle: SEPARATION OF CONCERNS

**CRITICAL**: Each layer is strictly isolated:
- **Routes** → handle HTTP requests/responses, call business logic
- **Business Logic** → shuffle algorithms, Spotify API interactions
- **Models** → data structures and validation only
- **Templates** → presentation logic only (no business logic)

**NEVER violate layer boundaries**. If you find yourself importing across layers incorrectly, refactor.

---

## Essential Commands

### Development Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements/dev.txt

# Configure environment
cp .env.example .env
# Add your Spotify API credentials to .env
# Get them from: https://developer.spotify.com/dashboard

# Run development server
python run.py
# Visit http://localhost:8000
```

### Common Development Tasks

```bash
# Run application
python run.py                 # Development server (auto-reload)
gunicorn run:app                  # Production mode

# Code quality
ruff check shuffify/          # Linting
black shuffify/               # Code formatting
pytest tests/ -v              # Run tests with verbose output
pytest tests/ --cov=shuffify  # Tests with coverage

# Docker operations
docker build -t shuffify .    # Build image
docker-compose up             # Run with docker-compose
docker-compose down           # Stop containers

# Flask utilities
flask routes                  # List all routes
flask shell                   # Interactive shell
```

---

## Core Modules Reference

### Shuffle Algorithms

All algorithms implement the `ShuffleAlgorithm` protocol and are registered in `ShuffleRegistry`.

| Algorithm | Description | Use Case |
|-----------|-------------|----------|
| **BasicShuffle** | Random reordering with optional fixed tracks | General-purpose randomization |
| **BalancedShuffle** | Round-robin from playlist sections | Even distribution from all parts |
| **PercentageShuffle** | Keep top N% fixed, shuffle rest | Protect favorites while refreshing |
| **StratifiedShuffle** | Shuffle within sections independently | Maintain overall structure |
| **ArtistSpacingShuffle** | Ensure same artist doesn't appear back-to-back | Variety in artist sequence |
| **AlbumSequenceShuffle** | Keep album tracks together, shuffle albums | Preserve album flow |
| **TempoGradientShuffle** | Sort by BPM for DJ-style transitions | DJ-style mixing *(hidden — needs Audio Features API)* |

**Location**: `shuffify/shuffle_algorithms/`

**Registry Pattern**: Algorithms are explicitly registered in `shuffify/shuffle_algorithms/registry.py` via the `_algorithms` dict. No decorator-based registration.

### Spotify Integration

**Module**: `shuffify/spotify/client.py`

**Key Methods**:
- `get_user_playlists()` - Fetch user's playlists
- `get_playlist_tracks()` - Get tracks from a playlist
- `reorder_playlist()` - Apply new track order to Spotify
- `get_current_user()` - User profile information

**Error Handling**: All Spotify API calls wrapped with exception handling and logging.

### Models

**Playlist Model** (`shuffify/models/playlist.py`):
- Encapsulates playlist data and metadata
- Validation for Spotify API payloads
- Helper methods for track manipulation

**Database Models** (`shuffify/models/db.py`):
- `User` — Spotify user with encrypted refresh token storage and profile fields
- `UserSettings` — Persistent user preferences (default algorithm, theme, notifications)
- `WorkshopSession` — Workshop session state tracking
- `UpstreamSource` — External playlist sources for raiding
- `Schedule` — Scheduled job definitions (shuffle/raid) with algorithm config
- `JobExecution` — Execution history log with status and results
- `LoginHistory` — Sign-in event tracking (IP, user agent, timestamps)
- `PlaylistSnapshot` — Point-in-time playlist track orderings with retention management
- `ActivityLog` — Unified audit trail for all user actions (17 activity types)

---

## Session Management & State

### Redis-Based Sessions

The application uses Redis for session storage with automatic filesystem fallback:
- **Primary**: Redis (configured via `REDIS_URL` environment variable)
- **Fallback**: Filesystem sessions in `.flask_session/` if Redis unavailable
- Session keys prefixed with `shuffify:session:` for namespacing

### Undo System

The application maintains a **session-based undo stack**:
- Each shuffle operation stores the previous track order
- Users can undo multiple times within a session
- Undo history cleared on logout or session expiry

**Implementation**: `session['undo_stack']` in Flask session

### API Response Caching

Spotify API responses are cached in Redis for improved performance:
- **Playlists**: 60 second TTL (changes frequently)
- **User profile**: 10 minute TTL
- **Audio features**: 24 hour TTL (rarely change)
- Cache automatically invalidated after playlist modifications

**Usage**:
```python
from shuffify import get_spotify_cache
from shuffify.spotify import SpotifyAPI

cache = get_spotify_cache()  # Returns None if Redis unavailable
api = SpotifyAPI(token_info, auth_manager, cache=cache)
playlists = api.get_user_playlists()  # Uses cache if available
```

### Security Considerations

- **OAuth Tokens**: Stored in Flask session (Redis), never exposed to client
- **Session Fallback**: Filesystem sessions in `.flask_session/` (gitignored)
- **CSRF Protection**: Handled by Flask session cookies
- **Redis Security**: Use `REDIS_URL` with authentication in production

---

## Development Guidelines

### 1. Algorithm Development

**Adding a new shuffle algorithm**:

```python
# shuffify/shuffle_algorithms/my_algorithm.py
from typing import List, Dict, Any, Optional
from . import ShuffleAlgorithm

class MyAlgorithm(ShuffleAlgorithm):
    @property
    def name(self) -> str:
        return "My Algorithm"

    @property
    def description(self) -> str:
        return "What this algorithm does"

    @property
    def parameters(self) -> dict:
        return {
            'param1': {'type': 'integer', 'default': 5, 'min': 1, 'max': 10,
                       'description': 'Parameter description'}
        }

    @property
    def requires_features(self) -> bool:
        return False

    def shuffle(self, tracks: List[Dict[str, Any]],
                features: Optional[Dict[str, Dict[str, Any]]] = None,
                **kwargs) -> List[str]:
        """Returns list of track URIs in new order."""
        # Your logic here
        return [t['uri'] for t in tracks if t.get('uri')]
```

**Then register in `shuffify/shuffle_algorithms/registry.py`**:
1. Add import: `from .my_algorithm import MyAlgorithm`
2. Add to `_algorithms` dict: `"MyAlgorithm": MyAlgorithm`
3. Add to `desired_order` list in `list_algorithms()` for UI positioning
4. Add tests in `tests/algorithms/test_my_algorithm.py`
5. Update `shuffify/shuffle_algorithms/README.md` with documentation

### 2. Route Development

**Adding a new route**:

Routes are organized in `shuffify/routes/` as feature modules (core, playlists, shuffle, workshop, upstream_sources, schedules, settings, snapshots). Add new routes to an existing module or create a new one:

```python
# shuffify/routes/my_feature.py (or add to existing module)
@main.route('/new-feature', methods=['GET', 'POST'])
def new_feature():
    """Route description."""
    if 'access_token' not in session:
        return redirect(url_for('main.index'))

    # Your logic here

    return render_template('new_feature.html')
```

If creating a new module, import it in `shuffify/routes/__init__.py`.

**Guidelines**:
- Always check for `access_token` in session before Spotify API calls
- Use `flash()` for user messages
- Log errors with appropriate severity
- Return proper HTTP status codes

### 3. Template Development

**Creating a new template**:

```html
<!-- shuffify/templates/new_feature.html -->
{% extends "base.html" %}

{% block title %}Feature Title{% endblock %}

{% block content %}
<!-- Your content here -->
{% endblock %}
```

**Guidelines**:
- Extend `base.html` for consistent layout
- Use Tailwind utility classes (already loaded)
- Include ARIA labels for accessibility
- Add focus states for keyboard navigation
- Use semantic HTML elements

### 4. Error Handling Pattern

```python
from flask import current_app
import logging

logger = logging.getLogger(__name__)

try:
    # Spotify API call
    result = sp.current_user_playlists()
except Exception as e:
    logger.error(f"Failed to fetch playlists: {e}", exc_info=True)
    flash("Unable to fetch playlists. Please try again.", "error")
    return redirect(url_for('main.dashboard'))
```

**Logging Levels**:
- **DEBUG**: Detailed diagnostic info
- **INFO**: General informational messages
- **WARNING**: Something unexpected but handled
- **ERROR**: Error occurred but app continues
- **CRITICAL**: Severe error, app might crash

### 5. Testing Standards

**Test Structure** (mirrors `shuffify/`):
```
tests/
├── algorithms/             # Tests for all 7 shuffle algorithms
├── spotify/                # Spotify client, API, cache tests
├── schemas/                # Pydantic schema validation tests
├── services/               # Service layer tests (15 services)
├── models/                 # Database model tests
├── test_app_factory.py     # App initialization tests
├── test_error_handlers.py  # Error handler tests
├── test_health_db.py       # Database health check tests
├── test_integration.py     # Integration tests
├── test_models.py          # Model unit tests
├── test_settings_route.py  # Settings route tests
├── test_snapshot_routes.py # Snapshot route tests
├── test_workshop*.py       # Workshop feature tests (4 files)
├── test_scheduler.py       # Scheduler tests
└── conftest.py             # Shared fixtures (app context, db, mocks)
```

**953 tests** covering all modules.

**Test Template**:

```python
# tests/shuffle_algorithms/test_example.py
import pytest
from shuffify.shuffle_algorithms.example import ExampleAlgorithm

class TestExampleAlgorithm:
    """Test suite for ExampleAlgorithm."""

    def test_shuffle_basic(self):
        """Test basic shuffle functionality."""
        # Arrange
        tracks = [{'id': i, 'name': f'Track {i}'} for i in range(10)]
        algorithm = ExampleAlgorithm()

        # Act
        result = algorithm.shuffle(tracks)

        # Assert
        assert len(result) == len(tracks)
        assert set(t['id'] for t in result) == set(t['id'] for t in tracks)

    def test_shuffle_with_params(self):
        """Test shuffle with parameters."""
        tracks = [{'id': i} for i in range(10)]
        algorithm = ExampleAlgorithm()

        result = algorithm.shuffle(tracks, param1=5)

        # Assertions based on expected behavior
```

**Running Tests**:
```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/shuffle_algorithms/test_basic.py -v

# With coverage
pytest tests/ --cov=shuffify --cov-report=html
```

---

## Configuration & Environment Variables

### Required Environment Variables

```bash
# Spotify API (REQUIRED)
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REDIRECT_URI=http://localhost:8000/callback

# Flask Configuration
# Note: FLASK_ENV was deprecated in Flask 3.0. The app uses it as a config selector.
FLASK_ENV=development  # or production (used for config selection)
SECRET_KEY=your-secret-key-here

# Redis (recommended for production)
REDIS_URL=redis://localhost:6379/0  # Falls back to filesystem if unavailable

# Database (optional — falls back to SQLite if not set)
DATABASE_URL=postgresql://user:pass@host:5432/dbname  # Production: Neon PostgreSQL

# Optional
PORT=8000
```

**Validation**: The application validates required environment variables on startup and fails fast in production.

**Redis Notes**:
- `REDIS_URL` format: `redis://[[username]:[password]@]host[:port][/database]`
- In production, use authentication: `redis://:password@host:6379/0`
- If Redis is unavailable, app automatically falls back to filesystem sessions

### Database & Migrations

The app uses SQLAlchemy with Alembic (via Flask-Migrate) for schema management:

```bash
# Migrations run automatically on app startup. For manual operations:
flask db current         # Show current migration version
flask db upgrade         # Apply pending migrations
flask db migrate -m "description"  # Generate new migration after model changes
flask db history         # Show migration history
```

**Local development**: If `DATABASE_URL` is not set, the app falls back to SQLite (`sqlite:///shuffify.db`). No migration setup needed for SQLite — `db.create_all()` handles it.

**Production**: Set `DATABASE_URL` to a PostgreSQL connection string. See `documentation/production-database-setup.md`.

### Configuration Files

- `config.py` - Main configuration with environment-specific classes
- `.env` - Local environment variables (gitignored)
- `requirements/base.txt` - Core dependencies
- `requirements/dev.txt` - Development dependencies (includes base.txt)
- `requirements/prod.txt` - Production dependencies (includes base.txt)

---

## Docker & Deployment

### Docker Configuration

**Dockerfile**:
- Multi-stage build (planned)
- Runs as `nobody:nogroup` for security
- Health check endpoint: `/health`
- Proper session directory permissions (`755`)

**Health Check**:
```bash
# Check application health
curl http://localhost:8000/health
# Returns: {"status": "healthy"}
```

### Security Best Practices

1. **Session Directory**: Set to `755` (not `777`)
2. **User Context**: Run as non-root user in container
3. **Environment Variables**: Never commit to git
4. **OAuth Tokens**: Stored server-side only
5. **HTTPS**: Required in production (enforced by Spotify)

---

## Changelog Maintenance (CRITICAL)

**ALWAYS update CHANGELOG.md when creating PRs.** The changelog is the user-facing record of all changes.

**Format**: This project uses [Keep a Changelog](https://keepachangelog.com/) with [Semantic Versioning](https://semver.org/).

**When to update**:
- **Every PR** must include a CHANGELOG.md entry
- Add entries under `## [Unreleased]` section
- Move entries to a versioned section when releasing

**Version bump rules** (Semantic Versioning):
- **MAJOR** (X.0.0): Breaking changes, incompatible API changes
- **MINOR** (x.Y.0): New features, backward-compatible additions
- **PATCH** (x.y.Z): Bug fixes, minor improvements

**Entry categories** (use as applicable):
- `### Added` - New features or capabilities
- `### Changed` - Changes to existing functionality
- `### Deprecated` - Features that will be removed
- `### Removed` - Features that were removed
- `### Fixed` - Bug fixes
- `### Security` - Security-related changes

**Entry format**:
```markdown
## [Unreleased]

### Added
- **Feature Name** - Brief description of what was added
  - Sub-bullet with implementation detail if needed

### Fixed
- **Bug Name** - What was broken and how it was fixed
```

**Example entry**:
```markdown
## [Unreleased]

### Added
- **Smart Shuffle Algorithm** - New algorithm that learns user preferences
  - Tracks user skip patterns to optimize future shuffles
  - Accessible via `/shuffle?algorithm=smart`
  - Added SmartShuffle class in `shuffify/shuffle_algorithms/smart.py`

### Fixed
- **Session Expiry Bug** - Fixed issue where undo stack was lost on token refresh
  - Preserve undo_stack across OAuth token refresh cycles
  - Added session persistence test in `tests/test_routes.py`
```

---

## Documentation Organization

**IMPORTANT**: All helpful markdown documentation should be placed in the `documentation/` directory.

```
documentation/
├── README.md              # Documentation index
├── production-database-setup.md  # Neon PostgreSQL setup guide
├── evaluation/            # Active system evaluations
│   ├── 03_extensibility_evaluation.md
│   ├── 04_future_features_readiness.md
│   └── 05_brainstorm_enhancements.md
├── planning/              # Development plans
│   └── phases/            # Feature implementation plans (empty — all archived)
├── archive/               # Completed evaluations and plans
│   ├── 01_architecture_evaluation.md
│   ├── 02_modularity_assessment.md
│   ├── tech_debt_q1-2026_2026-02-10/
│   ├── post-workshop-cleanup_2026-02-11/
│   ├── playlist-workshop_2026-02-10/  (6 phases, all complete)
│   └── user-persistence_2026-02-12/   (7 phases, all complete)
```

**Rules**:
- ✅ **DO** create subdirectories in `documentation/` based on purpose
- ✅ **DO** keep root-level docs for critical info only (README.md, CHANGELOG.md, CLAUDE.md)
- ✅ **DO** update `documentation/README.md` when adding new docs
- ❌ **DON'T** create markdown files scattered throughout the codebase
- ❌ **DON'T** put documentation in source directories (`shuffify/`, `tests/`, etc.)

**Root-level documentation exceptions** (only these in project root):
- `README.md` - Project overview and quick start
- `CHANGELOG.md` - Version history
- `CLAUDE.md` - This file (developer guide for AI assistants)
- `LICENSE` - Project license

---

## Common Patterns

### OAuth Flow

```python
# 1. Redirect to Spotify authorization
@main.route('/login')
def login():
    sp_oauth = SpotifyOAuth(...)
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

# 2. Handle callback
@main.route('/callback')
def callback():
    code = request.args.get('code')
    sp_oauth = SpotifyOAuth(...)
    token_info = sp_oauth.get_access_token(code)
    session['access_token'] = token_info['access_token']
    return redirect(url_for('main.dashboard'))

# 3. Use token
@main.route('/dashboard')
def dashboard():
    if 'access_token' not in session:
        return redirect(url_for('main.index'))

    sp = spotipy.Spotify(auth=session['access_token'])
    playlists = sp.current_user_playlists()
```

### Undo Stack Management

```python
# Before shuffle
if 'undo_stack' not in session:
    session['undo_stack'] = []

# Save current state
session['undo_stack'].append({
    'playlist_id': playlist_id,
    'snapshot_id': snapshot_id,
    'track_order': current_tracks
})
session.modified = True

# Undo
if session.get('undo_stack'):
    previous_state = session['undo_stack'].pop()
    # Restore previous state
    session.modified = True
```

---

## Troubleshooting Guide

### Common Issues

**OAuth errors ("Invalid redirect URI")**:
- Ensure `SPOTIFY_REDIRECT_URI` in `.env` matches Spotify Developer Dashboard
- Must be exact match including protocol and port
- Common: `http://localhost:8000/callback`

**Session not persisting**:
- Check `.flask_session/` directory exists and is writable
- Ensure `session.modified = True` after modifying session dict
- Verify `SECRET_KEY` is set in environment

**Undo not working**:
- Check `session['undo_stack']` is a list
- Ensure `session.modified = True` after modifications
- Verify session hasn't expired

**Import errors**:
- Ensure virtual environment is activated
- Run `pip install -r requirements/dev.txt`
- Check Python version (requires 3.12+)

**Tests failing**:
- Check all dependencies installed: `pip install -r requirements/dev.txt`
- Ensure no production environment variables set
- Run `pytest -v` for verbose output

**Docker build failing**:
- Check Dockerfile syntax
- Ensure all required files in build context
- Verify `.dockerignore` not excluding needed files

---

## Planned Improvements

### Short-term
- Integration tests for OAuth flow
- Live playlist preview (preview shuffle before committing)

### Medium-term
- CI/CD pipeline for automated testing and deployment
- Notification system (Telegram, SMS, email)
- Public REST API layer

### Long-term
- Analytics dashboard
- Multi-service support (Apple Music, YouTube Music)
- Plugin architecture

### Completed
- ~~Flask 3.x upgrade~~ (v3.1.x)
- ~~Redis session storage~~
- ~~Caching layer for Spotify API responses~~
- ~~Service layer extraction~~ (15 services)
- ~~Pydantic validation layer~~ (4 schema modules)
- ~~7 shuffle algorithms~~ (6 visible + 1 hidden, 953 tests)
- ~~Playlist Workshop~~ (track management, merging, raiding)
- ~~SQLAlchemy database~~ (9 models — see Models section)
- ~~APScheduler background jobs~~ (scheduled shuffle/raid operations)
- ~~Fernet token encryption~~ (secure refresh token storage)
- ~~Refresh playlists button~~
- ~~Route modularization~~ (split routes.py into 8 feature modules)
- ~~PostgreSQL support~~ (production database via Neon)
- ~~Alembic migrations~~ (database schema management via Flask-Migrate)
- ~~User persistence suite~~ (user dimension, login tracking, settings, snapshots, activity log, dashboard)
- ~~Playlist snapshots~~ (point-in-time capture with auto-snapshot and restoration)
- ~~Activity logging~~ (unified audit trail with 17 activity types)
- ~~Personalized dashboard~~ (welcome messaging, stats, activity feed)

---

## Things Claude Should NOT Do

- Don't modify OAuth redirect URIs without user approval
- Don't commit `.env` files or expose secrets
- Don't skip error handling in Spotify API calls
- Don't modify session structure without updating undo logic
- Don't add dependencies without updating requirements files
- Don't create templates without extending `base.html`
- Don't deploy to production without explicit approval
- Don't modify `CHANGELOG.md` version numbers (only add to Unreleased)
- **Don't push to `main` without running `flake8 shuffify/` and `pytest tests/ -v` first**

---

## Key Files to Know

| File | Contains |
|------|----------|
| `shuffify/__init__.py` | Flask app factory, Redis/DB/Scheduler initialization |
| `shuffify/routes/` | 8 feature-based route modules (core, playlists, shuffle, workshop, upstream_sources, schedules, settings, snapshots) |
| `shuffify/services/` | 15 service modules (auth, playlist, shuffle, state, token, scheduler, job_executor, user, workshop_session, upstream_source, activity_log, dashboard, login_history, playlist_snapshot, user_settings) |
| `shuffify/schemas/` | 4 Pydantic validation modules (requests, schedule_requests, settings_requests, snapshot_requests) |
| `shuffify/spotify/client.py` | Spotify API wrapper (facade) |
| `shuffify/spotify/api.py` | Spotify Web API data operations with caching support |
| `shuffify/spotify/cache.py` | Redis caching layer for Spotify API responses |
| `shuffify/shuffle_algorithms/registry.py` | Algorithm registration system (7 algorithms) |
| `shuffify/models/playlist.py` | Playlist data model |
| `shuffify/models/db.py` | SQLAlchemy models (9 models — User, UserSettings, WorkshopSession, UpstreamSource, Schedule, JobExecution, LoginHistory, PlaylistSnapshot, ActivityLog) |
| `shuffify/enums.py` | Shared enums (ScheduleFrequency, JobType, SnapshotType, ActivityType) |
| `shuffify/scheduler.py` | APScheduler initialization and startup |
| `shuffify/error_handlers.py` | Global exception handlers |
| `config.py` | Configuration classes (dev, prod) with Redis/DB/Scheduler settings |
| `run.py` | Application entry point |
| `migrations/` | Alembic migration scripts (auto-generated via Flask-Migrate) |

---

## Summary

**Key Principles**:
1. ✅ Security-first: validate inputs, protect OAuth tokens
2. ✅ Separation of concerns: routes → business logic → external APIs
3. ✅ User experience: maintain undo stack, clear error messages
4. ✅ Code quality: tests for all algorithms, proper error handling
5. ✅ Documentation: update CHANGELOG.md with every change
6. ✅ Logging: appropriate severity levels, structured messages
7. ✅ Configuration: environment-specific settings, fail-fast validation

**Quick Reference**:
- Main application: `python run.py`
- Run tests: `pytest tests/ -v`
- Check health: `curl http://localhost:8000/health`
- View routes: `flask routes`
- All documentation: See `documentation/` directory
- Algorithm docs: `shuffify/shuffle_algorithms/README.md`
- 953 tests across all modules

---

_Update this file continuously. Every mistake Claude makes is a learning opportunity._
