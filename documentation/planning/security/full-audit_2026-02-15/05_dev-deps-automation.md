# [LOW] Update Dev Dependencies and Add Dependabot

| Field | Value |
|-------|-------|
| **PR Title** | `[LOW] Update dev dependencies and add Dependabot` |
| **Severity** | LOW |
| **Estimated Effort** | 1-2 hours |
| **Branch Name** | `security/update-deps-add-dependabot` |
| **Files Modified** | `requirements/dev.txt`, `Dockerfile`, `.github/dependabot.yml` (new), `CHANGELOG.md` |

---

## Findings Addressed

| Finding | Severity | Description |
|---------|----------|-------------|
| #12 | LOW | No Dependabot/Renovate configured for automated dependency updates |
| #13 | LOW | Dev deps outdated: pytest 7.4.4, mypy 1.19.1, ipython 8.38.0 |
| #14 | LOW | pip 25.2 has 2 CVEs (CVE-2025-8869, CVE-2026-1703), fix version 26.0 |

---

## Dependencies

None. This plan is self-contained and can be implemented at any time.

---

## Detailed Implementation Plan

### Step 1: Update `requirements/dev.txt`

**File**: `requirements/dev.txt`

Change these two lines only:

| Package | Current | Target | Reason |
|---------|---------|--------|--------|
| `pytest` | `7.4.4` | `8.3.5` | Latest bug-fix in 8.3.x. Do NOT use 9.x (major breaking changes affecting 1081 tests) |
| `pytest-cov` | `4.1.0` | `6.0.0` | 4.1.0 does not support pytest 8.x. Version 6.0.0 is compatible with pytest 8.3.x |

**Leave unchanged** (investigation showed these are already at latest stable):
- `mypy==1.19.1` — IS the latest stable release
- `ipython==8.38.0` — IS the final 8.x release; 9.x has breaking API changes

**Before:**
```
pytest==7.4.4
pytest-cov==4.1.0
```

**After:**
```
pytest==8.3.5
pytest-cov==6.0.0
```

---

### Step 2: Upgrade pip in the Dockerfile

**File**: `Dockerfile`

**Before (line 19):**
```dockerfile
RUN pip install --no-cache-dir --upgrade pip>=25.3 setuptools>=78.1.1 wheel>=0.46.2
```

**After:**
```dockerfile
# Upgrade pip, setuptools, and wheel to fix security vulnerabilities
# CVE-2025-8869 + CVE-2026-1703 (pip >=26.0), CVE-2024-6345 (setuptools), CVE-2026-24049 (wheel)
RUN pip install --no-cache-dir --upgrade "pip>=26.0" "setuptools>=78.1.1" "wheel>=0.46.2"
```

**Changes**: Bump pip from `>=25.3` to `>=26.0`. Quote all version specifiers for shell safety.

---

### Step 3: Create `.github/dependabot.yml`

**New file**. Create the directory first: `mkdir -p .github`

**File**: `.github/dependabot.yml`

```yaml
# Dependabot configuration for automated dependency updates
# Docs: https://docs.github.com/en/code-security/dependabot/dependabot-version-updates
version: 2
updates:
  # Python pip dependencies
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "09:00"
      timezone: "America/New_York"
    open-pull-requests-limit: 10
    reviewers:
      - "chrisrogers37"
    commit-message:
      prefix: "deps"
      include: "scope"
    labels:
      - "dependencies"
    # Group minor and patch updates to reduce PR noise
    groups:
      dev-dependencies:
        patterns:
          - "pytest*"
          - "flake8"
          - "black"
          - "isort"
          - "mypy"
          - "safety"
          - "bandit"
          - "ipython"
        update-types:
          - "minor"
          - "patch"
      production-dependencies:
        patterns:
          - "Flask*"
          - "spotipy"
          - "gunicorn"
          - "redis"
          - "pydantic"
          - "cryptography"
          - "requests"
          - "APScheduler"
          - "psycopg2*"
          - "sentry-sdk"
        update-types:
          - "minor"
          - "patch"
```

