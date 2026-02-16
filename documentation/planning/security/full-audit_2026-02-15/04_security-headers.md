# [LOW] Add Security Headers and Harden Health Endpoint

| Field | Value |
|-------|-------|
| **PR Title** | `[LOW] Add security headers and harden health endpoint` |
| **Severity** | LOW |
| **Effort** | 1-2 hours |
| **Findings Addressed** | #9 (No HSTS header), #10 (Health endpoint exposes DB status) |
| **Files Modified** | `shuffify/__init__.py`, `shuffify/routes/core.py`, `tests/test_health_db.py`, `tests/test_app_factory.py`, `CHANGELOG.md` |

---

## Findings Addressed

**Finding #9 (LOW)**: No HSTS header in production responses. Browsers will not enforce HTTPS on subsequent visits, allowing potential downgrade attacks.

**Finding #10 (LOW)**: Health endpoint exposes DB status without authentication. Returns `{"checks": {"database": "unavailable"}}` to any caller, revealing infrastructure details.

---

## Dependencies

None. Self-contained changes, no new packages required.

---

## Detailed Implementation Plan

### Step 1: Add Security Headers in `shuffify/__init__.py`

**File**: `shuffify/__init__.py`

Replace the existing `after_request` block (lines ~249-262) with a combined handler:

**Before:**

```python
    # Add a `no-cache` header to responses in development mode.
    if app.debug:

        @app.after_request
        def after_request(response):
            response.headers["Cache-Control"] = (
                "no-cache, no-store, must-revalidate, public, max-age=0"
            )
            response.headers["Expires"] = 0
            response.headers["Pragma"] = "no-cache"
            return response

    return app
```

**After:**

```python
    # Security headers applied to ALL responses
    @app.after_request
    def set_security_headers(response):
        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Control referrer information leakage
        response.headers["Referrer-Policy"] = (
            "strict-origin-when-cross-origin"
        )

        # HSTS: only in production (development uses HTTP)
        if not app.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # No-cache headers for development mode
        if app.debug:
            response.headers["Cache-Control"] = (
                "no-cache, no-store, must-revalidate, public, max-age=0"
            )
            response.headers["Expires"] = 0
            response.headers["Pragma"] = "no-cache"

        return response

    return app
```

**Key details**:
- Function renamed from `after_request` to `set_security_headers` for clarity
- The `if app.debug:` guard that wrapped the entire decorator is removed — the handler runs unconditionally
- HSTS only sent when `not app.debug` (production)
- Dev-mode no-cache logic preserved inside the function
- `max-age=31536000` = 1 year (standard recommendation)

---

### Step 2: Simplify the `/health` Endpoint

**File**: `shuffify/routes/core.py`

Remove the `checks` object from the response. Keep the internal DB check logic.

**Before:**

```python
@main.route("/health")
def health():
    """Health check endpoint for Docker and monitoring."""
    from shuffify import is_db_available

    db_healthy = is_db_available()
    overall_status = "healthy" if db_healthy else "degraded"

    return (
        jsonify({
            "status": overall_status,
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "checks": {
                "database": "ok" if db_healthy else "unavailable",
            },
        }),
        200,
    )
```

**After:**

```python
@main.route("/health")
def health():
    """Health check endpoint for Docker and monitoring."""
    from shuffify import is_db_available

    db_healthy = is_db_available()
    overall_status = "healthy" if db_healthy else "degraded"

    return (
        jsonify({
            "status": overall_status,
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
        }),
        200,
    )
```

**What changed**: Only the `"checks": {...}` key is removed. The `is_db_available()` call and `overall_status` logic remain. The endpoint still returns `"degraded"` when the DB is down — it just doesn't say *why*.

Docker `HEALTHCHECK` (Dockerfile line 41) uses `curl -f` which checks for HTTP 200. Since we still return 200, no Dockerfile changes needed.

---

### Step 3: Write Tests

#### 3a. Update `tests/test_health_db.py`

Replace the entire file:

```python
"""
Tests for health check endpoint.
"""

from unittest.mock import patch


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint should return 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_healthy_when_db_available(self, client):
        """Health should return 'healthy' when DB is up."""
        response = client.get("/health")
        data = response.get_json()
        assert data["status"] == "healthy"

    def test_health_returns_degraded_when_db_unavailable(self, client):
        """Health should return 'degraded' when DB is down."""
        with patch(
            "shuffify.is_db_available",
            return_value=False,
        ):
            response = client.get("/health")
            data = response.get_json()
            assert data["status"] == "degraded"

    def test_health_does_not_expose_subsystem_details(self, client):
        """Health response must NOT include a 'checks' key."""
        response = client.get("/health")
        data = response.get_json()
        assert "checks" not in data

    def test_health_does_not_expose_subsystem_on_failure(self, client):
        """Health response must NOT expose details even when degraded."""
        with patch(
            "shuffify.is_db_available",
            return_value=False,
        ):
            response = client.get("/health")
            data = response.get_json()
            assert "checks" not in data
            assert "database" not in str(data)

    def test_health_includes_timestamp(self, client):
        """Health response should include ISO timestamp."""
        response = client.get("/health")
        data = response.get_json()
        assert "timestamp" in data
        assert "T" in data["timestamp"]

    def test_health_response_has_exactly_two_keys(self, client):
        """Health response should only contain 'status' and 'timestamp'."""
        response = client.get("/health")
        data = response.get_json()
        assert set(data.keys()) == {"status", "timestamp"}
```

