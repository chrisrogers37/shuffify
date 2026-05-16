# WOOKLYN Silent Track-Loss — Investigation

**Session:** `wooklyn-silent-loss`
**Date:** 2026-05-16
**Reporter:** chrisrogers37
**Platform:** DigitalOcean App Platform (Flask, single instance)

---

## Problem

User's **WOOKLYN** Spotify playlist is silently losing songs during scheduled
rotations on Shuffify. The rotate executor logs warnings about size drift but
the parent job is still marked `success`. Shuffle / drip / raid executors have
no post-write verification at all. The user's read: *"rotates should check
against the target song counter. This [verify+fallback] can wrap shuffles."*

## Smoking gun

DigitalOcean runtime-log buffer captured on **2026-05-13 09:00:09 UTC**:

```
WARNI [shuffify.services.executors.rotate_executor]
Schedule 11: swap size mismatch — expected 241 tracks, got 240
```

`Schedule 11` is the playlist's rotation. The drift was exactly one track and
the job completed successfully — direct evidence of the silent-loss class.

## Environment

| Item | Value |
|------|-------|
| DO app ID | `1ac416ce-832e-4f93-bcef-f0624c5f81c5` (`shuffify`) |
| Instance count | 1 (no APScheduler concurrency) |
| Last deploy | 2026-05-11 (commit `0e21cef`) |
| Sentry DSN | NOT configured (sentry-sdk installed) |
| Log forwarder | None |
| DO log buffer | Small; historical losses age out |

## Recent code changes that plausibly amplify the issue

- `c51f31b` — Per-track position locks. Locks are excluded from the rotate
  eligible pool (`rotate_executor.py:386-393`), which can make `swap_out_uris`
  shorter than `swap_in_uris` even when `archive_uris` is full.
- `e91c750` — Rotation LIFO → FIFO. Changed swap-in order but kept the same
  count-only verifier.
- No edits to `spotify/api.py` or `spotify/http_client.py` in the last 60 days.

## Root-cause analysis

| # | Category | Finding | Confidence |
|---|----------|---------|------------|
| RC1 | Rotate verifier permissive | `_verify_playlist_size` (`rotate_executor.py:214-253`) only raises when drift exceeds 50% of expected (`threshold = max(1, expected // 2)`). Smaller drifts log a warning and the job is marked `success`. | **High** |
| RC2 | Rotate expected-count baseline is wrong | Expected count passed in Phase 2 swap is `len(prod_uris)` — the **original** size — at `rotate_executor.py:507-510`. When `swap_in_uris` and `swap_out_uris` diverge (locks/protect/depleted pool), the real post-swap size is `len(prod_uris) + (len(swap_in) - len(swap_out))`. The verifier is comparing against the wrong target. | **High** |
| RC3 | Shuffle has no post-write verification | `SpotifyAPI.update_playlist_tracks` (`spotify/api.py:344-385`) PUTs batch 1 then POSTs batches 2+. It returns `True` regardless of whether later batches landed. Shuffle executor reports `tracks_total = len(shuffled_uris)` (`shuffle_executor.py:178`) without re-fetching. A network blip on a 200+ track shuffle silently truncates to ~100. | **High** |
| RC4 | Drip is fire-and-forget | `drip_executor.py:127-130` calls `playlist_add_items` then `playlist_remove_items` with no return-value check. `_mark_dripped_as_promoted` runs after, so tracks can be marked PROMOTED in the DB without ever having landed in the target. | **High** |
| RC5 | Raid swallows failures | `_add_to_raid_playlist` (`raid_executor.py:198-210`) wraps `playlist_add_items` in `try/except Exception` and logs `warning`. `tracks_added` reported to JobExecution is the staged DB count, not the Spotify reality. | **Medium** |
| RC6 | Snapshots taken but never used for rollback | `_auto_snapshot_before_{rotate,shuffle,raid,drip}` snapshot the pre-state. `PlaylistSnapshotService.restore_snapshot` (`playlist_snapshot_service.py:149-178`) returns URIs — caller must re-apply via Spotify API. No executor has a rollback call site. The "we can revert" capability is dormant. | **High** |
| RC7 | Activity log records executor's claim, not reality | `_record_success` (`base_executor.py:117-166`) logs `tracks_added`/`tracks_total` from the executor's return dict, not from a re-fetch. Audit trail can't distinguish "succeeded" from "succeeded with silent drift". | **High** |
| RC8 | No production observability | `SENTRY_DSN` not configured. DO buffer is small. We have no way to know how many rotations have already silently dropped tracks. | **High** |

