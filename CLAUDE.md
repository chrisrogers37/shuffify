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

## Project Overview

**Shuffify** is a web application that provides advanced playlist reordering controls for Spotify users.

**Core Philosophy**: Security-first development with OAuth 2.0, multi-level undo, and intuitive UX.

**Tech Stack**:
- **Backend**: Flask 3.1.x (Python 3.12+)
- **Frontend**: Tailwind CSS with custom animations, vanilla JavaScript
- **API**: Spotify Web API (via spotipy library)
- **Server**: Gunicorn (production), Flask dev server (local)
- **Containerization**: Docker with health checks
- **Session Management**: Flask-Session 0.8.x (filesystem-based, planned migration to Redis)
- **Validation**: Pydantic v2 for request validation

## Architecture at a Glance

### Four-Layer Architecture

```
┌─────────────────────────────────────────┐
│  Presentation Layer                     │
│  • templates/     - Jinja2 templates   │
│  • static/        - CSS, JS, images    │
│  • routes.py      - Flask routes       │
│  • schemas/       - Pydantic validation │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│  Services Layer                         │
│  • services/auth_service.py    - OAuth │
│  • services/playlist_service.py        │
│  • services/shuffle_service.py         │
│  • services/state_service.py           │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│  Business Logic & Data Layer            │
│  • shuffle_algorithms/ - Algorithms    │
│  • spotify/           - API client      │
│  • models/            - Data models     │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│  External Services                      │
│  • Spotify Web API                      │
│  • OAuth 2.0 Provider                   │
└─────────────────────────────────────────┘
```

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
# Edit .env with your Spotify API credentials
# Get them from: https://developer.spotify.com/dashboard

# Run development server
python run.py
# Visit http://localhost:5000
```

### Common Development Tasks

```bash
# Run application
python run.py                 # Development server (auto-reload)
gunicorn -c gunicorn_config.py run:app  # Production mode

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

All algorithms inherit from `ShuffleAlgorithm` base class and implement the `shuffle()` method.

| Algorithm | Description | Use Case |
|-----------|-------------|----------|
| **BasicShuffle** | Random reordering with optional fixed tracks | General-purpose randomization |
| **BalancedShuffle** | Round-robin from playlist sections | Even distribution from all parts |
| **PercentageShuffle** | Keep top N% fixed, shuffle rest | Protect favorites while refreshing |
| **StratifiedShuffle** | Shuffle within sections independently | Maintain overall structure |

**Location**: `shuffify/shuffle_algorithms/`

**Registry Pattern**: All algorithms auto-register via `shuffify/shuffle_algorithms/registry.py`

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

---

## Session Management & State

### Undo System

The application maintains a **session-based undo stack**:
- Each shuffle operation stores the previous track order
- Users can undo multiple times within a session
- Undo history cleared on logout or session expiry

**Implementation**: `session['undo_stack']` in Flask session

### Security Considerations

- **OAuth Tokens**: Stored in Flask session, never exposed to client
- **Session Directory**: `.flask_session/` (gitignored)
- **CSRF Protection**: Handled by Flask session cookies
- **No Database**: Stateless design, all state in session

---

## Development Guidelines

### 1. Algorithm Development

**Adding a new shuffle algorithm**:

```python
# shuffify/shuffle_algorithms/my_algorithm.py
from .registry import ShuffleAlgorithm, register_algorithm

@register_algorithm(
    algorithm_id='my_algorithm',
    name='My Algorithm',
    description='What this algorithm does',
    parameters={
        'param1': {'type': 'int', 'default': 5, 'description': 'Parameter description'}
    }
)
class MyAlgorithm(ShuffleAlgorithm):
    def shuffle(self, tracks, **params):
        """
        Implement your shuffle logic here.

        Args:
            tracks: List of track dictionaries
            **params: Algorithm parameters

        Returns:
            List of reordered tracks
        """
        # Your logic here
        return reordered_tracks
```

**Then**:
1. Import in `shuffify/shuffle_algorithms/__init__.py`
2. Add tests in `tests/shuffle_algorithms/test_my_algorithm.py`
3. Update `shuffify/shuffle_algorithms/README.md` with documentation

### 2. Route Development

**Adding a new route**:

```python
# shuffify/routes.py
@main.route('/new-feature', methods=['GET', 'POST'])
def new_feature():
    """Route description."""
    if 'access_token' not in session:
        return redirect(url_for('main.index'))

    # Your logic here

    return render_template('new_feature.html')
```

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
├── shuffle_algorithms/
│   ├── test_basic.py
│   ├── test_balanced.py
│   ├── test_percentage.py
│   └── test_stratified.py
├── spotify/
│   └── test_client.py
└── test_routes.py
```

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
SPOTIFY_REDIRECT_URI=http://localhost:5000/callback

# Flask Configuration
# Note: FLASK_ENV was deprecated in Flask 3.0. The app uses it as a config selector.
FLASK_ENV=development  # or production (used for config selection)
SECRET_KEY=your-secret-key-here

# Optional
PORT=5000
```

**Validation**: The application validates required environment variables on startup and fails fast in production.

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
curl http://localhost:5000/health
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
├── planning/              # Design docs, architecture decisions
│   └── separation_of_concerns_evaluation.md
├── guides/                # How-to guides and tutorials
│   ├── spotify_setup.md   # OAuth setup guide
│   └── algorithm_dev.md   # Algorithm development guide
└── operations/            # Production runbooks
    ├── deployment.md      # Deployment procedures
    └── monitoring.md      # Health check and monitoring
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
- Common: `http://localhost:5000/callback`

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

### Short-term (v2.4.x)
- Refresh playlists button without losing undo state
- Unit tests for all shuffle algorithms
- Integration tests for OAuth flow

### Medium-term (v2.5.x)
- Flask 3.x upgrade (breaking changes assessment needed)
- Redis session storage (more scalable than filesystem)
- Caching layer for Spotify API responses

### Long-term (v3.0.0+)
- Lightweight database for user preferences
- Analytics dashboard
- Algorithm performance comparison
- A/B testing framework

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

---

## Key Files to Know

| File | Contains |
|------|----------|
| `shuffify/__init__.py` | Flask app factory, configuration loading |
| `shuffify/routes.py` | All HTTP routes and view logic |
| `shuffify/spotify/client.py` | Spotify API wrapper |
| `shuffify/shuffle_algorithms/registry.py` | Algorithm registration system |
| `shuffify/models/playlist.py` | Playlist data model |
| `config.py` | Configuration classes (dev, prod) |
| `run.py` | Application entry point |

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
- Check health: `curl http://localhost:5000/health`
- View routes: `flask routes`
- All documentation: See `documentation/` directory
- Algorithm docs: `shuffify/shuffle_algorithms/README.md`

---

_Update this file continuously. Every mistake Claude makes is a learning opportunity._
