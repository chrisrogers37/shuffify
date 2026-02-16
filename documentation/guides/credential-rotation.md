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
| `SECRET_KEY` | Flask session signing, Fernet key derivation | `.env`, deployment env vars | Generated locally |
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
- Must be at least 32 bytes (64 hex characters) for Fernet key derivation security

### Redis URL

Rotation depends on your Redis provider. Update the `REDIS_URL` environment variable with the new connection string. The app falls back to filesystem sessions if Redis is unavailable.

---

## Side Effects Matrix

| Credential Changed | What Breaks | Self-Heals? | Manual Action Required |
|--------------------|-------------|-------------|----------------------|
| `SPOTIFY_CLIENT_SECRET` | OAuth flow fails until new secret deployed | No | Update `.env` and deployment env vars, restart app |
| `SPOTIFY_CLIENT_ID` | Same as above, plus all OAuth redirects fail | No | Update `.env`, deployment env vars, and Spotify app Redirect URI |
| `SECRET_KEY` | All sessions invalidated (users logged out) | Yes (users re-login) | None |
| `SECRET_KEY` | All encrypted refresh tokens become undecryptable | Partially | Run `UPDATE users SET encrypted_refresh_token = NULL;` then users re-authenticate |
| `SECRET_KEY` | Scheduled jobs fail for all users | Yes (after user re-auth) | None — jobs auto-recover when users log in |
| `DATABASE_URL` | All DB operations fail until new URL deployed | No | Update `.env` and deployment env vars, restart app |
| `REDIS_URL` | Sessions fall back to filesystem, caching disabled | Yes (automatic fallback) | Update `.env` and deployment env vars when ready |

### SECRET_KEY Deep Dive

The `SECRET_KEY` has two roles:

1. **Flask session signing** — changing it invalidates all active sessions. Users are logged out and must re-authenticate. This is immediate and unavoidable.

2. **Fernet encryption key derivation** — `TokenService` (in `shuffify/services/token_service.py`) derives a Fernet key from `SECRET_KEY` using PBKDF2 with a fixed salt. This encrypts Spotify refresh tokens stored in `users.encrypted_refresh_token`. A new `SECRET_KEY` produces a different Fernet key, making all stored tokens undecryptable.

**Required action after SECRET_KEY rotation:**

```sql
UPDATE users SET encrypted_refresh_token = NULL;
```

Do NOT attempt to decrypt old tokens with the new key. The Fernet key is deterministically derived — a different `SECRET_KEY` will always produce a different key. NULL out the column and let users re-authenticate.

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
- [ ] If SECRET_KEY changed: `encrypted_refresh_token` column NULLed out
- [ ] If SECRET_KEY changed: confirm re-login re-encrypts token (`encrypted_refresh_token IS NOT NULL` after login)
- [ ] Tests pass: `flake8 shuffify/ && pytest tests/ -v`

---

## Rotation Timing

Rotate all credentials together, then restart once. Do not rotate one at a time with restarts in between — this causes unnecessary downtime and makes debugging harder if something fails.

**Recommended order:**
1. Generate new `SECRET_KEY` locally
2. Rotate Spotify secret on dashboard
3. Rotate Neon DB password on console
4. Update `.env` with all new values at once
5. Run `UPDATE users SET encrypted_refresh_token = NULL;` against the database
6. Update deployment environment variables
7. Restart the application
8. Verify with the post-rotation checklist
