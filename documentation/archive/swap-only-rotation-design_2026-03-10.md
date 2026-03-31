# Design: Simplify Rotation to Swap-Only

**Date:** 2026-03-10
**Status:** Approved

## Overview

Remove archive_oldest and refresh rotation modes, leaving swap as the sole rotation mode. Simplifies the UI and reduces code surface area. Prior implementations preserved in git history.

## Changes

### Enum (`shuffify/enums.py`)
- Remove `ARCHIVE_OLDEST` and `REFRESH` from `RotationMode`, keep only `SWAP`

### Executor (`shuffify/services/executors/rotate_executor.py`)
- Delete `_rotate_archive()` and `_rotate_refresh()` functions
- Remove dispatch branching — go directly to `_rotate_swap()`
- Default mode becomes `swap` instead of `archive_oldest`
- Add TODOs where modes were removed

### Schema Validation (`shuffify/schemas/schedule_requests.py`)
- `rotation_mode` only accepts `swap`
- `target_size` always required for rotate jobs

### Frontend — Workshop (`shuffify/templates/workshop.html`)
- Remove mode dropdown, replace with static description
- Remove "optional/required" hint from cap field — always required
- Cap field placeholder: "e.g. 50"

### Frontend — Schedules Page (`shuffify/templates/schedules.html`)
- Remove mode dropdown from schedule creation modal
- Hardcode swap mode when creating rotation schedules

### Tests
- Delete archive_oldest and refresh test classes
- Keep swap tests, update validation tests to reflect swap-only

## Not Changing
- Rotation count field (stays as-is)
- Protect top N tracks (stays as-is)
- Auto-snapshot behavior
- Archive pair requirement

## Future TODOs
- Re-implement archive_oldest mode (see git history for prior implementation)
- Re-implement refresh mode (see git history for prior implementation)
