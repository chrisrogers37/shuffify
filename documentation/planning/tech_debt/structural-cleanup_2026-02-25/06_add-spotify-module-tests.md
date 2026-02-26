# Phase 06: Add Missing Spotify Module Tests

`📋 PENDING`

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `test: Add dedicated tests for spotify error_handling and exceptions modules` |
| **Risk Level** | Low (tests only, no production code changes) |
| **Estimated Effort** | Low (1-2 hours) |
| **Dependencies** | None |
| **Blocks** | Nothing |

### Files Created

| File | Purpose |
|------|---------|
| `tests/spotify/test_error_handling.py` | Tests for `_classify_error`, `_should_retry`, `_get_retry_delay`, `_raise_final_error`, constants |
| `tests/spotify/test_exceptions.py` | Tests for all 7 exception classes — hierarchy, messages, custom attributes |

---

## Problem

Two modules in `shuffify/spotify/` lack dedicated test files:

1. **`shuffify/spotify/error_handling.py`** (225 lines) — 5 private helper functions, 3 constants, and the `api_error_handler` decorator. The decorator and `_calculate_backoff_delay` have partial coverage in `tests/spotify/test_api.py`, but `_classify_error`, `_should_retry`, `_get_retry_delay`, and `_raise_final_error` have **zero direct test coverage**.

2. **`shuffify/spotify/exceptions.py`** (49 lines) — 7 exception classes in a hierarchy. Used extensively but never tested for hierarchy structure or the custom `retry_after` attribute on `SpotifyRateLimitError`.

---

## Step-by-Step Implementation

### Step 1: Create `tests/spotify/test_exceptions.py`

Test the exception hierarchy, inheritance chains, catch semantics, message passing, and `SpotifyRateLimitError.retry_after`.

**Test classes to create:**

| Class | Tests | Purpose |
|-------|-------|---------|
| `TestExceptionHierarchy` | 7 | Verify `issubclass` relationships for all 7 classes |
| `TestExceptionCatchSemantics` | 7 | Verify parent catches child, sibling doesn't catch sibling |
| `TestExceptionMessages` | 6 | Verify `str(err)` returns the message for each class |
| `TestSpotifyRateLimitError` | 6 | `retry_after` default (None), set values, inheritance |

### Step 2: Create `tests/spotify/test_error_handling.py`

Test the helper functions NOT already covered in `test_api.py`.

**Test classes to create:**

| Class | Tests | Purpose |
|-------|-------|---------|
| `TestModuleConstants` | 3 | Assert `MAX_RETRIES=4`, `BASE_DELAY=2`, `MAX_DELAY=16` |
| `TestClassifyError` | 15 | All HTTP status codes (404, 401, 429, 500-504, 400, 403) + ConnectionError, Timeout, RequestException, ValueError, RuntimeError, generic Exception |
| `TestShouldRetry` | 7 | All 7 categories: rate_limited/server_error/network_error = True; not_found/token_expired/client_error/unexpected = False |
| `TestGetRetryDelay` | 6 | Rate-limited with Retry-After header, without header, None headers; server_error and network_error use backoff |
| `TestRaiseFinalError` | 14 | All 7 categories raise correct exception type with correct message; logging assertions for server_error, network_error, client_error, unexpected |

---

## Verification Checklist

```bash
# 1. Run only new tests
./venv/bin/python -m pytest tests/spotify/test_exceptions.py tests/spotify/test_error_handling.py -v

# 2. Full test suite
./venv/bin/python -m pytest tests/ -v

# 3. Lint new files
./venv/bin/python -m flake8 tests/spotify/test_exceptions.py tests/spotify/test_error_handling.py
```

---

## What NOT To Do

1. **Do NOT duplicate tests from `test_api.py`.** `TestBackoffCalculation`, `TestApiErrorHandler`, and `TestApiErrorHandlerRetry` already cover `_calculate_backoff_delay` and the `api_error_handler` decorator.
2. **Do NOT modify `test_api.py`** to move existing tests out.
3. **Do NOT import from `shuffify.spotify.api` or `client`.** Keep each test file focused on its own module.
4. **Do NOT add test dependencies.** All required packages are already in `requirements/dev.txt`.
5. **Do NOT use `time.sleep()` in tests.** These test helper functions directly, not the decorator.
6. **Do NOT test exact log message format strings.** Test that logging occurs at the right level and includes key identifiers.
