# Credential Rotation Guide

This guide documents how to rotate every secret used by the Shuffify application, when rotation is needed, and what side effects to expect.

---

## When to Rotate

- A credential was committed to version control or exposed in logs
- A team member with access leaves the project
- A dependency or service reports a breach
- Periodic rotation as part of security hygiene (recommended: annually)

---

## Credential Inventory

| Credential | Used For | Where It Lives | Rotation Source |
|------------|----------|----------------|-----------------|
| `SPOTIFY_CLIENT_ID` | OAuth app identity | `.env`, deployment env vars | Spotify Developer Dashboard |
| `SPOTIFY_CLIENT_SECRET` | OAuth authentication | `.env`, deployment env vars | Spotify Developer Dashboard |
| `SECRET_KEY` | Flask session signing (and legacy token-key derivation when `TOKEN_ENCRYPTION_KEY` is unset) | `.env`, deployment env vars | Generated locally |
| `TOKEN_ENCRYPTION_KEY` | Fernet encryption of stored refresh tokens | `.env`, deployment env vars | Generated locally |
| `TOKEN_ENCRYPTION_KEY_FALLBACKS` | Retired token keys, decrypt-only, during rotation windows | `.env`, deployment env vars | Previous `TOKEN_ENCRYPTION_KEY` values |
| `DATABASE_URL` | PostgreSQL connection (includes password) | `.env`, deployment env vars | Neon Console |
| `REDIS_URL` | Session storage, caching | `.env`, deployment env vars | Redis provider dashboard |

---

## Rotation Procedures

### Spotify API Credentials

1. Go to **https://developer.spotify.com/dashboard**
2. Select the **Shuffify** application
3. Navigate to **Settings**
4. Click **RESET CLIENT SECRET**
5. Copy the new secret immediately and store in a password manager

**Notes:**
- The old secret is invalidated immediately
- The Client ID cannot be rotated independently; to get a fresh pair, create a new Spotify app (and update the Redirect URI to match `SPOTIFY_REDIRECT_URI`)
- If creating a new app, delete the old one after verifying the new credentials work

### Neon Database Password

1. Go to **https://console.neon.tech**
2. Select the Shuffify project
3. Navigate to **Roles** (under branch settings)
4. Click **Reset Password** next to the database role
5. Copy the new password immediately (Neon only shows it once)
6. Construct the new `DATABASE_URL`:
   ```
   postgres://<user>:<NEW_PASSWORD>@<endpoint>.neon.tech/<dbname>?sslmode=require
   ```

**Notes:**
- The old password is invalidated immediately
- Coordinate with other credential updates to minimize downtime

### Flask SECRET_KEY

Generate a new key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Notes:**
- Must be at least 32 bytes (64 hex characters)
- With `TOKEN_ENCRYPTION_KEY` set and all tokens re-encrypted (see below), rotating `SECRET_KEY` only invalidates sessions — stored refresh tokens survive

### Token Encryption Key

Generate a new key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**First-time adoption (migrating off SECRET_KEY derivation):**

1. Generate a key and set `TOKEN_ENCRYPTION_KEY` in `.env` / deployment env vars
2. Restart the application — existing tokens stay readable through the legacy SECRET_KEY-derived fallback key
3. Re-encrypt stored tokens onto the new key:
   ```bash
   flask rotate-token-encryption --dry-run   # preview counts
   flask rotate-token-encryption             # re-encrypt
   ```
4. Verify: the command reports `failed: 0` and exits 0
5. From this point, `SECRET_KEY` rotation no longer touches stored tokens

**Rotating an existing TOKEN_ENCRYPTION_KEY:**

1. Generate a new key
2. Move the current key into `TOKEN_ENCRYPTION_KEY_FALLBACKS` (comma-separated if several) and set the new key as `TOKEN_ENCRYPTION_KEY`
3. Restart the application — old tokens decrypt via the fallback, new writes use the new key
4. Run `flask rotate-token-encryption` to move all stored tokens onto the new key
5. Once it reports `failed: 0`, remove the retired key from `TOKEN_ENCRYPTION_KEY_FALLBACKS` and restart

**Ordering rule:** always finish re-encryption (`failed: 0`) *before* removing a fallback key or rotating `SECRET_KEY`. A token whose key disappears from the configuration is unrecoverable — the affected user must re-login.

### Redis URL

Rotation depends on your Redis provider. Update the `REDIS_URL` environment variable with the new connection string. The app falls back to filesystem sessions if Redis is unavailable.

---

## Side Effects Matrix

