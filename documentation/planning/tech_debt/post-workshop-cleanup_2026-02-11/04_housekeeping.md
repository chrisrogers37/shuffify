# Phase 04: Housekeeping — Dependency Updates and .env.example

**PR Title:** `chore: Update dependencies and add .env.example`
**Risk:** Low (requirements files + new file only)
**Effort:** ~1 hour
**Files Changed:** requirements/*.txt, .env.example (new)

---

## Objective

Address two housekeeping items:
1. Update pinned dependencies to their latest compatible versions
2. Create a `.env.example` file so new developers know what environment variables to set

---

## Item 1: Dependency Updates

### Current State (installed versions via `pip list`)

| Package | Pinned In | Current Version | Latest Available | Action |
|---------|-----------|-----------------|------------------|--------|
| Flask | `>=3.1.0` | 3.1.2 | Check PyPI | Verify compatible |
| Flask-Session | `>=0.8.0` | 0.8.0 | Check PyPI | Update if available |
| spotipy | `==2.25.1` | 2.25.1 | Check PyPI | Update if patch available |
| python-dotenv | `==1.1.1` | 1.1.1 | Check PyPI | Update if patch available |
| gunicorn | `==23.0.0` | 23.0.0 | Check PyPI | Update if patch available |
| numpy | `>=1.26.0` | 2.2.5 | Check PyPI | Verify compatible |
| pydantic | `>=2.0.0` | 2.12.5 | Check PyPI | Verify compatible |
| requests | `==2.32.5` | 2.32.5 | Check PyPI | Update if patch available |
| python-jose | `>=3.4.0` | 3.5.0 | Check PyPI | Verify compatible |
| cryptography | `>=43.0.1` | 43.0.3 | Check PyPI | Verify compatible |
| redis | `>=5.0.0` | 5.2.1 | Check PyPI | Verify compatible |
| Flask-SQLAlchemy | `>=3.1.0` | 3.1.1 | Check PyPI | Verify compatible |
| Flask-Migrate | `>=4.0.0` | 4.1.0 | Check PyPI | Verify compatible |
| APScheduler | `>=3.10` | 3.11.2 | Check PyPI | Verify compatible |
| pytest | `==7.4.0` | 7.4.0 | Check PyPI | **Update** (likely 8.x available) |
| pytest-cov | `==4.1.0` | 4.1.0 | Check PyPI | **Update** |
| pytest-mock | `==3.11.1` | 3.11.1 | Check PyPI | **Update** |
| flake8 | `==6.1.0` | 6.1.0 | Check PyPI | **Update** (7.x available) |
| black | `>=24.3.0` | 26.1.0 | Check PyPI | Verify compatible |
| sentry-sdk | `==1.29.2` | 1.29.2 | Check PyPI | **Update** (2.x available) |

### Implementation Steps

1. **Check latest versions:**
   ```bash
   pip install pip-tools  # if not installed
   pip list --outdated
   ```

2. **Update `requirements/base.txt`:**
   - Keep `>=` pins for packages where we want automatic minor updates
   - Update `==` pins to latest patch versions
   - For major version jumps (e.g., sentry-sdk 1.x → 2.x), check migration guides first

3. **Update `requirements/dev.txt`:**
   - pytest: 7.4.0 → latest 8.x (check for breaking changes in test collection/fixtures)
   - pytest-cov: 4.1.0 → latest
   - pytest-mock: 3.11.1 → latest
   - flake8: 6.1.0 → latest (check for new rules that might flag existing code)
   - black: already uses `>=24.3.0`, fine as-is

4. **Update `requirements/prod.txt`:**
   - sentry-sdk: 1.29.2 → 2.x (check Sentry migration guide — likely has breaking changes in initialization)

5. **Test after updates:**
   ```bash
   pip install -r requirements/dev.txt
   pytest tests/ -v
   flake8 shuffify/
   ```

### Update Strategy

- **Patch updates** (e.g., 2.25.1 → 2.25.2): Apply directly
- **Minor updates** (e.g., 7.4.0 → 7.5.0): Apply, run tests
- **Major updates** (e.g., pytest 7→8, sentry-sdk 1→2, flake8 6→7):
  - Read changelog/migration guide
  - Apply in separate commit
  - Run full test suite
  - If tests fail, fix or defer the update

---

## Item 2: Create `.env.example`

### File: `.env.example` (new file in project root)

```bash
# Shuffify Environment Configuration
# Copy this file to .env and fill in your values:
#   cp .env.example .env

# ─── Spotify API (REQUIRED) ───────────────────────────────────
# Get these from: https://developer.spotify.com/dashboard
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REDIRECT_URI=http://localhost:8000/callback

# ─── Flask Configuration ──────────────────────────────────────
# development or production (selects config class in config.py)
FLASK_ENV=development
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=change-me-to-a-random-secret-key

# ─── Redis (recommended) ──────────────────────────────────────
# Used for session storage and Spotify API response caching.
# If not set, falls back to filesystem sessions (no caching).
# Format: redis://[[username]:[password]@]host[:port][/database]
REDIS_URL=redis://localhost:6379/0

# ─── Database ─────────────────────────────────────────────────
# SQLite is used by default. Override for PostgreSQL/MySQL in production.
# SQLALCHEMY_DATABASE_URI=sqlite:///shuffify.db

# ─── Server ───────────────────────────────────────────────────
PORT=8000
```

### Verification

```bash
# Ensure .env.example is NOT gitignored
git check-ignore .env.example
# Should return nothing (not ignored)

# Ensure .env IS gitignored
git check-ignore .env
# Should return: .env
```

### Update `.gitignore` if needed

Verify `.env` is already in `.gitignore` (it should be). Verify `.env.example` is NOT in `.gitignore`.

### Update CLAUDE.md

The development setup section in CLAUDE.md already references `cp .env.example .env` — this was updated in PR #50 (docs review). Confirm the reference exists.

---

## Verification Checklist

```bash
# 1. Install updated dependencies
pip install -r requirements/dev.txt

# 2. Run full test suite
pytest tests/ -v

# 3. Lint check (especially important if flake8 version changes)
flake8 shuffify/

# 4. Verify .env.example exists and is tracked
git ls-files .env.example  # Should show the file
git check-ignore .env.example  # Should return nothing

# 5. Verify .env.example works
cp .env.example .env.test
# Confirm all required vars are present
grep SPOTIFY_CLIENT_ID .env.test && echo "OK"
rm .env.test
```

**Expected outcome:** All tests pass with updated dependencies, .env.example exists and is tracked by git, flake8 clean.

---

## Dependencies

- **Blocks:** None
- **Blocked by:** None (can start immediately)
- **Safe to run in parallel with:** Phase 01, Phase 02, Phase 03

---

## Risk Mitigation

- **If a major update breaks tests:** Revert that specific package to the previous version and defer the update. Don't let one package hold up all other updates.
- **Sentry SDK 2.x:** Has known breaking changes in initialization. If it's complex, pin to latest 1.x instead and defer 2.x migration.
- **Flake8 7.x:** May introduce new lint rules. If it flags existing code, either fix the code (preferred) or add exclusions to `.flake8` config.

---

*Generated by /techdebt scan on 2026-02-11*
