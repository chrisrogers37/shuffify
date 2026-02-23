# Phase 07: Sentry SDK Major Version Update

## Header

| Field | Value |
|-------|-------|
| **Status** | :white_check_mark: COMPLETE |
| **PR** | [#94](https://github.com/chrisrogers37/shuffify/pull/94) |
| **PR Title** | `chore: update sentry-sdk pin from 1.45.1 to 2.x` |
| **Risk Level** | Low |
| **Effort** | Low (15 minutes) |
| **Files Modified** | 1 |
| **Files Created** | 0 |
| **Files Deleted** | 0 |
| **Dependencies** | None |
| **Blocks** | Nothing |

### Files Changed

| File | Action | Description |
|------|--------|-------------|
| `requirements/prod.txt` | Modify | Update sentry-sdk version pin from `==1.45.1` to `>=2.53.0,<3.0` |

---

## Context

The project pins `sentry-sdk==1.45.1` in `requirements/prod.txt`, but **sentry-sdk is not imported, initialized, or referenced anywhere in the application source code** (`shuffify/` directory). The dependency exists as a forward-looking production requirement — it was installed in anticipation of enabling Sentry error tracking but has never been wired into the Flask application.

The current pin at 1.45.1 is a dead-end: sentry-sdk 1.x is no longer receiving feature updates, and the 2.x line (currently at 2.53.0) has been stable since mid-2024. Dependabot is configured to update sentry-sdk for minor and patch versions only (see `.github/dependabot.yml:47`), so it will never auto-propose this major version bump.

Because there is zero application code using sentry-sdk, the upgrade is a pure requirements file change with no migration work. The 2.x breaking changes (Hub removal, `configure_scope()` to `get_current_scope()`, `with_locals` to `include_local_variables`, etc.) are irrelevant since none of these APIs are called.

**Why this matters**: Keeping a stale major version pinned creates confusion — future developers enabling Sentry will follow 2.x documentation and encounter API mismatches if the 1.x package is installed. Updating now removes that trap.

---

## Dependencies

- **Depends on**: Nothing. This phase modifies only `requirements/prod.txt`.
- **Unlocks**: Nothing directly. However, a future "Enable Sentry Integration" phase would benefit from having the 2.x SDK already installed.
- **Parallel safety**: This phase touches only `requirements/prod.txt`. It can run in parallel with any phase that does not modify this file.

---

## Detailed Implementation Plan

### Step 1: Update the sentry-sdk version pin

**File**: `requirements/prod.txt`

**Current content** (line 4):

```
sentry-sdk==1.45.1
```

**New content** (line 4):

```
sentry-sdk>=2.53.0,<3.0
```

**Full file after change**:

```
-r base.txt

# Production
sentry-sdk>=2.53.0,<3.0
psycopg2>=2.9.9
```

**Why `>=2.53.0,<3.0` instead of `==2.53.0`**:
- The `>=2.53.0,<3.0` range allows Dependabot (configured for minor+patch updates) to automatically propose future 2.x updates.
- The `<3.0` upper bound protects against a future 3.x major version being pulled in automatically.
- This matches the project's existing pattern for other production dependencies (e.g., `psycopg2>=2.9.9` in the same file, `Flask>=3.1.3` in `base.txt`).

**Why NOT `==2.53.0`**: An exact pin would require a manual PR for every patch release and defeats the purpose of having Dependabot configured.

### Step 2: Install and verify

After changing the file, install the updated dependency:

```bash
source venv/bin/activate
pip install -r requirements/prod.txt
```

Verify the installed version:

```bash
pip show sentry-sdk
```

Expected output should show `Version: 2.53.0` (or newer).

### What about code changes?

**None required.** The sentry-sdk package is not imported or used in any file under `shuffify/`. Evidence:

- `grep -ri "sentry" shuffify/` returns zero results.
- `grep -ri "import sentry" shuffify/` returns zero results.
- `config.py` has no Sentry DSN configuration.
- `shuffify/__init__.py` (the app factory) has no `sentry_sdk.init()` call.

The 2.x breaking changes documented in the [Sentry 1.x to 2.x migration guide](https://docs.sentry.io/platforms/python/migration/1.x-to-2.x) — Hub removal, scope API changes, configuration option renames — are all irrelevant because no sentry-sdk API is called.

---

## Test Plan

### Existing Tests

No test changes are needed. sentry-sdk is not imported in any test file.

Run the full suite to confirm no transitive dependency conflicts:

```bash
pytest tests/ -v
```

All 1220+ tests must pass.

### Manual Verification Steps

1. **Dependency installation succeeds**:
   ```bash
   pip install -r requirements/prod.txt
   ```
   Must complete without errors or dependency conflicts.

2. **Application starts cleanly**:
   ```bash
   python run.py
   ```
   Verify the app starts without import errors or warnings related to sentry-sdk.

3. **Health endpoint responds**:
   ```bash
   curl http://localhost:8000/health
   ```
   Expected: `{"status": "healthy"}` with HTTP 200.

4. **No sentry-related log noise**: Check the application startup logs for any unexpected sentry-sdk messages. Since the SDK is not initialized, there should be none.

5. **Lint passes**:
   ```bash
   flake8 shuffify/
   ```
   Must return 0 errors (no code changes, but confirm no regressions).

---

## Documentation Updates

### CHANGELOG.md

Add under `## [Unreleased]`:

```markdown
### Changed
- **Sentry SDK** - Updated sentry-sdk pin from 1.45.1 to 2.x (>=2.53.0,<3.0) in production requirements
```

### No other documentation changes needed

- `CLAUDE.md`: No change needed (sentry-sdk is not mentioned in key files or architecture).
- `README.md`: No change needed.
- `documentation/`: No change needed.

---

## Stress Testing & Edge Cases

### Edge Case: Transitive dependency conflicts

sentry-sdk 2.x has dependencies on `urllib3>=1.26.11` and `certifi`. The project already pins `urllib3>=2.6.3` and `certifi>=2026.1.4` in `base.txt`, both of which satisfy sentry-sdk 2.x's requirements. No conflict expected.

### Edge Case: Docker build

The Docker image installs from `requirements/prod.txt`. The version bump will be picked up automatically on the next build. No Dockerfile changes needed.

### Edge Case: Future Sentry enablement

When a developer later adds `sentry_sdk.init(dsn=...)` to the app factory, they must follow the **2.x API**, not 1.x. Key differences for future reference:

- **Initialization**: `sentry_sdk.init(dsn="...", integrations=[FlaskIntegration()])` (same in 2.x)
- **Scope access**: Use `sentry_sdk.get_current_scope()` instead of removed `configure_scope()`
- **Hub**: The `Hub` class is removed in 2.x; use `isolation_scope()` / `new_scope()` instead
- **Config options**: `with_locals` is now `include_local_variables`; `request_bodies` is now `max_request_body_size`

These are informational notes only; no action needed in this phase.

---

## Verification Checklist

- [x] `requirements/prod.txt` shows `sentry-sdk>=2.53.0,<3.0` (not `==1.45.1`)
- [x] `pip install -r requirements/prod.txt` succeeds without errors
- [x] `pip show sentry-sdk` shows version 2.53.0
- [x] `pytest tests/ -v` — all 1220 tests pass
- [x] `flake8 shuffify/` — 0 errors
- [ ] `python run.py` — app starts without sentry-related errors (manual verification)
- [ ] `curl http://localhost:8000/health` — returns `{"status": "healthy"}` (manual verification)
- [x] CHANGELOG.md updated with entry under `[Unreleased]`

---

## What NOT To Do

1. **Do NOT add `sentry_sdk.init()` to the app factory.** This phase is a version pin update only. Enabling Sentry integration is a separate future task that requires a DSN, configuration decisions (sample rates, integrations), and its own test plan.

2. **Do NOT pin to an exact version (`==2.53.0`).** Use `>=2.53.0,<3.0` to allow Dependabot to manage future 2.x patch and minor updates automatically.

3. **Do NOT update `.github/dependabot.yml`.** The existing configuration already groups sentry-sdk under `production-dependencies` for minor+patch updates. Once the pin is at 2.x, Dependabot will handle future 2.x updates automatically.

4. **Do NOT add sentry-sdk to `requirements/base.txt` or `requirements/dev.txt`.** It is a production-only dependency (error tracking is not needed in development/testing). Keep it in `requirements/prod.txt` only.

5. **Do NOT migrate any sentry API calls.** There are none. If you find yourself writing migration code, stop — you are solving a problem that does not exist.
