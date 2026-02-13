# Production Database Setup Guide

How to connect a PostgreSQL database to your Shuffify deployment (e.g., shuffify.app).

---

## Overview

Shuffify uses SQLite by default for local development. For production, it supports PostgreSQL via a single environment variable: `DATABASE_URL`. The app automatically:

- Converts `postgres://` to `postgresql://` (for Neon/Railway compatibility)
- Enables SSL when connecting to managed PostgreSQL providers
- Configures connection pooling (pool size 5, recycle every 300s)
- Runs Alembic migrations on startup to create/update all tables

**Tables created** (9 total):

| Table | Purpose |
|-------|---------|
| `users` | Spotify user profiles with encrypted refresh tokens |
| `user_settings` | Per-user preferences (default algorithm, theme, snapshot settings) |
| `workshop_sessions` | Saved playlist workshop sessions |
| `upstream_sources` | Playlist raid source configurations |
| `schedules` | Scheduled shuffle/raid job definitions |
| `job_executions` | Execution history for scheduled jobs |
| `login_history` | Login/logout event tracking |
| `playlist_snapshots` | Point-in-time playlist track order captures |
| `activity_log` | Audit trail of all user actions |

---

## Option A: Neon (Recommended for Serverless)

[Neon](https://neon.tech) provides serverless PostgreSQL with a generous free tier.

### 1. Create a Neon Project

1. Sign up at [neon.tech](https://neon.tech)
2. Create a new project (any region close to your app server)
3. A default database (`neondb`) and role are created automatically

### 2. Get Your Connection String

From the Neon dashboard:

1. Go to your project **Dashboard**
2. Click **Connection Details**
3. Select **Pooled connection** (recommended for web apps)
4. Copy the connection string. It looks like:

```
postgres://username:password@ep-cool-name-123456.us-east-2.aws.neon.tech/neondb?sslmode=require
```

### 3. Set the Environment Variable

In your production environment (Railway, Render, Fly.io, or wherever shuffify.app is hosted), set:

```
DATABASE_URL=postgres://username:password@ep-cool-name-123456.us-east-2.aws.neon.tech/neondb?sslmode=require
```

The app automatically converts `postgres://` to `postgresql://` for SQLAlchemy compatibility.

### 4. Deploy

On the next deploy/restart, the app will:
1. Connect to the Neon database
2. Run all Alembic migrations to create the 9 tables
3. Begin storing user data persistently

No manual migration step is needed -- migrations run automatically on startup.

---

## Option B: Railway

[Railway](https://railway.app) provides managed PostgreSQL alongside your app.

### 1. Add PostgreSQL to Your Railway Project

1. In your Railway project, click **New** > **Database** > **PostgreSQL**
2. Railway automatically provisions the database

### 2. Link the Database

Railway automatically sets `DATABASE_URL` in your app's environment when you link the database service. If it doesn't:

1. Go to the PostgreSQL service in Railway
2. Click **Connect** > **Available Variables**
3. Copy `DATABASE_URL`
4. Add it to your app service's environment variables

The connection string looks like:

```
postgresql://postgres:password@hostname.railway.app:5432/railway
```

### 3. Deploy

Same as Neon -- the app handles migrations automatically on startup.

---

## Option C: Docker Compose (Self-Hosted)

The repo includes a `docker-compose.yml` with a PostgreSQL service pre-configured.

### 1. Configure Environment

Create a `.env` file from the example:

```bash
cp .env.example .env
```

Add the database URL pointing to the Docker PostgreSQL service:

```
DATABASE_URL=postgresql://shuffify:shuffify_dev_password@db:5432/shuffify_dev
```

### 2. Start Services

```bash
docker-compose up -d
```

This starts:
- **app**: The Shuffify Flask application on port 8000
- **db**: PostgreSQL 16 (Alpine) on port 5432

The app waits for the database health check to pass before starting.

### 3. Verify

```bash
curl http://localhost:8000/health
```

Should return:

```json
{"status": "healthy", "checks": {"database": "ok"}}
```

---

## Option D: Any PostgreSQL Instance

Any PostgreSQL 14+ instance works. Just set `DATABASE_URL`:

```
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

For remote instances requiring SSL, append `?sslmode=require`:

```
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
```

---

## Required Environment Variables

For a fully functional production deployment, you need:

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SPOTIFY_CLIENT_ID` | Yes | Spotify API credentials |
| `SPOTIFY_CLIENT_SECRET` | Yes | Spotify API credentials |
| `SPOTIFY_REDIRECT_URI` | Yes | Must match Spotify dashboard (e.g., `https://shuffify.app/callback`) |
| `SECRET_KEY` | Yes | Flask session signing + token encryption key |
| `FLASK_ENV` | Yes | Set to `production` |
| `REDIS_URL` | Recommended | Session storage and API caching |

### Generating a Secret Key

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Important:** The `SECRET_KEY` is used to derive the Fernet encryption key for stored Spotify refresh tokens. Changing it will invalidate all existing encrypted tokens, requiring users to re-authenticate.

---

## Verifying the Database Connection

### Health Check Endpoint

```bash
curl https://shuffify.app/health
```

**Healthy response:**
```json
{"status": "healthy", "checks": {"database": "ok"}}
```

**Degraded response** (database unreachable):
```json
{"status": "degraded", "checks": {"database": "unavailable"}}
```

The app still functions in degraded mode (session-based features work), but persistent features (schedules, snapshots, activity log, settings) are unavailable.

### Checking Tables Were Created

Connect to your database and verify:

```sql
SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
```

Expected output:

```
 activity_log
 alembic_version
 job_executions
 login_history
 playlist_snapshots
 schedules
 upstream_sources
 user_settings
 users
 workshop_sessions
```

---

## Migrations

### Automatic (Default)

Migrations run automatically when the app starts. The startup logic:

1. Checks if a `migrations/` directory exists
2. If yes, runs `flask db upgrade` (Alembic) to apply any pending migrations
3. If no, falls back to `db.create_all()` (creates tables without migration history)

### Manual (If Needed)

If you need to run migrations manually (e.g., debugging):

```bash
# Set required env vars
export FLASK_APP=run.py
export FLASK_ENV=production
export DATABASE_URL=postgresql://...

# Check current migration status
flask db current

# Apply pending migrations
flask db upgrade

# View migration history
flask db history
```

---

## Troubleshooting

### "Connection refused" on startup

- Verify `DATABASE_URL` is set correctly
- Ensure the database server is reachable from your app server
- For Neon: check that your IP is not blocked (Neon allows all IPs by default)
- For Docker: ensure the `db` service is healthy before the app starts

### "SSL required" errors

Managed providers (Neon, Railway) require SSL. Ensure your connection string includes `?sslmode=require`. The app automatically enables SSL when it detects a `postgres` prefix in `DATABASE_URL`.

### "Relation does not exist" errors

Migrations may not have run. Check:

1. The `migrations/` directory is included in your deployment
2. The `alembic_version` table exists (if not, migrations never ran)
3. Try running `flask db upgrade` manually

### Users must re-authenticate after deployment

This happens when `SECRET_KEY` changes between deployments. The encrypted refresh tokens become unreadable. Set a stable `SECRET_KEY` that persists across deployments.

### Health check shows "degraded"

The database is unreachable. The app will still serve the login page and basic shuffle features (session-based), but all persistent features are disabled. Check `DATABASE_URL` and database server status.
