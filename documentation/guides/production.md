# Production

Production has run this repository since 2026-10-17. It has one environment.
The Railway and Neon staging copies were removed on 2026-10-18, and the
previous platform is gone.

## What runs where

| Piece | Where | Details |
| --- | --- | --- |
| Domain | DigitalOcean DNS, zone `shuffify.app` | Apex `A` records `216.150.1.1` and `216.150.16.1` (TTL 300) point at Vercel. The zone has no MX, TXT or `www` records. |
| Frontend | Vercel project `shuffify` (team `chrisrogers37`), domains `shuffify.app` and `shuffify.vercel.app` | Root `web/`, build `npm run build:vercel`, production branch `main`. Serves the React app and forwards `/api`, `/login`, `/callback`, `/logout` and `/health` to the URL in `RAILWAY_API_URL`. |
| API | Railway project `shuffify`, environment `production`, service `api` | Public at `https://api-production-391dd.up.railway.app`. Each deployment runs `scripts/pre-deploy.sh` (Alembic upgrade) first; a failure blocks it. |
| Worker | Same environment, service `worker` | Private. One replica owns queued Spotify writes and the scheduler. |
| Redis | Same environment, service `redis` | Sessions, rate-limit counters, Spotify caching and worker coordination. |
| Database | Neon project `shuffify-db` (`proud-dew-57305636`), branch `production` (`br-snowy-bird-ai3vjw7w`), database `neondb` | The API, its pre-deploy migration and the worker use the branch's direct (unpooled) endpoint. Schema head `e1f2a3b4c5d6`. |

Railway builds the API and worker from `main` of `chrisrogers37/shuffify`. Both services receive `REDIS_URL` from the Redis service. The API, its pre-deploy migration and the worker each verify that Redis answers a ping and accepts a rate-limit counter write at startup. Failure blocks startup until writable Redis is available.

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

## Interactive Spotify reads

Each Spotify account can start **200 interactive resource reads per fixed minute**, shared across routes, sessions, IP addresses and API instances through Redis. One unit covers one upstream origin and path in one signed-in HTTP request. For example, playlist metadata and that playlist’s items cost two units together. A restore review supports up to 100 resources, so even a review that needs metadata and items for every target fits in an empty window.

The first outgoing GET to a path spends its unit immediately before dispatch, even if Spotify returns an error. Pagination, retries, changes to query parameters and repeat observations of that path in the same view use the admitted unit. A different origin or path, including a redirect or the public embed fallback, needs another unit. The next user request starts its own set of resources. At capacity the app refuses the first GET to a new resource before dispatch and returns the view’s JSON 429 with a positive `Retry-After` in seconds. Flask’s automatic HEAD runs the GET view and returns headers only. Cache lookups use the account verified at sign-in. Cache hits, local validation and database reads spend zero units.

The two playlist-list views, `/api/library` and `/api/user-playlists`, also share **10 forced refresh requests per account per minute**. Their `refresh=1` GET or HEAD spends one refresh unit before client construction. Fetching the playlist list then spends one data unit including all its pages. A rejection at the refresh gate spends zero data units. The refresh unit remains spent if the data budget refuses the list. The setup playlist picker’s `refresh=1` uses the data budget alone.

All paths in the table are relative to `/api`. A fresh read can still end locally if validation or ownership checks reject it first.

| Read path | Data source |
| --- | --- |
| GET `/library`, `/user-playlists`, `/routines`, `/playlist/<id>/setup/playlists` | The account’s cached editable playlist list, or paginated `/me/playlists` on a miss or supported refresh. |
| POST `/workshop/search`, `/workshop/load-external-playlist` | Track or playlist searches use the search cache. Loading by URL reads account-scoped playlist metadata and tracks on misses. Restricted playlists can need a metadata fallback from Spotify’s public embed page. |
| GET `/workshop/<id>` | Always reads a fresh edit-permission summary, then uses the metadata, track and navigation-list caches. |
| GET `/workshop/<id>/current-state`, `/workshop/<id>/source-context`, `/playlist/<id>/pair/archive-tracks` | Fresh metadata and item pages for a complete order or linked sources. Archive tracks reads the count and the final page. |
| GET `/playlist/<id>/setup/context`, `/playlist/<id>/routines/context` and POST `/playlist/<id>/setup/impact`, `/playlist/<id>/routines/review` | Fresh target and applicable dependency authorization. Setup impact can also read cached source metadata or the public embed fallback. |
| POST `/playlist/<id>/live-context`, `/playlist/<id>/inspect-live` and GET `/schedules/<id>/run-context`, `/snapshots/<id>/restore-context`, `/operations/<id>/restore-context` | Fresh observations for the user’s review, including each resource and repeat observation used to verify order or metadata. |
| POST `/playlist/<id>/pending-raids/finalize`, `/playlist/<id>/pair/archive`, `/playlist/<id>/pair/finalize-restore` | The `inspect_only` branch observes current state. The enqueue branch captures local intent. |
| Session, activity, settings, operation status, schedule history and latest results, pair and raid status, pending raids, saved snapshots, workshop sessions and locks | Session or database data. Workshop preview-shuffle uses the supplied tracks. |

