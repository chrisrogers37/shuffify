# Phase 01: Fix Error Handling & Route Resilience

> **Status:** ✅ COMPLETE
> **Started:** 2026-02-25
> **Completed:** 2026-02-25

## Header

| Field | Value |
|---|---|
| **PR Title** | Fix global 500 handler and route error resilience for page routes |
| **Risk Level** | Low |
| **Estimated Effort** | Medium (3-4 hours) |
| **Files Modified** | 5 (`shuffify/error_handlers.py`, `shuffify/routes/schedules.py`, `shuffify/routes/settings.py`, `shuffify/routes/playlists.py`, `shuffify/templates/dashboard.html`) |
| **Files Created** | 2 (`shuffify/templates/errors/500.html`, `tests/routes/test_error_page_rendering.py`) |
| **Files Deleted** | 0 |

---

## Context

Three primary dashboard navigation targets -- Schedules, Settings, and Refresh -- return raw JSON error responses when exceptions occur. The root cause is the global 500 error handler at `shuffify/error_handlers.py:382-386`, which unconditionally returns JSON for ALL routes. The 404 handler (lines 373-380) already correctly differentiates between API and page routes. This phase aligns the 500 handler to the same pattern, creates an HTML error template, hardens the schedules and settings GET routes with broader exception handling, and fixes the `refreshPlaylists()` JavaScript function to properly check `response.ok`.

---

## Dependencies

- **Depends on**: None (this is Phase 01)
- **Unlocks**: Phases 02, 03, 04 (these phases modify `dashboard.html` more extensively; the minimal JS fix here is isolated to the `refreshPlaylists()` function only)

---

## Detailed Implementation Plan

### Step 1: Create the 500 error template

**New file**: `shuffify/templates/errors/500.html`

This template extends `base.html` and follows the visual pattern used by `settings.html` (gradient background, glass card, back-to-dashboard link). The `errors/` subdirectory does not yet exist; it will be created implicitly when the file is created.

**Full content**:

```html
{% extends "base.html" %}

{% block title %}Something went wrong - Shuffify{% endblock %}

{% block content %}
<div class="min-h-screen bg-gradient-to-br from-spotify-green via-spotify-green/90 to-spotify-dark">
    <div class="absolute inset-0" style="background-image: url('/static/images/hero-pattern.svg'); opacity: 0.15; pointer-events: none;"></div>

    <div class="relative flex items-center justify-center min-h-screen px-4">
        <div class="max-w-md w-full rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-8 text-center">
            <!-- Error icon -->
            <div class="mx-auto w-16 h-16 rounded-full bg-red-500/20 flex items-center justify-center mb-6">
                <svg class="w-8 h-8 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"></path>
                </svg>
            </div>

            <h1 class="text-2xl font-bold text-white mb-3">Something went wrong</h1>
            <p class="text-white/70 mb-6">
                We hit an unexpected error. This has been logged and we'll look into it.
            </p>

            <div class="flex flex-col gap-3">
                <a href="{{ url_for('main.index') }}"
                   class="inline-flex items-center justify-center px-6 py-3 rounded-lg bg-spotify-green hover:bg-spotify-green/80 text-white font-semibold transition duration-150">
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0a1 1 0 01-1-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 01-1 1"></path>
                    </svg>
                    Back to Dashboard
                </a>
                <button onclick="window.location.reload()"
                        class="inline-flex items-center justify-center px-6 py-3 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium transition duration-150 border border-white/20">
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                    </svg>
                    Try Again
                </button>
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

**Why this design**: It uses `base.html` inheritance for consistent footer/scripts, the same gradient background as all authenticated pages, and two clear CTAs: go home or retry. The error icon uses the warning triangle pattern common in error UIs.

---

### Step 2: Fix the global 500 error handler

**File**: `shuffify/error_handlers.py`

**Change 1 -- Add `render_template` to imports (line 9)**:

Before (`shuffify/error_handlers.py:9`):
```python
from flask import Blueprint, jsonify, request
```

After:
```python
from flask import Blueprint, jsonify, render_template, request
```

**Change 2 -- Rewrite the 500 handler (lines 382-386)**:

Before (`shuffify/error_handlers.py:382-386`):
```python
@app.errorhandler(500)
def handle_internal_error(error):
    """Handle 500 Internal Server Error."""
    logger.error(f"Internal server error: {error}", exc_info=True)
    return json_error_response("An unexpected error occurred.", 500)
```

After:
```python
@app.errorhandler(500)
def handle_internal_error(error):
    """Handle 500 Internal Server Error."""
    logger.error(
        "Internal server error on %s %s: %s [type=%s]",
        request.method,
        request.path,
        error,
        type(error).__name__,
        exc_info=True,
    )
    # Return JSON for API routes and AJAX requests
    if (
        request.path.startswith("/api/")
        or request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    ):
        return json_error_response(
            "An unexpected error occurred.", 500
        )
    # Render HTML error page for browser navigation
    return render_template("errors/500.html"), 500
