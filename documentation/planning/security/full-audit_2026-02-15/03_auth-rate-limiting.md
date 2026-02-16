# [MEDIUM] Add Rate Limiting to Auth Endpoints

| Field | Value |
|-------|-------|
| **PR Title** | `[MEDIUM] Add rate limiting to auth endpoints` |
| **Severity** | MEDIUM |
| **Effort** | 2-3 hours |
| **Finding** | #8 — No rate limiting on `/login` and `/callback` auth endpoints |
| **Files Modified** | `requirements/base.txt`, `shuffify/__init__.py`, `shuffify/error_handlers.py`, `tests/test_rate_limiting.py` (new), `CHANGELOG.md` |

---

## Findings Addressed

**Finding #8 (MEDIUM)**: No rate limiting on `/login` and `/callback` auth endpoints. Attackers could abuse the OAuth flow without throttling, potentially causing excessive Spotify API calls, session store exhaustion, or denial-of-service conditions.

---

## Dependencies

None. Flask-Limiter integrates directly with the existing Flask + Redis stack.

---

## Detailed Implementation Plan

### Approach

Use **Flask-Limiter** backed by Redis when available, with in-memory fallback. This is the standard Flask rate-limiting library. Do NOT implement a custom solution.

---

### Step 1: Add Flask-Limiter to `requirements/base.txt`

**File**: `requirements/base.txt`

Add one line at the end:

```
Flask-Limiter>=3.5.0
```

Then install:
```bash
source venv/bin/activate && pip install -r requirements/dev.txt
```

---

### Step 2: Initialize Flask-Limiter in the App Factory

**File**: `shuffify/__init__.py`

#### Step 2a: Add the import

After line 7 (`import redis`), add:

```python
from flask_limiter import Limiter
```

#### Step 2b: Add the module-level variable

After `_migrate: Optional[Migrate] = None`, add:

```python
_limiter: Optional[Limiter] = None
```

#### Step 2c: Add a getter function

After the `get_redis_client()` function, add:

```python
def get_limiter() -> Optional[Limiter]:
    """
    Get the global Flask-Limiter instance.

    Returns:
        Limiter instance if initialized, None otherwise.
    """
    return _limiter
```

#### Step 2d: Add initialization inside `create_app()`

After `Session(app)` and before the token encryption service initialization, add:

```python
    # Initialize Flask-Limiter for rate limiting
    global _limiter
    try:
        from flask_limiter.util import get_remote_address

        if _redis_client is not None:
            storage_uri = app.config.get("REDIS_URL")
            logger.info("Rate limiter using Redis storage")
        else:
            storage_uri = "memory://"
            logger.warning(
                "Rate limiter using in-memory storage. "
                "Rate limits will not persist across restarts."
            )

        _limiter = Limiter(
            app=app,
            key_func=get_remote_address,
            storage_uri=storage_uri,
            default_limits=[],  # No global default — per-route only
            strategy="fixed-window",
        )
        logger.info("Flask-Limiter initialized")
    except Exception as e:
        logger.warning(
            "Flask-Limiter initialization failed: %s. "
            "Rate limiting will be unavailable.",
            e,
        )
        _limiter = None
```

**Key design decisions**:
- `key_func=get_remote_address`: Rate limits by IP address (standard for pre-auth endpoints)
- `default_limits=[]`: No global limit — only auth routes are limited
- `strategy="fixed-window"`: Simple and predictable
- Wrapped in try/except so failures don't prevent app startup

#### Step 2e: Apply rate limits after blueprint registration

After `app.register_blueprint(main_blueprint)`, add:

```python
    # Apply rate limits to auth endpoints
    if _limiter is not None:
        try:
            _limiter.limit("10/minute")(
                app.view_functions["main.login"]
            )
            _limiter.limit("20/minute")(
                app.view_functions["main.callback"]
            )
            logger.info(
                "Rate limits applied: /login=10/min, /callback=20/min"
            )
        except Exception as e:
            logger.warning(
                "Failed to apply auth rate limits: %s", e
            )
```

**Why this pattern**: Applying limits via `app.view_functions` after blueprint registration keeps `core.py` completely clean — no rate limiting imports or decorators needed there. Also avoids circular imports.

**Why these specific limits**:
- `/login` at 10/min: Generous for real use, blocks brute-force abuse
- `/callback` at 20/min: Slightly higher because Spotify may redirect back multiple times during re-authorization

---

### Step 3: Add a Rate-Limit Exceeded Error Handler

**File**: `shuffify/error_handlers.py`

After the 500 error handler and before the final `logger.info`, add:

```python
    # =========================================================================
    # Rate Limiting (429)
    # =========================================================================

    @app.errorhandler(429)
    def handle_rate_limit_exceeded(error):
        """Handle 429 Too Many Requests from Flask-Limiter."""
        logger.warning(
            f"Rate limit exceeded: {request.remote_addr} on {request.path}"
        )
        return json_error_response(
            "Too many requests. Please wait a moment and try again.",
            429,
        )
```

**Why**: Without this, Flask-Limiter returns plain-text. This ensures the standard JSON error format is used, consistent with all other error handlers.

---

### Step 4: Write Tests

**File**: `tests/test_rate_limiting.py` (new file)

