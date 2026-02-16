# [CRITICAL] Rotate Exposed Credentials and Document Rotation Procedure

**Status**: ✅ COMPLETE
**Started**: 2026-02-16
**Completed**: 2026-02-16
**PR**: #69

| Field | Value |
|-------|-------|
| **PR Title** | `[CRITICAL] Rotate exposed credentials and document rotation procedure` |
| **Severity** | CRITICAL |
| **Effort Estimate** | 2-3 hours (manual credential rotation + documentation) |
| **Files Modified** | `documentation/guides/credential-rotation.md` (new file) |
| **Files NOT Modified** | `.env` (gitignored, updated manually outside version control), `config.py`, application source code |

---

## Findings Addressed

**Finding #1 (CRITICAL)**: Real credentials were committed to git history in commits `cdf15d9` and `7a03250`. The `.env` file was tracked at those points and contained:

- Spotify API client ID and client secret
- Neon PostgreSQL database URL (including password)
- Flask `SECRET_KEY`

The `.env` file is currently gitignored (confirmed in `.gitignore` at lines 36-38) and is no longer tracked in the working tree. However, the credentials remain fully readable in the git history of the repository.

---

## Dependencies

None. This remediation is entirely standalone.

---

## Detailed Implementation Plan

### Step 1: Rotate Spotify API Credentials

The Spotify client ID and client secret were exposed. Both must be rotated.

1. Navigate to **https://developer.spotify.com/dashboard**.
2. Log in with the Spotify account that owns the Shuffify application.
3. Select the **Shuffify** application from the dashboard.
4. Navigate to **Settings**.
5. Locate the **Client Secret** section. Click **RESET CLIENT SECRET**.
6. **Important**: The old client secret will be immediately invalidated. Any running instance of Shuffify will lose the ability to authenticate until the new secret is deployed.
7. Copy the new client secret and store it in a password manager. Do **not** paste it into any file that is tracked by git.
8. Note: The **Client ID** cannot be rotated independently on Spotify. If you want a completely fresh credential pair, create a new application:
   - Click **Create App** on the dashboard.
   - Set the **Redirect URI** to match your production value.
   - Save the new Client ID and Client Secret.
   - Delete the old Spotify app after verifying the new one works.

### Step 2: Rotate Neon Database Password

The Neon PostgreSQL connection string (including the password) was exposed.

1. Navigate to **https://console.neon.tech**.
2. Log in to the account that owns the Shuffify database.
3. Select the Shuffify project.
4. Go to **Roles** (under the branch settings or the "Roles" sidebar item).
5. Find the database role used in the exposed `DATABASE_URL`.
6. Click **Reset Password** next to that role.
7. Neon will generate a new password. Copy it immediately — Neon only shows it once.
8. Construct the new `DATABASE_URL` (do NOT commit this anywhere):
   ```
   postgres://<user>:<NEW_PASSWORD>@<endpoint>.neon.tech/<dbname>?sslmode=require
   ```
9. **Timing note**: The old password is invalidated immediately. Coordinate with Step 5 (updating deployment environments) to minimize downtime.

### Step 3: Generate a New Flask SECRET_KEY

The Flask `SECRET_KEY` was exposed. It is used for two critical purposes:

1. **Session cookie signing** — Flask uses it to cryptographically sign session cookies.
2. **Fernet encryption key derivation** — `TokenService` (in `shuffify/services/token_service.py`) derives a Fernet key from `SECRET_KEY` using PBKDF2 with a fixed salt. This encrypts Spotify refresh tokens stored in the `users.encrypted_refresh_token` database column.

**Generate the new key:**

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Critical side effects of changing SECRET_KEY:**

1. **All existing Flask sessions become invalid.** Every active user will be logged out and must re-authenticate via Spotify OAuth. This is unavoidable and acceptable.

2. **All stored encrypted refresh tokens become undecryptable.** With a new `SECRET_KEY`, `TokenService.decrypt_token()` will raise `TokenEncryptionError` for every stored token.

   **Recommended approach (simple, acceptable for a small user base):** Accept that all stored refresh tokens are now invalid. Do NOT attempt a decrypt-then-re-encrypt migration. Instead:

   - NULL out the `encrypted_refresh_token` column for all users in the database. Run this SQL against the Neon database (after Step 2, using the new password):

     ```sql
     UPDATE users SET encrypted_refresh_token = NULL;
     ```

   - When users next log in via OAuth, the callback handler in `shuffify/routes/core.py` (line 263) will re-encrypt their new refresh token with the new `SECRET_KEY`-derived Fernet key automatically.

   - **Impact on scheduled jobs**: Any user with an active schedule will have their scheduled jobs fail until they re-authenticate. The `job_executor_service.py` (line 240) checks for `encrypted_refresh_token` and raises an error if it is `None`. This self-heals when users log in again.

### Step 4: Update Local .env with New Credentials

Open your local `.env` file (gitignored, not tracked) and update these values:

```bash
# Replace with the new values from Steps 1-3.
SPOTIFY_CLIENT_ID=<new client ID from Step 1, or same if only secret was rotated>
SPOTIFY_CLIENT_SECRET=<new client secret from Step 1>
SECRET_KEY=<64-char hex string from Step 3>
DATABASE_URL=<new connection string from Step 2>
```