**Key settings**:
- Weekly on Mondays — not too noisy for a small team
- Grouped updates — one PR for dev deps, one for prod deps
- Major version bumps get individual PRs for careful review
- Auto-assigned reviewer

---

### Step 4: Install Updated Dependencies and Verify

```bash
source venv/bin/activate

# Upgrade pip locally
pip install --upgrade "pip>=26.0"
pip --version  # Verify >= 26.0

# Install updated dev deps
pip install -r requirements/dev.txt

# Verify versions
pip show pytest | grep Version       # Expected: 8.3.5
pip show pytest-cov | grep Version   # Expected: 6.0.0

# Run lint and tests
flake8 shuffify/
pytest tests/ -v
```

**If tests fail after pytest upgrade**, common issues:

| Symptom | Cause | Fix |
|---------|-------|-----|
| `DeprecationWarning` about `pytest.warns` | pytest 8.x made it stricter | Use `with pytest.warns(WarningType):` |
| Import errors from `_pytest` | Private API imports | Replace with public equivalents |
| `pytest-cov` plugin errors | Version mismatch | Verify pytest-cov 6.0.0 installed |

---

### Step 5: Update CHANGELOG.md

Under `## [Unreleased]`, add:

```markdown
### Security
- **pip CVE remediation** - Upgraded minimum pip to >=26.0 in Dockerfile (CVE-2025-8869, CVE-2026-1703)

### Changed
- **Dev dependency updates** - Updated pytest 7.4.4 to 8.3.5, pytest-cov 4.1.0 to 6.0.0
  - Conservative upgrade within 8.x line to avoid pytest 9.0 breaking changes

### Added
- **Dependabot configuration** - Automated weekly dependency monitoring via `.github/dependabot.yml`
  - Grouped updates for dev and production dependencies to reduce PR noise
```

---

## Verification Checklist

- [ ] `requirements/dev.txt` has `pytest==8.3.5` (not 7.4.4, not 9.x)
- [ ] `requirements/dev.txt` has `pytest-cov==6.0.0` (not 4.1.0)
- [ ] `requirements/dev.txt` has `mypy==1.19.1` (unchanged)
- [ ] `requirements/dev.txt` has `ipython==8.38.0` (unchanged)
- [ ] `Dockerfile` has `"pip>=26.0"` (not `>=25.3`)
- [ ] `Dockerfile` version specifiers are quoted
- [ ] `.github/dependabot.yml` exists and is valid YAML
- [ ] `.github/dependabot.yml` has `version: 2`
- [ ] `CHANGELOG.md` has new entries under `[Unreleased]`
- [ ] `pip --version` shows >= 26.0 in local venv
- [ ] `flake8 shuffify/` returns 0 errors
- [ ] `pytest tests/ -v` — all 1081 tests pass
- [ ] `git diff` shows changes only in expected files

---

## What NOT To Do

1. **Do NOT upgrade pytest to 9.x.** Major version with breaking changes. The project has 1081 tests. A pytest 9.x upgrade should be its own dedicated PR, not bundled into security remediation.

2. **Do NOT upgrade ipython to 9.x.** Breaking API changes, no security benefit. Leave at 8.38.0.

3. **Do NOT change `requirements/base.txt` or `requirements/prod.txt`.** This PR only addresses dev dependencies and the Dockerfile pip version.

4. **Do NOT use unpinned versions like `pytest>=8.0`.** This project pins exact versions in dev.txt for reproducibility. Use `==`.

5. **Do NOT remove `mypy==1.19.1` or `ipython==8.38.0`.** Investigation shows these ARE the latest stable versions. The audit finding was slightly inaccurate. Note this in the PR description.

6. **Do NOT manually create the "dependencies" label on GitHub.** Dependabot creates it automatically on its first PR.

7. **Do NOT merge without verifying the YAML is valid.** Invalid `dependabot.yml` fails silently. After pushing, check repo Settings > Code security > Dependabot to confirm it shows "Enabled".

8. **Do NOT quote version specifiers inconsistently in the Dockerfile.** Quote all three for consistency and shell safety.
