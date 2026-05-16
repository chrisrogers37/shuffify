# F3 — Correct the rotate expected-URI computation

**Investigation:** [00_INVESTIGATION.md](00_INVESTIGATION.md)
**Targets:** RC2 (wrong baseline in rotate Phase 2)
**Depends on:** F1 (uses `verify_playlist_state` for the verify step)
**Risk:** Low. Effort: Small.

## Context

The current rotate verifier baseline is the **original** production size, used
verbatim at `rotate_executor.py:507-510`:

```python
actual_total = _verify_playlist_size(
    api, target_id, len(prod_uris),
    schedule.id, "swap",
)
```

`Phase 2` swap math is:

- `swap_in_uris = archive_uris[:rotation_count]`
- `swap_out_uris = _sample_at_most(eligible_uris, len(swap_in_uris))`
- After writes: production has `(prod_uris ∖ swap_out_uris) ∪ swap_in_uris`

When `eligible_uris` is shrunk (high `protect_count`, many locked tracks, small
playlist), `swap_out_uris` ends up shorter than `swap_in_uris` — so the actual
final size is `len(prod_uris) + (len(swap_in_uris) − len(swap_out_uris))`, not
`len(prod_uris)`. The verifier was comparing against the wrong target. Even
F1's strict check would compare against the wrong expected list without this
fix.

`Phase 1` (overflow) already passes the correct expected count
(`len(prod_uris) - len(overflow_uris)` at line 444) but does not provide a URI
list — F1 needs one.

## Files touched

| File | Change |
|------|--------|
| `shuffify/services/executors/rotate_executor.py` | Replace verifier call sites with multiset-correct expected URI lists |
| `tests/services/executors/test_rotate_executor.py` | Add tests for asymmetric-swap, depleted-pool, and overflow paths |

## Changes (concrete)

### Phase 1 (overflow), replace lines 443-457

Current:
```python
expected = len(prod_uris) - len(overflow_uris)
actual_total = _verify_playlist_size(
    api, target_id, expected,
    schedule.id, "overflow removal",
)
```

New:
```python
overflow_set = set(overflow_uris)
expected_uris = [u for u in prod_uris if u not in overflow_set]
verified = JobExecutorService.verify_playlist_state(
    api, target_id, expected_uris, schedule.id, "overflow",
)
actual_total = len(verified)
```

### Phase 2 (swap), replace lines 502-510

Current:
```python
swapped = min(
    len(swap_in_uris),
    len(swap_out_uris),
)

actual_total = _verify_playlist_size(
    api, target_id, len(prod_uris),
    schedule.id, "swap",
)
```

New:
```python
swap_out_set = set(swap_out_uris)
expected_uris = [u for u in prod_uris if u not in swap_out_set]
expected_uris.extend(swap_in_uris)

verified = JobExecutorService.verify_playlist_state(
    api, target_id, expected_uris, schedule.id, "swap",
)
actual_total = len(verified)

# Report swap pair count (not symmetric — depleted pool can shorten swap_out)
swapped = min(len(swap_in_uris), len(swap_out_uris))
```

### Order semantics

Spotify's `playlist_add_items` adds at the end by default. Production today
ends up as `(prod_uris ∖ swap_out_uris) ++ swap_in_uris` (append order). The
expected URI list above matches that exactly. F1 compares as a **multiset**,
so even if Spotify reorders by a position quirk, F1 won't false-fail — it
only requires same membership and same multiplicities.

### Archive verification

The rotate swap also writes to the archive playlist (lines 487-488 in Phase 2,
lines 440-442 in Phase 1). The expected archive state is
`archive_uris ∪ new_to_archive` (Phase 1 + Phase 2) or
`(archive_uris ∖ swap_in_uris) ∪ new_to_archive` (Phase 2 after swap-in
removal at line 491-495). Add a second `verify_playlist_state` call for the
archive playlist in both phases for symmetry:

```python
# After archive writes complete:
expected_archive = [
    *(u for u in archive_uris if u not in set(swap_in_uris)),
    *new_to_archive,
]
JobExecutorService.verify_playlist_state(
    api, archive_id, expected_archive, schedule.id, "swap archive",
)
```

Whether to include this in F3 or save it for a follow-up: include it.
Rotation correctness means both halves of the swap are valid. The verify
cost is one extra paginated fetch per archive — cheap, ~1s for most users.

## Tests (`tests/services/executors/test_rotate_executor.py`)

Add cases:

```python
def test_swap_with_full_eligible_pool_passes()
def test_swap_with_depleted_pool_due_to_locks()       # asymmetric counts
def test_swap_with_high_protect_count()               # asymmetric counts
def test_overflow_uses_correct_expected_uri_list()
def test_archive_state_verified_after_swap()
def test_archive_state_verified_after_overflow()
```

Each uses `unittest.mock.Mock` for `SpotifyAPI`, asserts on the multiset of
URIs passed to `verify_playlist_state` (since F1 raises on diff, the test
controls the mock's `get_playlist_tracks` return to be exactly the expected
URIs).

Update any existing `test_rotate_executor.py` cases that mock
`_verify_playlist_size` (now removed) to mock `verify_playlist_state`.

## Verification

```bash
flake8 shuffify/services/executors/rotate_executor.py
pytest tests/services/executors/test_rotate_executor.py -v
pytest tests/ -v
```

End-to-end:

1. Construct a 10-track playlist with 5 locked + 5 unlocked.
2. Run a rotation with `rotation_count=8`. `swap_in_uris` will be 8;
   `swap_out_uris` will be capped at 5.
3. Expected final size = 10 − 5 + 8 = 13.
4. With F3 + F1, this passes verification. Without F3, F1 would raise because
   it would have been told to expect 10.

## Rollout note

Ship in the same release as F1 (and ideally F2). F3 only fixes a baseline
that nothing else looks at today — its value materializes once F1 is enforcing.
