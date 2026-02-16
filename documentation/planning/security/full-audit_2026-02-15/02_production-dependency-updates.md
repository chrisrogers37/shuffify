# [HIGH] Update Production Dependencies with Known CVEs

| Field | Value |
|-------|-------|
| **PR Title** | `[HIGH] Update production dependencies with known CVEs` |
| **Severity** | HIGH — six packages with a combined nine CVEs, including TLS/crypto and HTTP transport |
| **Estimated Effort** | 1-2 hours |
| **Branch Name** | `security/update-vulnerable-deps` |
| **Files Modified** | `requirements/base.txt`, `requirements/dev.txt` |
| **Files NOT Modified** | `Dockerfile` (already pins `wheel>=0.46.2`), `requirements/prod.txt` (inherits from base.txt) |

---

## Findings Addressed

| Finding # | Package | Current Version | CVEs | Fix Version |
|-----------|---------|-----------------|------|-------------|
| 2 | cryptography | 43.0.3 | CVE-2024-12797, CVE-2026-26007 | >= 46.0.5 |
| 3 | werkzeug | 3.1.3 | CVE-2025-66221, CVE-2026-21860 | >= 3.1.5 |
| 4 | urllib3 | 2.2.3 | CVE-2025-50182, -50181, -66418, -66471, CVE-2026-21441 | >= 2.6.3 |
| 5 | marshmallow | 3.18.0 | CVE-2025-68480 | >= 3.26.2 |
| 6 | pyasn1 | 0.6.1 | CVE-2026-23490 | >= 0.6.2 |
| 7 | wheel | 0.45.1 | CVE-2026-24049 | >= 0.46.2 |

---

## Dependencies

None. This plan is self-contained.

The Dockerfile already handles `wheel>=0.46.2` (line 19), so Docker builds are already patched for Finding #7.

---

## Detailed Implementation Plan

### Step 1: Create a feature branch

```bash
cd /Users/chris/Projects/shuffify
git checkout -b security/update-vulnerable-deps
```

### Step 2: Update the direct pin in `requirements/base.txt`

**File**: `requirements/base.txt`

**What to change**: Line 10 currently reads:

```
cryptography>=43.0.1
```

Change it to:

```
cryptography>=46.0.5
```

**Why**: `cryptography` is the only vulnerable package already explicitly listed in `base.txt`. Bumping the minimum from `43.0.1` to `46.0.5` ensures pip never resolves to a version affected by CVE-2024-12797 or CVE-2026-26007.

### Step 3: Add explicit minimum pins for vulnerable transitive dependencies

**File**: `requirements/base.txt`

Add the following lines at the bottom of the file, below the existing `APScheduler>=3.10` line:

```
# --- Security: explicit floors for transitive deps with known CVEs ---
# These packages are pulled in by Flask, requests, cryptography, etc.
# pip will NOT upgrade them on its own unless we set a minimum version.
werkzeug>=3.1.5
urllib3>=2.6.3
marshmallow>=3.26.2
pyasn1>=0.6.2
```

**Why this is necessary**:

`werkzeug`, `urllib3`, `marshmallow`, and `pyasn1` are transitive (indirect) dependencies:

| Transitive Dep | Pulled In By |
|----------------|-------------|
| `werkzeug` | `Flask` (WSGI toolkit) |
| `urllib3` | `requests` (HTTP transport) |
| `marshmallow` | `flask-session` / `apispec` (serialization) |
| `pyasn1` | `cryptography` -> `pyOpenSSL` / `python-jose` (ASN.1 parsing) |

The problem: `pip` resolves dependency versions by finding ANY version that satisfies all constraints. If `Flask>=3.1.0` needs `werkzeug>=3.0`, pip might install `werkzeug==3.1.3` (vulnerable) because it satisfies `>=3.0`. Pip does not know about CVEs. By adding `werkzeug>=3.1.5` to our own `base.txt`, we force pip to pick a patched version.

### Step 4: Add `wheel` pin to `requirements/dev.txt`

**File**: `requirements/dev.txt`

Add at the end of the file, after the `ipython` line:

```
# Security: pin wheel to patched version (CVE-2026-24049)
wheel>=0.46.2
```

**Why `dev.txt` and not `base.txt`**: `wheel` is a build tool, not a runtime dependency. The Dockerfile already handles this for production. Adding it to `dev.txt` ensures local development environments are also patched.

### Step 5: Activate the virtual environment and upgrade all packages

```bash
cd /Users/chris/Projects/shuffify
source venv/bin/activate

# Upgrade pip itself first (avoids resolver bugs)
pip install --upgrade pip

# Upgrade wheel first (used during installation of other packages)
pip install --upgrade wheel>=0.46.2

# Install all dependencies, allowing pip to upgrade to meet new minimums
pip install -r requirements/dev.txt --upgrade
```

**What `--upgrade` does**: Without it, pip sees packages are already installed and skips them, even if below the new minimum. `--upgrade` forces re-evaluation.

