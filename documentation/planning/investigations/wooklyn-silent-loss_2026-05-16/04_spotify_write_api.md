# F4 — Tighten Spotify write-API contracts

**Status:** COMPLETE — Started 2026-05-17
**Investigation:** [00_INVESTIGATION.md](00_INVESTIGATION.md)
**Targets:** RC3 (shuffle multi-batch partial-write), RC4/RC5 (add/remove partial-write)
**Risk:** Low. Effort: Medium.

## Implementation note

The plan's return-type change from `bool`/`None` to `list[str]` was skipped.
The same diagnostic information lives on `SpotifyPartialBatchError` (which is
raised on failure), so on success the return value would only repeat the input
URI list — pure noise. Keeping `bool` (`True` on full success) preserves the
existing `if success:` checks in `playlist_service.update_playlist_tracks` and
in `spotify/client.py` without touching call sites.

`_batch_add_tracks` shortfall check (plan section E) was also dropped: the
underlying `playlist_add_items` now raises `SpotifyPartialBatchError` on
per-batch HTTP failure, so a separate shortfall test in `_batch_add_tracks`
would only fire if `playlist_add_items` returned `True` while quietly losing
tracks — which can't happen given the new contract. Silent-dedupe / Spotify
side-effects that pass HTTP are F1's job.

F2's rollback handler was extended in the same change: `execute()` now catches
`(PlaylistVerificationError, SpotifyPartialBatchError)` and `_record_rollback`
shapes its activity-log payload from whichever error type fired. Two helpers
(`_rollback_trigger_phrase`, `_rollback_metadata`) encapsulate the branching.

## Context

The three Spotify-side write helpers swallow most failure information:

- `update_playlist_tracks` (`spotify/api.py:336-385`) PUTs the first batch then
  POSTs subsequent batches in a loop. The return value is `True` whether or not
  later POSTs succeeded.
- `playlist_add_items` (`spotify/api.py:427-460`) returns `None`. Callers can't
  tell partial batches apart from full success.
- `playlist_remove_items` (`spotify/api.py:462-508`) returns `True` regardless
  of how many of the requested URIs actually got removed (Spotify silently
  no-ops on missing URIs).
- `_batch_add_tracks` (`base_executor.py:401-408`) calls `playlist_add_items`
  and ignores the return value entirely.

F1's verify wrapper catches the **outcome** of these failures, but F4 closes
the gap at the **source** — the lowest layer raises so the diagnostic shows
which batch failed, with what HTTP status. This makes Sentry events (F5)
actionable and reduces the cases F1's expensive re-fetch has to handle.

## Files touched

| File | Change |
|------|--------|
| `shuffify/spotify/api.py` | `update_playlist_tracks`, `playlist_add_items`, `playlist_remove_items` all return a structured result and raise on per-batch HTTP failure. Add a `SpotifyPartialBatchError` exception |
| `shuffify/spotify/exceptions.py` | Add `SpotifyPartialBatchError(SpotifyAPIError)` |
| `shuffify/spotify/http_client.py` | Verify that non-2xx already raises (it should, but confirm — see Investigation note below) |
| `shuffify/services/executors/base_executor.py` | `_batch_add_tracks` consumes the return value and raises on shortfall |
| `tests/spotify/test_api_write_contracts.py` | New test module |

## Approach

### A. New exception

```python
# shuffify/spotify/exceptions.py

class SpotifyPartialBatchError(SpotifyAPIError):
    """Raised when a multi-batch playlist write fails mid-flight.

    Attributes:
        playlist_id: Target playlist.
        method: One of 'add', 'remove', 'update'.
        completed_batches: Number of batches that succeeded.
        total_batches: Total batches attempted.
        completed_uris: URIs successfully written.
        remaining_uris: URIs that were not written (or whose write
            could not be confirmed).
        cause: The underlying HTTP/Spotify error.
    """
```

### B. `update_playlist_tracks` — per-batch try, raise on failure

Current (`spotify/api.py:344-385`):
```python
# Replace first batch
self._http.put(...)

# Add remaining batches
for i in range(self.BATCH_SIZE, len(track_uris), self.BATCH_SIZE):
    batch = track_uris[i: i + self.BATCH_SIZE]
    self._http.post(...)

return True
```

New:
```python
completed: list[str] = []
total_batches = (len(track_uris) + self.BATCH_SIZE - 1) // self.BATCH_SIZE

# Batch 1: PUT (replaces playlist contents)
first = track_uris[:self.BATCH_SIZE]
try:
    self._http.put(
        f"/playlists/{playlist_id}/items",
        json={"uris": first},
    )
except SpotifyAPIError as e:
    raise SpotifyPartialBatchError(
        playlist_id=playlist_id, method="update",
        completed_batches=0, total_batches=total_batches,
        completed_uris=[],
        remaining_uris=list(track_uris),
        cause=e,
    )
completed.extend(first)

# Batches 2+: POST (append)
for batch_idx, i in enumerate(
    range(self.BATCH_SIZE, len(track_uris), self.BATCH_SIZE), start=1,
):
    batch = track_uris[i: i + self.BATCH_SIZE]
    try:
        self._http.post(
            f"/playlists/{playlist_id}/items",
            json={"uris": batch},
        )
    except SpotifyAPIError as e:
        if self._cache:
            self._cache.invalidate_playlist(playlist_id)
        raise SpotifyPartialBatchError(
            playlist_id=playlist_id, method="update",
            completed_batches=batch_idx, total_batches=total_batches,
            completed_uris=list(completed),
            remaining_uris=list(track_uris[i:]),
            cause=e,
        )
    completed.extend(batch)

if self._cache:
    self._cache.invalidate_playlist(playlist_id)
    if self._user_id:
        self._cache.invalidate_user_playlists(self._user_id)

return completed
```