| Credential Changed | What Breaks | Self-Heals? | Manual Action Required |
|--------------------|-------------|-------------|----------------------|
| `SPOTIFY_CLIENT_SECRET` | OAuth flow fails until new secret deployed | No | Update `.env` and deployment env vars, restart app |
| `SPOTIFY_CLIENT_ID` | Same as above, plus all OAuth redirects fail | No | Update `.env`, deployment env vars, and Spotify app Redirect URI |
| `SECRET_KEY` | All sessions invalidated (users logged out) | Yes (users re-login) | None |
| `SECRET_KEY` (with `TOKEN_ENCRYPTION_KEY` set and tokens re-encrypted) | Nothing beyond sessions — stored refresh tokens and scheduled jobs unaffected | — | None |
| `SECRET_KEY` (legacy: no `TOKEN_ENCRYPTION_KEY`) | All encrypted refresh tokens become undecryptable; scheduled jobs fail until users re-login | Partially | Adopt `TOKEN_ENCRYPTION_KEY` *before* rotating (see above). Last resort: `UPDATE users SET encrypted_refresh_token = NULL;` then users re-authenticate |
| `TOKEN_ENCRYPTION_KEY` (old key kept in `TOKEN_ENCRYPTION_KEY_FALLBACKS`) | Nothing — old tokens decrypt via fallback until re-encrypted | — | Run `flask rotate-token-encryption`, then drop the fallback |
| `TOKEN_ENCRYPTION_KEY` (old key removed without re-encryption) | Tokens still on the old key are unrecoverable | Yes (affected users re-login) | Don't do this — follow the rotation procedure above |
| `DATABASE_URL` | All DB operations fail until new URL deployed | No | Update `.env` and deployment env vars, restart app |
| `REDIS_URL` | Sessions fall back to filesystem, caching disabled | Yes (automatic fallback) | Update `.env` and deployment env vars when ready |

### Token Encryption Deep Dive

`TokenService` (in `shuffify/services/token_service.py`) encrypts Spotify refresh tokens stored in `users.encrypted_refresh_token` with a Fernet key chain:

1. **Primary** — `TOKEN_ENCRYPTION_KEY` when set; otherwise a key derived from `SECRET_KEY` via PBKDF2 (legacy scheme). All writes encrypt with the primary.
2. **Fallbacks** — every key in `TOKEN_ENCRYPTION_KEY_FALLBACKS`, plus the `SECRET_KEY`-derived key, accepted for decryption only.

Because the `SECRET_KEY`-derived key stays in the decrypt chain, setting `TOKEN_ENCRYPTION_KEY` on an existing deployment loses nothing: old tokens decrypt through the legacy key until `flask rotate-token-encryption` (or the user's next login / scheduled-job token refresh, which re-encrypt opportunistically) moves them onto the primary.

`SECRET_KEY` itself keeps one unavoidable role — **Flask session signing**. Rotating it always logs everyone out. Whether it also destroys stored refresh tokens depends on migration state:

- **Migrated** (`TOKEN_ENCRYPTION_KEY` set, `flask rotate-token-encryption` reported `failed: 0`): tokens are unaffected by `SECRET_KEY` rotation. Scheduled jobs keep running.
- **Not migrated**: rotating `SECRET_KEY` makes every stored token undecryptable and stops all scheduled jobs until users re-login. Adopt `TOKEN_ENCRYPTION_KEY` first. If the old `SECRET_KEY` is already gone, the tokens are unrecoverable — `UPDATE users SET encrypted_refresh_token = NULL;` and let users re-authenticate.

---

## Post-Rotation Checklist

After rotating credentials and updating all environments:

- [ ] `.env` updated with new values
- [ ] Deployment environment variables updated
- [ ] Application restarted in all environments
- [ ] Health check passes: `curl http://<host>:8000/health` returns `{"status": "healthy"}`
- [ ] OAuth login flow completes successfully
- [ ] Playlists load from Spotify
- [ ] Old credentials no longer work (verify by attempting connection with old values)
- [ ] If TOKEN_ENCRYPTION_KEY changed: `flask rotate-token-encryption` run, reports `failed: 0`, retired key removed from fallbacks
- [ ] If SECRET_KEY changed on a migrated deployment: `flask rotate-token-encryption --dry-run` reports all tokens `already on primary key`
- [ ] If SECRET_KEY changed on a legacy (non-migrated) deployment: `encrypted_refresh_token` column NULLed out, and re-login re-encrypts (`encrypted_refresh_token IS NOT NULL` after login)
- [ ] Tests pass: `flake8 shuffify/ && pytest tests/ -v`

---

## Rotation Timing

Rotate all credentials together, then restart once. Do not rotate one at a time with restarts in between — this causes unnecessary downtime and makes debugging harder if something fails.

**Recommended order:**
1. If not yet migrated: adopt `TOKEN_ENCRYPTION_KEY` and run `flask rotate-token-encryption` to `failed: 0` *before* anything else
2. Generate new `SECRET_KEY` (and new `TOKEN_ENCRYPTION_KEY`, if rotating it — keep the old one in `TOKEN_ENCRYPTION_KEY_FALLBACKS`) locally
3. Rotate Spotify secret on dashboard
4. Rotate Neon DB password on console
5. Update `.env` with all new values at once
6. Update deployment environment variables
7. Restart the application
8. If `TOKEN_ENCRYPTION_KEY` rotated: run `flask rotate-token-encryption`, then drop the fallback key and restart
9. Verify with the post-rotation checklist
