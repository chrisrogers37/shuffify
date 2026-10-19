# Production

Production has run this repository since 2026-10-17. It has one environment.
The Railway and Neon staging copies were removed on 2026-10-18, and the
previous platform is gone.

## What runs where

| Piece | Where | Details |
| --- | --- | --- |
| Domain | DigitalOcean DNS, zone `shuffify.app` | Apex `A` records `216.150.1.1` and `216.150.16.1` (TTL 300) point at Vercel. The zone has no MX, TXT or `www` records. |
| Frontend | Vercel project `shuffify-v2` (team `chrisrogers37`) | Root `web/`, build `npm run build:vercel`, production branch `main`. Serves the React app and forwards `/api`, `/login`, `/callback`, `/logout` and `/health` to the URL in `RAILWAY_API_URL`. |
| API | Railway project `shuffify-v2`, environment `production`, service `api` | Public at `https://api-production-391dd.up.railway.app`. Each deployment runs `scripts/pre-deploy.sh` (Alembic upgrade) first; a failure blocks it. |
| Worker | Same environment, service `worker` | Private. One replica owns queued Spotify writes and the scheduler. |
| Redis | Same environment, service `redis` | Sessions and worker coordination. |
| Database | Neon project `shuffify-db` (`proud-dew-57305636`), branch `production` (`br-snowy-bird-ai3vjw7w`), database `neondb` | The API, its pre-deploy migration and the worker use the branch's direct (unpooled) endpoint. Schema head `c9d0e1f2a3b4`. |
| Retained database | Same Railway environment, service `postgres` | The database these services used before the move to Neon, about 860 MB. No service references it. See [Retiring the Railway PostgreSQL](#retiring-the-railway-postgresql). |

Railway builds the API and worker from `main` of `chrisrogers37/shuffify`.

The `production` environment's shared variables hold every value that
`.railway/railway.ts` reads:

- `DATABASE_URL` (the Neon URL above)
- `PUBLIC_ORIGIN` (`https://shuffify.app`)
- `SPOTIFY_REDIRECT_URI` (`https://shuffify.app/callback`)
- `SECRET_KEY` and `TOKEN_ENCRYPTION_KEY`
- `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`
- `WORKER_CONTROL_TOKEN`

The services reference these shared variables, so `railway config plan` proposes no
variable changes. Change a value in the shared variable, not on a service.

Vercel preview deployments also forward to the production API. They can render
pages. Sign-in still returns to `https://shuffify.app`, and the API accepts
unsafe requests only from that origin.

## Backups

- **Pre-cutover snapshot:** Neon branch `backup-final-before-v2-cutover-2026-10-17` (`br-wispy-brook-aijw6fkz`), with no compute. It was taken from `production` on 2026-10-17, after the previous application stopped and before the cutover changed any data. It holds:
  - the pre-cutover schema (`f7a8b9c0d1e2`)
  - the original users and routines, with routines enabled
  - refresh tokens that the current encryption key cannot read
- **Point-in-time history:** Neon's history of `production`, within the project's retention window.
- **Retained Railway PostgreSQL:** kept until it is retired (below).

## Rolling back

### Code

- **Frontend:** in Vercel, promote the previous production deployment (Deployments → Instant Rollback, or `vercel rollback`).
- **API and worker:** in Railway, redeploy each service's previous successful deployment, or revert the change on `main`.
  - The API refuses to start unless the schema is exactly at its own Alembic head.
  - A rollback across a migration therefore also needs the matching schema change.
  - Every release since the cutover uses head `c9d0e1f2a3b4`.

### Recent data changes

Stop the worker first, so no operation runs across the restore: in Railway, open the worker's active deployment and choose Remove. Then restore `production` to an earlier moment, keeping the current state as another branch:

```sh
npx neonctl branches restore production '^self@2026-10-20T12:00:00Z' \
  --project-id proud-dew-57305636 --preserve-under-name production-before-restore
```

Connections drop briefly and the API reconnects. Redeploy the worker afterwards.

### Back to the pre-cutover snapshot

This discards everything since 2026-10-17.

1. Stop the worker as described above.
2. Restore `production` from the backup branch:
   ```sh
   npx neonctl branches restore production br-wispy-brook-aijw6fkz \
     --project-id proud-dew-57305636 --preserve-under-name production-before-restore
   ```
3. Clear the unreadable tokens and pause routines, as the cutover did. Use the branch's direct connection string:
   ```sh
   psql "$(npx neonctl connection-string production --project-id proud-dew-57305636 --database-name neondb)" \
     -c "BEGIN; UPDATE users SET encrypted_refresh_token = NULL; UPDATE schedules SET is_enabled = false; COMMIT;"
   ```
4. Redeploy the API. Its pre-deploy step upgrades the schema from `f7a8b9c0d1e2` to `c9d0e1f2a3b4`.
5. Redeploy the worker.
6. Users sign in again with Spotify and re-enable their routines.

### Previous platform

There is no rollback to it:

- the DigitalOcean app was destroyed on 2026-10-18
- its repository is archived as `chrisrogers37/shuffify-v1`

## Retiring the Railway PostgreSQL

`.railway/railway.ts` keeps declaring the `postgres` resource, wired to no
service, so `railway config plan` never proposes deleting it by omission.
Retiring it takes the `SHUFFIFY_RAILWAY_RETIRE_POSTGRES=1` opt-in.

1. **Take a final dump.** The database still holds data. It has no public address, so add one for the dump: Railway → `postgres` → Settings → Networking → TCP Proxy. The server runs PostgreSQL 18, so use an 18 client:
   ```sh
   brew install postgresql@18
   railway link --project shuffify-v2 --environment production
   PG18="$(brew --prefix postgresql@18)/bin"
   DATABASE_PUBLIC_URL="$(railway variable list --service postgres --environment production --kv \
     | sed -n 's/^DATABASE_PUBLIC_URL=//p')"
   "$PG18/pg_dump" --format=custom --no-owner \
     --file "railway-postgres-final-$(date +%F).dump" "$DATABASE_PUBLIC_URL"
   "$PG18/pg_restore" --list railway-postgres-final-*.dump | head
   ```
   Keep the dump somewhere durable, then remove the TCP proxy.
2. **Preview the retirement.** From the repository root:
   ```sh
   SHUFFIFY_RAILWAY_RETIRE_POSTGRES=1 SHUFFIFY_RAILWAY_GITHUB_REPO=chrisrogers37/shuffify \
     SHUFFIFY_RAILWAY_GITHUB_BRANCH=main railway config plan
   ```
   The plan must show `Delete database postgres`. As of 2026-10-18 it also sets an explicit restart policy on `api` and `worker` (`ON_FAILURE`, 10 retries); nothing else should appear.
3. **Apply.** Run the same command with `railway config apply`.
4. **Remove the declaration.** In a pull request, remove `retainedDatabases` from `.railway/railway.ts` so later plans do not recreate the database.
