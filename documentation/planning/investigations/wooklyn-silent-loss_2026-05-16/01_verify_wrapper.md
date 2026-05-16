# F1 — `verify_playlist_state` URI-set verifier

**Investigation:** [00_INVESTIGATION.md](00_INVESTIGATION.md)
**Targets:** RC1, RC2 (partial — F3 finishes RC2), RC3, RC4, RC5
**Risk:** Low. Effort: Medium.

## Context

Every executor that writes to a playlist has either a 50%-tolerance count
check (rotate) or no check at all (shuffle, raid, drip). Direct evidence:

```
WARNI [shuffify.services.executors.rotate_executor]
Schedule 11: swap size mismatch — expected 241 tracks, got 240
```

This fix introduces a single shared helper, `verify_playlist_state`, on
`JobExecutorService`. It re-fetches the playlist after writes and compares
the actual track URIs to the expected URIs as a **multiset** (a playlist
can legitimately contain duplicates). On any diff it raises a new
`PlaylistVerificationError` carrying `expected`, `actual`, `missing`, and
`extra` for downstream rollback (F2) and Sentry capture (F5).

## Files touched

| File | Change |
|------|--------|
| `shuffify/services/executors/base_executor.py` | New `PlaylistVerificationError` exception; new `JobExecutorService.verify_playlist_state(api, playlist_id, expected_uris, schedule_id, phase)` static method |
| `shuffify/services/executors/rotate_executor.py` | Replace `_verify_playlist_size` call sites (lines 445-448 and 507-510) with `verify_playlist_state`; remove `_verify_playlist_size` |
| `shuffify/services/executors/shuffle_executor.py` | After `api.update_playlist_tracks(...)` (line 157-159), call `verify_playlist_state(api, target_id, shuffled_uris, schedule.id, "shuffle")` |
| `shuffify/services/executors/drip_executor.py` | After the add+remove pair (line 127-130), call `verify_playlist_state` for **both** target (expect `drip_uris + previous_target_uris`) and raid (expect previous raid URIs minus `drip_uris`) |
| `shuffify/services/executors/raid_executor.py` | After `_add_to_raid_playlist` (line 106-108), if a raid link exists, call `verify_playlist_state` against the raid playlist (expect previous raid URIs ∪ `new_uris`). Remove the `try/except Exception → warning` at line 198-210 in favor of letting `verify_playlist_state` surface the failure |
| `tests/services/executors/test_verify_playlist_state.py` | New test module (see below) |

## Exception design

`PlaylistVerificationError` lives in `base_executor.py` (same file as
`JobExecutionError`) and **subclasses `JobExecutionError`** so existing
`except JobExecutionError` blocks already in `execute_rotate`, etc., catch
it without changes. F2 will add a narrower handler in
`JobExecutorService.execute`.

```python
class PlaylistVerificationError(JobExecutionError):
    """Raised when post-write playlist state diverges from expected."""

    def __init__(
        self,
        playlist_id: str,
        expected: list[str],
        actual: list[str],
        schedule_id: int,
        phase: str,
    ):
        self.playlist_id = playlist_id
        self.expected = expected
        self.actual = actual
        self.schedule_id = schedule_id
        self.phase = phase

        from collections import Counter
        exp = Counter(expected)
        act = Counter(actual)
        # multiset diff (preserves duplicate-count semantics)
        self.missing = list((exp - act).elements())
        self.extra = list((act - exp).elements())

        super().__init__(
            f"Schedule {schedule_id}: {phase} verification failed — "
            f"expected {len(expected)} tracks, got {len(actual)}, "
            f"missing {len(self.missing)}, extra {len(self.extra)}"
        )
```

## Helper

```python
@staticmethod
def verify_playlist_state(
    api: SpotifyAPI,
    playlist_id: str,
    expected_uris: list[str],
    schedule_id: int,
    phase: str,
) -> list[str]:
    """Re-fetch playlist and verify URI multiset matches expected.

    Returns the actual URI list on success. Raises
    PlaylistVerificationError on any multiset divergence.
    """
    verified = api.get_playlist_tracks(playlist_id)
    actual_uris = [
        t["uri"] for t in (verified or []) if t.get("uri")
    ]

    from collections import Counter
    if Counter(actual_uris) != Counter(expected_uris):
        raise PlaylistVerificationError(
            playlist_id=playlist_id,
            expected=expected_uris,
            actual=actual_uris,
            schedule_id=schedule_id,
            phase=phase,
        )
    return actual_uris
```

Cache note: `SpotifyAPI.get_playlist_tracks` reads through `SpotifyCache` with
a 60s TTL. The pre-write fetch and the post-write verify could collide on a
stale cache hit. **The verify must bypass the cache.** Either:

1. Call `cache.invalidate_playlist(playlist_id)` before `get_playlist_tracks`, OR
2. Add a `bypass_cache=True` kwarg to `get_playlist_tracks` and thread it through.