**Expected outcome** (verify with `pip list`):

| Package | Expected Version |
|---------|-----------------|
| cryptography | >= 46.0.5 |
| werkzeug | >= 3.1.5 |
| urllib3 | >= 2.6.3 |
| marshmallow | >= 3.26.2 |
| pyasn1 | >= 0.6.2 |
| wheel | >= 0.46.2 |

### Step 6: Verify CVEs are resolved with `pip-audit`

```bash
pip install pip-audit
pip-audit
```

The output should show NO findings for the six packages in this plan. If `pip-audit` reports other unrelated vulnerabilities, note them but do not block this PR.

If a package did not upgrade, another dependency may have an upper-bound constraint blocking it. Debug with `pip show <package>` and check for conflicts.

### Step 7: Run the full test suite and lint checks

```bash
flake8 shuffify/
pytest tests/ -v
```

**If tests fail**, the most likely causes:

- **`cryptography` (43 -> 46)**: Major version jump. Check `shuffify/services/token_service.py` (Fernet). Fernet's API is stable, so breakage is unlikely.
- **`marshmallow` (3.18 -> 3.26)**: No direct imports in application code — should be safe.
- **`werkzeug` (3.1.3 -> 3.1.5)**: Patch-level bump — extremely unlikely to break.
- **`urllib3` (2.2.3 -> 2.6.3)**: Minor version bump, abstracted by `requests` — unlikely to break.

### Step 8: Commit, push, and create PR

```bash
git add requirements/base.txt requirements/dev.txt
git commit -m "[HIGH] Update production dependencies with known CVEs

Bump cryptography>=46.0.5 (was >=43.0.1) to fix CVE-2024-12797, CVE-2026-26007.
Add explicit pins for transitive deps with CVEs:
- werkzeug>=3.1.5 (CVE-2025-66221, CVE-2026-21860)
- urllib3>=2.6.3 (CVE-2025-50182, -50181, -66418, -66471, CVE-2026-21441)
- marshmallow>=3.26.2 (CVE-2025-68480)
- pyasn1>=0.6.2 (CVE-2026-23490)
- wheel>=0.46.2 in dev.txt (CVE-2026-24049)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

git push -u origin security/update-vulnerable-deps
```

---

## Verification Checklist

| # | Check | Command | Expected Result |
|---|-------|---------|-----------------|
| 1 | cryptography version | `pip show cryptography \| grep Version` | >= 46.0.5 |
| 2 | werkzeug version | `pip show werkzeug \| grep Version` | >= 3.1.5 |
| 3 | urllib3 version | `pip show urllib3 \| grep Version` | >= 2.6.3 |
| 4 | marshmallow version | `pip show marshmallow \| grep Version` | >= 3.26.2 |
| 5 | pyasn1 version | `pip show pyasn1 \| grep Version` | >= 0.6.2 |
| 6 | wheel version | `pip show wheel \| grep Version` | >= 0.46.2 |
| 7 | No audit findings | `pip-audit` | No findings for these 6 packages |
| 8 | Lint passes | `flake8 shuffify/` | 0 errors |
| 9 | Tests pass | `pytest tests/ -v` | All 1081 tests pass |
| 10 | base.txt has pins | `grep -E "werkzeug\|urllib3\|marshmallow\|pyasn1\|cryptography" requirements/base.txt` | All 5 listed |
| 11 | dev.txt has wheel | `grep wheel requirements/dev.txt` | `wheel>=0.46.2` |
| 12 | Dockerfile unchanged | `git diff Dockerfile` | No changes |

---

## What NOT To Do

1. **Do NOT pin exact versions with `==`.** Using `cryptography==46.0.5` prevents pip from installing future patch releases with additional security fixes. Always use `>=` for security floor pins.

2. **Do NOT edit `requirements/prod.txt`.** It inherits from `base.txt` via `-r base.txt`. Adding pins there creates duplication and potential conflicts.

3. **Do NOT edit the `Dockerfile`.** It already contains `wheel>=0.46.2` (line 19) and runs `pip install -r requirements/prod.txt` which picks up all `base.txt` changes.

4. **Do NOT run `pip install --upgrade` without the `-r` flag.** Running bare `pip install --upgrade cryptography` upgrades only that package. Using `-r requirements/dev.txt --upgrade` upgrades the entire tree consistently.

5. **Do NOT remove the comment block explaining why transitive deps are pinned.** Future developers will wonder why Flask sub-dependencies are listed explicitly. The comment explains this.

6. **Do NOT skip `pip-audit` verification.** `pip show` confirms installed versions but `pip-audit` cross-references the actual CVE database. Always run both.

7. **Do NOT merge if any tests fail.** Even if versions look correct, a failing test means the upgrade broke something. The 1081-test suite is the safety net.

8. **Do NOT add `pip-audit` to `requirements/dev.txt` as part of this PR.** It's a one-time verification tool. Adding it permanently is a separate decision.