## Decisions taken with the user

| Decision | Value |
|----------|-------|
| Fix scope | F1+F2+F3+F4+F5+F6 (full slate) |
| Verifier mode | URI **multiset** compare (catches missing **and** substituted tracks) |
| Rollback policy | Full snapshot rollback on any verifier mismatch |

## Fix plan index

Each fix is an independently shippable PR. Suggested implementation order is
listed; the user's `/claudna:implement-plan` workflow can drive them in this
sequence:

| # | File | Title | Recommended order |
|---|------|-------|-------------------|
| F6 | [`06_neon_forensic_sql.md`](06_neon_forensic_sql.md) | Read-only WOOKLYN loss timeline + restore candidates | **1st** (no code; tells us scope before we change anything) |
| F1 | [`01_verify_wrapper.md`](01_verify_wrapper.md) | `BaseExecutor.verify_playlist_state` URI-set verifier | 2nd |
| F3 | [`03_rotate_expected_math.md`](03_rotate_expected_math.md) | Correct rotate expected-URI computation | 3rd (lands with F1) |
| F2 | [`02_snapshot_rollback.md`](02_snapshot_rollback.md) | Auto-rollback on `PlaylistVerificationError` | 4th |
| F4 | [`04_spotify_write_api.md`](04_spotify_write_api.md) | Tighten Spotify multi-batch write contract | 5th |
| F5 | [`05_sentry_wiring.md`](05_sentry_wiring.md) | Sentry init + executor-level WARNING capture | 6th |

## Defense-in-depth picture (after all fixes land)

```
┌──────────────────────────────────────────────────────────────────┐
│  JobExecutorService.execute  (base_executor.py:40)               │
│    └─ pre-snapshot (already exists)                              │
│    └─ executor write                                              │
│         └─ F4: Spotify API raises on per-batch shortfall          │
│    └─ F1: verify_playlist_state(expected_uris)                   │
│         └─ raises PlaylistVerificationError on multiset diff      │
│    └─ F2: catch PVE → restore_snapshot → re-apply via API         │
│         status='failed_rolled_back', ActivityLog has diff detail  │
│    └─ F5: Sentry captures WARNING/ERROR; tagged with schedule_id  │
└──────────────────────────────────────────────────────────────────┘
F6 (forensic SQL) runs out-of-band against Neon to assess past damage.
```

Three layers of protection: the write API itself raises on partial
batches (F4), the executor verifies post-write state via URI multiset
(F1+F3), and on failure the pre-snapshot is automatically restored (F2).

## Verification (after fixes land)

Per project CLAUDE.md pre-push checklist:

```
flake8 shuffify/ && pytest tests/ -v
```

Per-fix targeted tests live alongside each fix doc. End-to-end:

1. Run F6's SQL in Neon read-only — capture WOOKLYN's actual loss timeline and
   identify the most recent intact snapshot the user can restore from today.
2. Stage F1+F3 on a preview deploy, run a controlled rotation on a throwaway
   playlist where one URI is yanked mid-flight (simulate via mocked API). Expect
   `PlaylistVerificationError` and `JobExecution.status = 'failed'`.
3. Stage F2, repeat (2). Expect `status = 'failed_rolled_back'` and the
   playlist URI list matches the pre-snapshot exactly.
4. Stage F5, repeat (1). Expect the captured warning to appear as a Sentry
   event tagged with `schedule_id`, `playlist_id`, `job_type`.

## Out of scope

- "Corrective top-up" rollback (user chose hard snapshot rollback).
- Concurrency hardening — single DO instance; rotations don't overlap.
- The `added_at` reorder-API migration tracked in `project_shuffle_reorder_investigation.md`.
  F4 makes that migration safer but doesn't depend on it.