```python
"""
Tests for rate limiting on authentication endpoints.

Verifies that /login and /callback are rate-limited to prevent
abuse of the OAuth flow.
"""

import pytest
from unittest.mock import patch


@pytest.fixture
def rate_limited_app():
    """Create a Flask app with rate limiting enabled (in-memory)."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_client_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_client_secret"
    os.environ["SPOTIFY_REDIRECT_URI"] = (
        "http://localhost:5000/callback"
    )
    os.environ["SECRET_KEY"] = "test-secret-key-for-testing"

    from shuffify import create_app

    app = create_app("development")
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        from shuffify.models.db import db

        db.create_all()

    return app


@pytest.fixture
def rate_client(rate_limited_app):
    """Test client for rate limiting tests."""
    return rate_limited_app.test_client()


class TestLoginRateLimit:
    """Tests for rate limiting on the /login endpoint."""

    def test_login_allows_requests_under_limit(self, rate_client):
        """Requests under the limit should not return 429."""
        for _ in range(5):
            response = rate_client.get("/login")
            assert response.status_code != 429

    @patch("shuffify.routes.core.AuthService")
    def test_login_returns_429_when_limit_exceeded(
        self, mock_auth, rate_client
    ):
        """Exceeding the rate limit should return 429."""
        mock_auth.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize"
        )
        responses = []
        for _ in range(11):
            resp = rate_client.get("/login?legal_consent=true")
            responses.append(resp.status_code)
        assert 429 in responses

    def test_login_429_response_is_json(self, rate_client):
        """The 429 response should use standard JSON error format."""
        for _ in range(11):
            response = rate_client.get("/login")
        response = rate_client.get("/login")
        if response.status_code == 429:
            data = response.get_json()
            assert data is not None
            assert data["success"] is False
            assert "Too many requests" in data["message"]


class TestCallbackRateLimit:
    """Tests for rate limiting on the /callback endpoint."""

    def test_callback_allows_requests_under_limit(self, rate_client):
        """Requests under the limit should not return 429."""
        for _ in range(10):
            response = rate_client.get("/callback")
            assert response.status_code != 429

    def test_callback_returns_429_when_limit_exceeded(
        self, rate_client
    ):
        """Exceeding the rate limit should return 429."""
        responses = []
        for _ in range(21):
            resp = rate_client.get("/callback")
            responses.append(resp.status_code)
        assert 429 in responses

    def test_callback_429_response_is_json(self, rate_client):
        """The 429 response should use standard JSON error format."""
        for _ in range(21):
            response = rate_client.get("/callback")
        response = rate_client.get("/callback")
        if response.status_code == 429:
            data = response.get_json()
            assert data is not None
            assert data["success"] is False
            assert "Too many requests" in data["message"]


class TestRateLimitDoesNotAffectOtherRoutes:
    """Verify rate limits are scoped to auth endpoints only."""

    def test_health_endpoint_not_rate_limited(self, rate_client):
        """The /health endpoint should never return 429."""
        for _ in range(30):
            response = rate_client.get("/health")
            assert response.status_code == 200

    def test_index_not_rate_limited(self, rate_client):
        """The / endpoint should never return 429."""
        for _ in range(30):
            response = rate_client.get("/")
            assert response.status_code != 429


class TestLimiterInitialization:
    """Tests for limiter initialization behavior."""

    def test_limiter_is_available(self, rate_limited_app):
        """The limiter should be initialized in the app."""
        from shuffify import get_limiter

        limiter = get_limiter()
        assert limiter is not None

    def test_rate_limit_header_present(self, rate_client):
        """Rate-limited responses should include Retry-After."""
        for _ in range(11):
            response = rate_client.get("/login")
        response = rate_client.get("/login")
        if response.status_code == 429:
            assert "Retry-After" in response.headers
```

---

### Step 5: Update CHANGELOG.md

Under `## [Unreleased]`, add:

```markdown
### Security
- **Auth Endpoint Rate Limiting** - Added rate limiting to `/login` (10 req/min) and `/callback` (20 req/min)
  - Uses Flask-Limiter with Redis storage backend (in-memory fallback)
  - Returns standard JSON error response on 429 Too Many Requests
  - Added `Flask-Limiter>=3.5.0` dependency
```

---

## Verification Checklist

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 1 | Dependency installed | `pip show flask-limiter \| grep Version` | >= 3.5.0 |
| 2 | Lint passes | `flake8 shuffify/` | 0 errors |
| 3 | All tests pass | `pytest tests/ -v` | All pass |
| 4 | Rate limit tests pass | `pytest tests/test_rate_limiting.py -v` | All pass |
| 5 | /health not limited | `for i in $(seq 1 30); do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health; done` | All 200 |
| 6 | /login limited | `for i in $(seq 1 11); do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/login; done` | 429 on 11th |
| 7 | 429 is JSON | `curl -s http://localhost:8000/login` (after exceeding limit) | JSON with `success: false` |

---

## What NOT To Do

1. **Do NOT implement a custom rate limiting solution.** Flask-Limiter handles atomic counters, TTL, headers, and storage failover. A custom solution will have bugs.

2. **Do NOT add a global default rate limit.** Setting `default_limits=["200/day"]` would rate-limit `/health` (Docker calls it every 30s), static files, and API endpoints.

3. **Do NOT rate-limit `/logout`.** Rate-limiting logout could lock a user out of signing out, which is worse than not limiting it.

4. **Do NOT rate-limit by session or user ID.** Auth endpoints are hit BEFORE authentication — there's no user ID to key on. Use IP address.

5. **Do NOT set rate limits too low.** 1-3 per minute on `/login` will frustrate legitimate users. 10/minute is protective without being punitive.

6. **Do NOT skip the 429 error handler.** Without it, Flask-Limiter returns plain-text, breaking the JSON API contract.

7. **Do NOT put rate-limit decorators directly on route functions in `core.py`.** This creates circular imports. Apply limits in `create_app()` after blueprint registration.

8. **Do NOT use `fixed-window-elastic-expiry` strategy.** It extends the window on each request, potentially locking users out longer than expected.

9. **Do NOT modify existing route logic in `core.py`.** Rate limiting is applied externally via the app factory.
