# Phase 04: Spotify API Error Handling Extraction

**Status**: ✅ COMPLETE (PR #95)
**Priority**: Medium
**Estimated effort**: Small (pure extraction)
**Branch**: `implement/spotify-error-extraction`

## Goal

Extract ~200 lines of error handling code from `shuffify/spotify/api.py` into a new `shuffify/spotify/error_handling.py` module for improved readability and separation of concerns.

## Current State

`api.py` (786 lines) contains both:
- Error handling infrastructure (retry constants, backoff calculation, error classification, retry decorator) — lines 35-233
- SpotifyAPI class with data operations — lines 236-787

The error handling code is self-contained and has no dependency on the SpotifyAPI class.

## Changes

### New File: `shuffify/spotify/error_handling.py`
- Move constants: `MAX_RETRIES`, `BASE_DELAY`, `MAX_DELAY`
- Move functions: `_calculate_backoff_delay`, `_classify_error`, `_should_retry`, `_get_retry_delay`, `_raise_final_error`
- Move decorator: `api_error_handler`
- Bring along required imports: `logging`, `time`, `functools.wraps`, `typing.Callable`, `spotipy`, `requests.exceptions`, custom exceptions

### Modified: `shuffify/spotify/api.py`
- Remove error handling code (lines 35-233)
- Remove now-unused imports: `time`, `wraps`, `Callable`, `RequestException`, `ConnectionError`, `Timeout`
- Remove `SpotifyRateLimitError` and `SpotifyNotFoundError` from exception imports
- Add: `from .error_handling import api_error_handler`
- Keep: `SpotifyAPIError` and `SpotifyTokenExpiredError` (still used in SpotifyAPI class)

### Modified: `tests/spotify/test_api.py`
- Update import: `from shuffify.spotify.error_handling import api_error_handler, _calculate_backoff_delay, MAX_RETRIES`
- Update 7 `time.sleep` mock patches from `shuffify.spotify.api.time.sleep` to `shuffify.spotify.error_handling.time.sleep`

### Modified: `shuffify/spotify/__init__.py`
- Update docstring to list `error_handling.py`

## Verification

```bash
flake8 shuffify/
pytest tests/spotify/test_api.py -v
pytest tests/ -v
```

## Notes

- Pure extraction — no logic changes, no formatting changes, no docstring changes
- Do NOT re-export error handling symbols from `api.py`
- Do NOT add `error_handling` to `__all__` in `__init__.py`