**Changes**: Removed tests asserting `"checks"` exists. Added tests asserting `"checks"` is absent. Added test for exact response shape.

#### 3b. Add Security Header Tests to `tests/test_app_factory.py`

Add at the bottom of the file:

```python
class TestSecurityHeaders:
    """Tests for security response headers."""

    def test_x_content_type_options_present(self, client):
        """All responses should include X-Content-Type-Options: nosniff."""
        response = client.get("/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options_present(self, client):
        """All responses should include X-Frame-Options: DENY."""
        response = client.get("/health")
        assert response.headers.get("X-Frame-Options") == "DENY"

    def test_referrer_policy_present(self, client):
        """All responses should include Referrer-Policy."""
        response = client.get("/health")
        assert (
            response.headers.get("Referrer-Policy")
            == "strict-origin-when-cross-origin"
        )

    def test_hsts_not_present_in_debug_mode(self, client):
        """HSTS should NOT be sent in debug/development mode."""
        response = client.get("/health")
        assert "Strict-Transport-Security" not in response.headers

    def test_hsts_present_in_production_mode(self):
        """HSTS should be sent when debug is False (production)."""
        from unittest.mock import patch

        with patch.dict("os.environ", {
            "SPOTIFY_CLIENT_ID": "test_id",
            "SPOTIFY_CLIENT_SECRET": "test_secret",
            "REDIS_URL": "",
        }):
            from shuffify import create_app

            app = create_app("production")
            app.config["TESTING"] = True
            app.config["SQLALCHEMY_DATABASE_URI"] = (
                "sqlite:///:memory:"
            )

            with app.test_client() as prod_client:
                response = prod_client.get("/health")
                hsts = response.headers.get(
                    "Strict-Transport-Security"
                )
                assert hsts is not None
                assert "max-age=31536000" in hsts
                assert "includeSubDomains" in hsts

    def test_security_headers_on_non_health_routes(self, client):
        """Security headers should be on all routes, not just /health."""
        response = client.get("/")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert (
            response.headers.get("Referrer-Policy")
            == "strict-origin-when-cross-origin"
        )
```

---

### Step 4: Update CHANGELOG.md

Under `## [Unreleased]`, add:

```markdown
### Security
- **HSTS Header** - Production responses now include `Strict-Transport-Security: max-age=31536000; includeSubDomains`
  - Only applied when `DEBUG = False`; development on HTTP is unaffected
- **Security Response Headers** - All responses now include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`
- **Health Endpoint Hardened** - `/health` no longer exposes which subsystem is degraded
  - Returns only `{"status": "healthy"}` or `{"status": "degraded"}` without `checks` object
```

---

## Verification Checklist

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 1 | Lint passes | `flake8 shuffify/` | 0 errors |
| 2 | All tests pass | `pytest tests/ -v` | All pass |
| 3 | Health tests pass | `pytest tests/test_health_db.py -v` | All pass |
| 4 | Header tests pass | `pytest tests/test_app_factory.py -v` | All pass |
| 5 | Health no checks | `curl http://localhost:8000/health` | No `"checks"` key |
| 6 | X-Content-Type | `curl -v http://localhost:8000/health 2>&1 \| grep X-Content` | `nosniff` |
| 7 | X-Frame-Options | `curl -v http://localhost:8000/health 2>&1 \| grep X-Frame` | `DENY` |
| 8 | No HSTS in dev | `curl -v http://localhost:8000/health 2>&1 \| grep Strict` | Not present |

---

## What NOT To Do

1. **Do NOT add HSTS unconditionally.** Adding it in debug mode tells the browser to only use HTTPS for localhost, breaking local development. Extremely difficult to undo (requires clearing browser HSTS entries manually).

2. **Do NOT remove the `is_db_available()` call.** The internal check must remain so the endpoint can return `"degraded"`. You're only removing the *exposure* of which subsystem failed.

3. **Do NOT change the HTTP status code of `/health`.** It must always return `200`. Docker's `HEALTHCHECK` uses `curl -f` which fails on non-2xx. Returning `503` for degraded state would cause Docker to restart the container.

4. **Do NOT add `Content-Security-Policy` in this PR.** CSP requires auditing all inline scripts, styles, and external resources across every template. It deserves its own dedicated PR.

5. **Do NOT add `preload` to the HSTS header.** `preload` submits the domain to browser HSTS preload lists permanently. Only do this after explicitly verifying HTTPS works on all subdomains.

6. **Do NOT gate `X-Content-Type-Options`, `X-Frame-Options`, or `Referrer-Policy` behind debug checks.** These headers are safe in both development and production. Only HSTS must be gated.

7. **Do NOT restructure the existing `after_request` logic beyond what is shown.** Keep the no-cache headers in the same function. Splitting into separate handlers adds complexity for no benefit.