Interactive checks embedded in setup saves, routine activation or state changes and raid-add-url spend units when they read Spotify. OAuth sign-in and token maintenance are separate from data reads. A route that builds a client can renew an expired token. Worker execution, retries, reconciliation and recovery run outside the authenticated view’s budget.

## Backups

- **Pre-cutover snapshot:** Neon branch `backup-final-before-v2-cutover-2026-10-17` (`br-wispy-brook-aijw6fkz`), with no compute. It was taken from `production` on 2026-10-17, after the previous application stopped and before the cutover changed any data. It holds:
  - the pre-cutover schema (`f7a8b9c0d1e2`)
  - the original users and routines, with routines enabled
  - refresh tokens that the current encryption key cannot read
- **Point-in-time history:** Neon's history of `production`, within the project's retention window.
- **Retired Railway PostgreSQL:** a final dump, taken on 2026-10-19 before the database was deleted and
  kept by the owner outside the repository. It restores cleanly into PostgreSQL 18 with the source's
  17 tables and 5 rows.

## Changing the cross-account queue admission rule

When introducing per-playlist write proof (#129, step 1), stop and drain all
older worker replicas before either the upgraded API accepts manual requests or
an upgraded worker admits scheduled fires. Older workers can write during the
gap between another operation's interrupted write and its recovery. Once the
old workers have exited, start the upgraded worker and release the API.

To roll back across this change, stop manual submissions and scheduled fires
first. Keep the upgraded worker draining the queue, with scheduling disabled,
until all active operations have settled, including deferred and recovering
work. Then switch the API and worker back and resume admission.

## Rolling back

### Code

- **Frontend:** in Vercel, promote the previous production deployment (Deployments → Instant Rollback, or `vercel rollback`).
- **API and worker:** in Railway, redeploy each service's previous successful deployment, or revert the change on `main`.
  - The API refuses to start unless the schema is exactly at its own Alembic head.
  - A rollback across a migration therefore also needs the matching schema change.
  - Releases before #62 use head `c9d0e1f2a3b4`. From #62 they use `d0e1f2a3b4c5`, which drops v1's
    `apscheduler_jobs` table. From #143 on they use `e1f2a3b4c5d6`, which clears the session ids stored in login
    history.
  - To roll back to a release before #143, first step the schema back. From a checkout of a later release, with the
    repository's virtualenv active, run it with the API's variables:
    `railway run --service api --environment production -- env FLASK_APP=run.py SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 flask db downgrade d0e1f2a3b4c5`.
    For a release before #62, downgrade to `c9d0e1f2a3b4` instead. Each of these downgrades changes only the recorded
    revision.

### Recent data changes

Stop the worker first, so no operation runs across the restore: in Railway, open the worker's active deployment and choose Remove. Then restore `production` to an earlier moment, keeping the current state as another branch:

```sh
npx neonctl branches restore production '^self@2026-10-20T12:00:00Z' \
  --project-id proud-dew-57305636 --preserve-under-name production-before-restore
```

Connections drop briefly and the API reconnects. Then redeploy the API, then the worker. If the restore point
predates a migration, the API's pre-deploy step upgrades the schema again. The worker has no pre-deploy step
and refuses to start on an older schema.

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
4. Redeploy the API. Its pre-deploy step upgrades the schema from `f7a8b9c0d1e2` to `e1f2a3b4c5d6`.
5. Redeploy the worker.
6. Users sign in again with Spotify and re-enable their routines.

### Previous platform

There is no rollback to it:

- the DigitalOcean app was destroyed on 2026-10-18
- its repository is archived as `chrisrogers37/shuffify-v1`

## Retired Railway PostgreSQL

The Railway PostgreSQL these services used before the move to Neon was deleted on
2026-10-19, after the final dump listed under [Backups](#backups). No service used it.
`.railway/railway.ts` no longer declares it, so no plan recreates it.