Return contract changes from `bool` to `list[str]` — the URIs actually written
in order. Existing callers either:
- discard the return (e.g., `shuffle_executor.py:157-159`) — unaffected, F1
  drives verification;
- use it as a truthy check — change to `len(result) == len(track_uris)`.

### C. `playlist_add_items` — same pattern

Today returns `None`. Change to return `list[str]` (URIs written), raise
`SpotifyPartialBatchError` on per-batch failure.

### D. `playlist_remove_items` — same pattern

Spotify's DELETE silently no-ops on URIs not on the playlist. The HTTP layer
can succeed without anything being removed. **Detecting that requires a
post-write fetch**, which is exactly what F1 does. F4's job here is narrower:
ensure HTTP-level failures raise `SpotifyPartialBatchError(method="remove")`.

Do **not** try to detect "URI was on the playlist but didn't get removed" at
this layer — that's F1's job. F4 only catches "HTTP request failed".

### E. `_batch_add_tracks` (`base_executor.py:401-408`)

Today:
```python
@staticmethod
def _batch_add_tracks(
    api: SpotifyAPI,
    playlist_id: str,
    uris: List[str],
    batch_size: int = 100,
) -> None:
    api.playlist_add_items(playlist_id, uris)
```

New:
```python
@staticmethod
def _batch_add_tracks(
    api: SpotifyAPI,
    playlist_id: str,
    uris: List[str],
    batch_size: int = 100,
) -> list[str]:
    written = api.playlist_add_items(playlist_id, uris) or []
    if len(written) != len(uris):
        raise SpotifyPartialBatchError(
            playlist_id=playlist_id, method="add",
            completed_batches=-1, total_batches=-1,
            completed_uris=list(written),
            remaining_uris=[u for u in uris if u not in set(written)],
            cause=None,
        )
    return written
```

(`-1` sentinels for batch counts when we can't recover that detail from the
wrapped call — `playlist_add_items` is where the real counters live.)

### F. Verify `http_client.py` raises on non-2xx

The repo notes that `SpotifyHTTPClient` has retry + rate-limit backoff. Before
implementing F4, run a quick check:

```bash
grep -n "raise" shuffify/spotify/http_client.py
grep -n "status_code" shuffify/spotify/http_client.py
```

Confirm that 4xx (other than 429 + 401-with-refresh) and 5xx (after retries
exhausted) propagate as `SpotifyAPIError` / subclasses. The plan assumes they
do; if not, fix that as part of F4 — without it, the catches above don't fire.

## Tests (`tests/spotify/test_api_write_contracts.py`)

Use `pytest-mock` (or `unittest.mock`) to patch `SpotifyHTTPClient`:

```python
def test_update_playlist_tracks_succeeds_returns_uris()
def test_update_playlist_tracks_put_failure_raises_partial_batch()
def test_update_playlist_tracks_first_post_failure_raises_partial_batch()
def test_update_playlist_tracks_last_post_failure_raises_partial_batch()
def test_update_playlist_tracks_invalidates_cache_on_failure()  # data already changed
def test_playlist_add_items_succeeds_returns_uris()
def test_playlist_add_items_mid_batch_failure_raises_partial_batch()
def test_playlist_remove_items_http_failure_raises_partial_batch()
def test_batch_add_tracks_propagates_partial_batch()
def test_partial_batch_error_carries_completed_and_remaining_lists()
```

For each failure test, assert on `exc.completed_uris`, `exc.remaining_uris`,
`exc.completed_batches`, `exc.total_batches`, and `isinstance(exc.cause, SpotifyAPIError)`.

## Interaction with F1

F1's verify still runs after the write. F4 makes the write **raise early** on
HTTP failure — F1's verify would otherwise catch the same problem one round-trip
later. Two reasons to keep both:

1. F4 gives a precise diagnostic (which batch failed, with what HTTP status)
   for Sentry (F5). F1 only sees "the playlist looks wrong".
2. F4 doesn't cover Spotify side-effects that pass HTTP (e.g., silent
   dedupe). F1 catches those.

`PlaylistVerificationError` and `SpotifyPartialBatchError` are distinct types
so F2's rollback handler (which catches `PlaylistVerificationError`) keeps
its narrow scope. Should `SpotifyPartialBatchError` also trigger rollback?
Yes — extend F2's handler:

```python
except (PlaylistVerificationError, SpotifyPartialBatchError) as e:
    JobExecutorService._record_rollback(...)
```

Document this in F2 if F4 ships after F2.

## Verification

```bash
flake8 shuffify/spotify/
pytest tests/spotify/test_api_write_contracts.py -v
pytest tests/ -v
```

Behavioral: in a preview deploy, simulate a 502 on the second POST of a
multi-batch `update_playlist_tracks` by patching `_http.post` to raise on the
second call. Expect `SpotifyPartialBatchError`, JobExecution `failed_rolled_back`
(once F2 is wired), playlist restored from pre-snapshot.

## Rollout note

F4 can ship independently of F1/F2/F3 because today's callers ignore the
return values — changing them from `bool`/`None` to `list[str]` is a strict
superset. The new exceptions, however, will start firing where today's code
silently continued. Tests on the executor side need to be updated to catch
`SpotifyPartialBatchError` if they want to keep asserting "no exception".

Recommended order: ship after F1+F2+F3 so the rollback path is in place when
the new exceptions start surfacing in prod.