**Verify `.env` is not tracked:**

```bash
git ls-files --error-unmatch .env
# Expected: "error: pathspec '.env' did not match any file(s) known to git"
```

### Step 5: Update Deployment Environments

| Environment | How to Update | Variables to Change |
|---|---|---|
| **Local development** | Edit `.env` file directly (Step 4) | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SECRET_KEY`, `DATABASE_URL` |
| **Docker Compose (local)** | `docker-compose.yml` uses `env_file: .env`, so updating `.env` is sufficient. Restart with `docker-compose down && docker-compose up` | Same as above |
| **Production deployment** | Update environment variables in hosting platform dashboard | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL` (if also exposed) |

**For each environment, after updating credentials:**

1. Restart the application.
2. Verify health check: `curl http://<host>:8000/health`
3. Verify OAuth login flow completes successfully.

### Step 6: Consider Git History Cleanup

**Trade-offs of git history rewrite:**

| Factor | Keep History As-Is | Rewrite History |
|---|---|---|
| **Security** | Credentials visible (but rotated, so harmless) | Credentials removed |
| **Collaboration impact** | None | All clones/forks invalidated, collaborators must re-clone |
| **CI/CD impact** | None | Any SHA-pinned CI will break |
| **Effort** | None | 30-60 minutes |
| **Risk** | Low (credentials already rotated) | Moderate (could corrupt history) |

**If you decide to rewrite history**, use `git filter-repo` (recommended):

```bash
# Back up first
cp -r /Users/chris/Projects/shuffify /Users/chris/Projects/shuffify-backup

# Remove .env from all history
git filter-repo --path .env --invert-paths

# Force push (REWRITES ALL COMMIT HASHES)
git push origin --force --all
git push origin --force --tags
```

**Recommendation**: Rewriting is optional but recommended. Credential rotation (Steps 1-3) is the real security fix. History cleanup is defense-in-depth.

### Step 7: Create Credential Rotation Guide

Create `documentation/guides/credential-rotation.md` documenting:

1. **Purpose** — Why and when credential rotation is needed
2. **Credential inventory** — Every secret the app uses and where to rotate it
3. **Rotation procedures** — Step-by-step for each credential
4. **Side effects matrix** — What breaks when each credential changes
5. **Post-rotation checklist** — Verification steps

This is the only new file in this PR. Documentation only, no application code changes.

---

## Verification Checklist

### Credential Rotation Verification

- [ ] **Spotify secret rotated**: Old client secret no longer active in Spotify Developer Dashboard
- [ ] **Neon password rotated**: Connection with OLD password fails:
  ```bash
  psql "<OLD_DATABASE_URL>" -c "SELECT 1"
  # Expected: authentication failed
  ```
- [ ] **New SECRET_KEY is different from old**: Compare against `git show cdf15d9:.env | grep SECRET_KEY`
- [ ] **`.env` is not tracked by git**: `git ls-files .env` returns empty
- [ ] **Encrypted refresh tokens cleared**:
  ```sql
  SELECT COUNT(*) FROM users WHERE encrypted_refresh_token IS NOT NULL;
  -- Expected: 0
  ```

### Application Functionality Verification

- [ ] **Health check passes**: `curl http://localhost:8000/health` returns `{"status": "healthy"}`
- [ ] **OAuth login works**: Complete Spotify OAuth flow, land on dashboard
- [ ] **Playlist fetch works**: Playlists load from Spotify
- [ ] **Shuffle works**: Run a shuffle successfully
- [ ] **Refresh token re-encrypted**: After login, `encrypted_refresh_token IS NOT NULL` for logged-in user
- [ ] **Scheduled jobs recover**: After owner re-authenticates, manual run succeeds
- [ ] **Tests pass**: `flake8 shuffify/ && pytest tests/ -v` — all 1081 tests pass

---

## What NOT To Do

1. **Do NOT commit the new credentials to git.** Double-check that `.env` is in `.gitignore`.

2. **Do NOT put real credential values in the rotation guide.** Only placeholders and procedures.

3. **Do NOT attempt to decrypt old refresh tokens with the new SECRET_KEY.** The Fernet key is deterministically derived — a different `SECRET_KEY` produces a different key. NULL out the column instead.

4. **Do NOT rotate credentials one at a time with restarts in between.** Plan all rotations, execute together, then restart once.

5. **Do NOT assume git history cleanup replaces credential rotation.** Old credentials may exist in forks, caches, CI logs, and web archives. Rotation is the only real fix.

6. **Do NOT skip the `UPDATE users SET encrypted_refresh_token = NULL` step.** Without it, the job executor will log decryption errors for every scheduled job.

7. **Do NOT reuse the compromised SECRET_KEY "temporarily."** Commit to the new key immediately.

8. **Do NOT force-push history rewrites without backing up first.** `cp -r` the entire project directory before rewriting.

9. **Do NOT forget to update the Spotify redirect URI if creating a new Spotify app.** It must exactly match `SPOTIFY_REDIRECT_URI`.

10. **Do NOT log or print the new credentials during verification.** Use indirect checks (health endpoints, successful OAuth, DB queries).