```

**Why**: The handler now mirrors the 404 handler's pattern (check `request.path` and `request.is_json`) but also checks the `X-Requested-With` header. This is important because the `refreshPlaylists()` function and `handlePlaylistAction()` in `dashboard.html` both use AJAX. Without this check, those AJAX calls would receive an HTML page instead of JSON on error.

The structured log message now includes the HTTP method, path, and exception type name. This is crucial for diagnosing production errors -- knowing `ScheduleError` vs `SQLAlchemyError` vs `KeyError` tells you exactly where to look.

---

### Step 3: Add broader exception handling to the schedules GET route

**File**: `shuffify/routes/schedules.py`

**Change 1 -- Add `ScheduleError` to imports (line 33)**:

Before (`shuffify/routes/schedules.py:28-34`):
```python
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    AuthenticationError,
    PlaylistError,
)
```

After:
```python
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    AuthenticationError,
    PlaylistError,
    ScheduleError,
)
```

**Change 2 -- Expand exception handling (lines 92-99)**:

Before (`shuffify/routes/schedules.py:92-99`):
```python
    except (AuthenticationError, PlaylistError) as e:
        logger.error(
            f"Error loading schedules page: {e}"
        )
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )
```

After:
```python
    except (AuthenticationError, PlaylistError) as e:
        logger.error(
            "Auth/playlist error loading schedules: %s", e
        )
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )
    except ScheduleError as e:
        logger.error(
            "Schedule service error loading schedules page: %s",
            e,
        )
        flash(
            "Could not load schedule data. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))
    except Exception as e:
        logger.error(
            "Unexpected error loading schedules page: %s "
            "[type=%s]",
            e,
            type(e).__name__,
            exc_info=True,
        )
        flash(
            "Something went wrong loading schedules. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))
```

**Why**: The `ScheduleError` catch handles known scheduler-service failures with a specific message. The `Exception` catch is the safety net for anything else (database errors, template rendering errors, serialization errors). Both log the error with type information and redirect to the dashboard with a flash message, rather than falling through to the global 500 handler.

---

### Step 4: Add broader exception handling to the settings GET route

**File**: `shuffify/routes/settings.py`

**Change 1 -- No new imports needed**. `UserSettingsError` is already imported at line 31:
```python
from shuffify.services import (
    AuthService,
    ShuffleService,
    AuthenticationError,
    UserSettingsService,
    UserSettingsError,
)
```

**Change 2 -- Expand exception handling (lines 77-82)**:

Before (`shuffify/routes/settings.py:77-82`):
```python
    except AuthenticationError as e:
        logger.error("Error loading settings page: %s", e)
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )
```

After:
```python
    except AuthenticationError as e:
        logger.error("Auth error loading settings page: %s", e)
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )
    except UserSettingsError as e:
        logger.error(
            "Settings service error loading settings page: %s",
            e,
        )
        flash(
            "Could not load your settings. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))
    except Exception as e:
        logger.error(
            "Unexpected error loading settings page: %s "
            "[type=%s]",
            e,
            type(e).__name__,
            exc_info=True,
        )
        flash(
            "Something went wrong loading settings. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))
```

**Why**: Same rationale as Step 3. `UserSettingsError` is already imported but not caught. The general `Exception` fallback ensures no settings-page errors reach the global 500 handler.

Note: The `redirect` import is already present at line 10.

---

### Step 5: Add general exception fallback to the refresh route

**File**: `shuffify/routes/playlists.py`

**Change -- Expand exception handling (lines 38-43)**:

Before (`shuffify/routes/playlists.py:38-43`):
```python
    except PlaylistError as e:
        logger.error(f"Failed to refresh playlists: {e}")
        return json_error(
            "Failed to refresh playlists. Please try again.",
            500,
        )
```

After:
```python
    except PlaylistError as e:
        logger.error("Failed to refresh playlists: %s", e)
        return json_error(
            "Failed to refresh playlists. Please try again.",
            500,
        )
    except Exception as e:
        logger.error(
            "Unexpected error refreshing playlists: %s "
            "[type=%s]",
            e,
            type(e).__name__,
            exc_info=True,
        )
        return json_error(
            "An unexpected error occurred while refreshing "
            "playlists. Please try again.",
            500,
        )
```

**Why**: This is an AJAX endpoint, so JSON responses are correct. The change adds a catch-all so errors like serialization failures or unexpected Spotify API exceptions don't fall through to the global 500 handler. The structured log captures the exception type for debugging.

---

### Step 6: Fix `refreshPlaylists()` JavaScript to check `response.ok`

**File**: `shuffify/templates/dashboard.html`

**IMPORTANT**: This is the ONLY change to `dashboard.html` in this phase. Do not modify anything else in this file.

**Change -- Lines 496-528** (the `refreshPlaylists` function):

Before (`shuffify/templates/dashboard.html:496-528`):
```javascript
function refreshPlaylists() {
    const btn = document.getElementById('refresh-playlists-btn');
    const icon = document.getElementById('refresh-icon');
    const originalText = btn.innerHTML;

    btn.disabled = true;
    icon.classList.add('animate-spin');

    fetch('{{ url_for("main.refresh_playlists") }}', {
        method: 'POST',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showNotification(data.message, 'success');
            // Reload page to show updated playlists (preserves session/undo state)
            window.location.reload();
        } else {
            showNotification(data.message || 'Failed to refresh playlists.', 'error');
        }
    })
    .catch(error => {
        console.error('Error refreshing playlists:', error);
        showNotification('Failed to refresh playlists. Please try again.', 'error');
    })
    .finally(() => {
        btn.disabled = false;
        icon.classList.remove('animate-spin');
    });
}
```

After:
```javascript
function refreshPlaylists() {
    const btn = document.getElementById('refresh-playlists-btn');
    const icon = document.getElementById('refresh-icon');
    const originalText = btn.innerHTML;

    btn.disabled = true;
    icon.classList.add('animate-spin');

    fetch('{{ url_for("main.refresh_playlists") }}', {
        method: 'POST',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(response => {
        if (!response.ok) {
            return response.json().catch(() => {
                return { success: false, message: 'Server error (' + response.status + '). Please try again.' };
            }).then(data => {
                throw new Error(data.message || 'Failed to refresh playlists.');
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            showNotification(data.message, 'success');
            window.location.reload();
        } else {
            showNotification(data.message || 'Failed to refresh playlists.', 'error');
        }
    })
    .catch(error => {
        console.error('Error refreshing playlists:', error);
        showNotification(error.message || 'Failed to refresh playlists. Please try again.', 'error');
    })
    .finally(() => {
        btn.disabled = false;
        icon.classList.remove('animate-spin');
    });
}
```

**Why**: The original code calls `response.json()` unconditionally. If the server returns a non-200 response that isn't valid JSON (e.g., an HTML error page, a 502 from a proxy), `response.json()` throws a `SyntaxError` and the `.catch()` block shows a generic message. The fix:

1. Checks `response.ok` first (true for 200-299 status codes).
2. If not OK, attempts to parse JSON (which should work after our Step 5 fix), then throws an Error with the server's message.
3. If JSON parsing fails (e.g., upstream proxy returned HTML), creates a fallback error message with the HTTP status code.
4. The `.catch()` block now uses `error.message` which contains the specific error from the server.

---

## Test Plan

### New test file: `tests/routes/test_error_page_rendering.py`

This file tests the global 500 handler's HTML vs JSON branching and the new route exception handling.

**Tests to write**:

1. **`test_500_page_route_returns_html`** -- Hit a page route that raises an unhandled exception. Assert response is HTML (status 500, content-type `text/html`), and the response body contains "Something went wrong".

2. **`test_500_api_route_returns_json`** -- Hit a route under `/api/` that raises an unhandled exception. Assert response is JSON with `success: false`.

3. **`test_500_ajax_request_returns_json`** -- Hit a page route with `X-Requested-With: XMLHttpRequest` header. Assert response is JSON.

4. **`test_500_json_content_type_returns_json`** -- Hit a page route with `Content-Type: application/json`. Assert response is JSON.

5. **`test_500_handler_logs_exception_type`** -- Use `caplog` or mock logger to verify the structured log message includes the exception type name.

6. **`test_schedules_get_schedule_error_flashes_and_redirects`** -- Mock `SchedulerService.get_user_schedules` to raise `ScheduleError`. Assert 302 redirect to `main.index` and flash message.

7. **`test_schedules_get_unexpected_error_flashes_and_redirects`** -- Mock `SchedulerService.get_user_schedules` to raise `RuntimeError`. Assert 302 redirect to `main.index` and flash message.

8. **`test_settings_get_settings_error_flashes_and_redirects`** -- Mock `UserSettingsService.get_or_create` to raise `UserSettingsError`. Assert 302 redirect to `main.index` and flash message.

9. **`test_settings_get_unexpected_error_flashes_and_redirects`** -- Mock `UserSettingsService.get_or_create` to raise `RuntimeError`. Assert 302 redirect to `main.index` and flash message.

10. **`test_refresh_unexpected_error_returns_json`** -- Mock `PlaylistService.get_user_playlists` to raise `RuntimeError`. Assert JSON response with `success: false` and status 500.

### Existing test file modifications

**No modifications needed** to existing test files `tests/routes/test_schedules_routes.py`, `tests/routes/test_settings_routes.py`, or `tests/routes/test_playlists_routes.py` -- the new tests go in the new test file to keep concerns separated.

### Coverage expectations

- All modified code paths (500 handler: 3 branches; schedules: 2 new catches; settings: 2 new catches; playlists: 1 new catch; JS: manual) should have corresponding tests.
- The JS change is manually verified (see manual verification steps below).

### Manual verification steps

1. Start the dev server with `python run.py`
2. Log in with Spotify
3. Navigate to `/schedules` -- should render normally
4. Navigate to `/settings` -- should render normally
5. To test the 500 page: temporarily add `raise RuntimeError("test")` to the schedules route body, visit `/schedules`, confirm you see a flash message and redirect to dashboard (NOT raw JSON)
6. To test the 500 template directly: visit a route that triggers a genuine unhandled error. Confirm the error page renders with gradient background, warning icon, "Something went wrong" heading, and "Back to Dashboard" button.
7. Click the Refresh button on the dashboard. If Spotify API is working, confirm it refreshes. If not, confirm an error toast appears with a specific message (not a generic "An unknown error occurred").

---

## Documentation Updates

**`CHANGELOG.md`** -- Add under `## [Unreleased]`:

```markdown
### Fixed
- **500 Error Page** - Global 500 handler now returns an HTML error page for browser navigation instead of raw JSON
  - API routes and AJAX requests continue to receive JSON error responses
  - Added structured logging with exception type for production diagnostics
- **Schedules Page Error Handling** - Added broader exception handling to prevent raw JSON errors when schedule data fails to load
- **Settings Page Error Handling** - Added UserSettingsError and general fallback catches to settings page route
- **Refresh Button Error Handling** - Added general exception fallback to refresh endpoint and fixed JavaScript to check response.ok before parsing JSON
```

**No changes to** `CLAUDE.md`, `README.md`, or `documentation/README.md` -- this is a bug fix, not a new feature or architectural change.

---

## Stress Testing & Edge Cases

| Scenario | Expected Behavior |
|---|---|
| Database completely down, user navigates to `/schedules` | `get_db_user()` returns `None` -> flash + redirect to index (existing behavior at line 63-68) |
| Database partially down (SchedulerService query fails) | New `ScheduleError` catch -> flash + redirect to index |
| Spotify token expired mid-page-load on `/settings` | `AuthenticationError` catch (existing) -> clear session + login page |
| Template rendering error (e.g., missing variable) | `Exception` catch -> flash + redirect to index |
| AJAX refresh while session expired | `require_auth_and_db` decorator returns 401 JSON -> JS `.then()` sees `!response.ok` -> throws Error -> error toast |
| AJAX refresh, server returns HTML instead of JSON (e.g., proxy error) | JS `.catch()` on `response.json()` -> fallback message with status code |
| Concurrent page load and session expiry | Auth checks happen first, so redirect/401 before any service calls |

---

## Verification Checklist

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes (all existing + new tests)
- [ ] New file `shuffify/templates/errors/500.html` renders correctly when visited
- [ ] Schedules page loads normally when services are healthy
- [ ] Settings page loads normally when services are healthy
- [ ] Refresh button works normally and shows success toast
- [ ] Simulated error on schedules shows flash message + redirect (NOT JSON)
- [ ] Simulated error on settings shows flash message + redirect (NOT JSON)
- [ ] AJAX request to failing endpoint returns JSON (NOT HTML)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`

---

## What NOT To Do

1. **Do NOT add `except Exception` handlers to routes that use `@require_auth_and_db`**. Those routes (POST endpoints) return JSON and the global error handler now correctly returns JSON for AJAX requests. The broad catch is only needed on the GET page routes that do manual auth checking.

2. **Do NOT modify `dashboard.html` beyond the `refreshPlaylists()` function**. Phases 02, 03, and 04 make other dashboard.html changes. If you touch anything else, you create merge conflicts.

3. **Do NOT catch `Exception` and silently swallow it**. Every `except Exception` block MUST log with `exc_info=True` and `type(e).__name__` so the actual exception type is captured in production logs.

4. **Do NOT return `render_template("errors/500.html")` without the `, 500` status code**. Flask defaults to 200 if you forget the status code, which would make the browser think the error page is a successful response.

5. **Do NOT add error handling to the schedules/settings POST routes**. Those use `@require_auth_and_db` and their exceptions are already handled by the global error handlers (which now correctly return JSON for AJAX).

6. **Do NOT use `response.ok` check in `handlePlaylistAction()` in `base.html`**. That function already checks `!response.ok`. Only `refreshPlaylists()` is missing the check.

7. **Do NOT create a 404 error template** in this phase. The 404 handler currently delegates to Flask's default (`return error`). That's a separate enhancement.