Recommended: option 1 — it's a one-liner and doesn't change the API surface.
The write paths already invalidate the cache (e.g., `update_playlist_tracks`
at `spotify/api.py:380-383`), but `playlist_add_items` / `playlist_remove_items`
must also be checked to ensure invalidation runs before the verify reads.

## Call-site changes (concrete)

### Rotate (`rotate_executor.py`)

Replace lines 445-448:
```python
expected = len(prod_uris) - len(overflow_uris)
actual_total = _verify_playlist_size(
    api, target_id, expected,
    schedule.id, "overflow removal",
)
```
With:
```python
expected_uris = [u for u in prod_uris if u not in set(overflow_uris)]
verified = JobExecutorService.verify_playlist_state(
    api, target_id, expected_uris, schedule.id, "overflow",
)
actual_total = len(verified)
```

Replace lines 507-510 — see F3, which computes `expected_uris` correctly
for the asymmetric-swap case. F1's helper is the same; only the inputs
change.

Remove `_verify_playlist_size` (lines 214-253).

### Shuffle (`shuffle_executor.py`)

After line 157-159 (`api.update_playlist_tracks(target_id, shuffled_uris)`),
add before line 162:

```python
JobExecutorService.verify_playlist_state(
    api, target_id, shuffled_uris, schedule.id, "shuffle",
)
```

This catches the multi-batch-truncation case (RC3) at the executor layer
even before F4 tightens the API layer.

### Drip (`drip_executor.py`)

After line 130 (`api.playlist_remove_items(raid_id, drip_uris)`), before
the DB write at 131:

```python
prev_target_uris = [
    t.get("uri") for t in target_tracks if t.get("uri")
]
expected_target = drip_uris + prev_target_uris  # add at position=0
JobExecutorService.verify_playlist_state(
    api, target_id, expected_target, schedule.id, "drip target",
)

# Verify raid playlist: removed drip_uris from prev raid contents.
expected_raid = [u for u in raid_uris if u not in set(drip_uris)]
JobExecutorService.verify_playlist_state(
    api, raid_id, expected_raid, schedule.id, "drip raid",
)
```

If verification raises, `_mark_dripped_as_promoted` is skipped — which is
correct, because the DB state should not advance when Spotify state is bad.

### Raid (`raid_executor.py`)

Replace lines 198-210 (`_add_to_raid_playlist`) so it returns the previous
raid URIs to the caller, OR move the verification into the caller around
lines 106-108:

```python
link = RaidLinkService.get_link_for_playlist(
    schedule.user_id, target_id
)
if link:
    prev_raid_uris = [
        t.get("uri")
        for t in api.get_playlist_tracks(link.raid_playlist_id) or []
        if t.get("uri")
    ]
else:
    prev_raid_uris = None

_add_to_raid_playlist(
    api, schedule.user_id, target_id, new_uris,
)

if link:
    expected_raid = prev_raid_uris + new_uris
    JobExecutorService.verify_playlist_state(
        api, link.raid_playlist_id, expected_raid,
        schedule.id, "raid pull",
    )
```

Drop the `try/except Exception → warning` inside `_add_to_raid_playlist` so
real errors propagate. The verify call above replaces the silent warning.

## Tests (`tests/services/executors/test_verify_playlist_state.py`)

```python
def test_verify_match_returns_actual()
def test_verify_count_drift_raises()
def test_verify_uri_substitution_raises()       # same count, wrong tracks
def test_verify_duplicates_match()               # legit duplicate URIs OK
def test_verify_duplicates_diverge()             # 2 copies expected, 1 actual
def test_verify_paginated_fetch()                # >100 tracks, mock paginates
def test_verify_empty_playlist()
def test_verify_invalidates_cache_before_fetch() # asserts cache.invalidate_* called
```

Mock `api.get_playlist_tracks` with `unittest.mock.Mock`. Use
`PlaylistVerificationError` in `pytest.raises(...)` and assert on
`exc.missing`, `exc.extra`, `exc.phase`, `exc.schedule_id`.

Also update existing executor tests that assert on
`_verify_playlist_size` to use `verify_playlist_state` instead.

## Verification

```bash
flake8 shuffify/
pytest tests/services/executors/test_verify_playlist_state.py -v
pytest tests/ -v
```

Behavioral: deploy to a preview environment, run a controlled rotation on
a throwaway playlist where one URI is deleted between the executor's pre-fetch
and the verify call (mocked). Expect the JobExecution to land with
`status='failed'` and `error_message` containing `expected …, got …, missing 1`.

## Rollout note

F1 alone is **stricter than today's behavior** — rotations that currently
land in the warn-only zone will start failing once F1 ships. Without F2 (auto
rollback) the playlist will be left in the broken state after F1 raises, so
F1 should ship in the same release as F2 (or one preview deploy before F2)
to avoid degrading user experience while we wait on the rollback path.
