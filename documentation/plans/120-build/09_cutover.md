---
title: "[plan] Cutover: wipe, merge, owner acceptance — ops"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, ops]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: S, but ordered and
irreversible at one named step.

The owner is the only user, so the cutover is one sitting. `feat/workspaces`
merges into main and production's database is wiped and rebuilt. Then the
owner deletes their old Spotify app, signs up as the first user, creates a new
Spotify app from the in-product guide alone (timed: under 15 minutes from
step 1 to their own playlists, with notes on where the copy differs from
Spotify's live dashboard) and rebuilds their routines. That is the acceptance
test. The Google consent screen is published, so this is a real opening, with
#139's per-address limit as the only sign-up safeguard until #123 ships the
rest.

The domain moved in phase 01b, and the pre-deploy script learned to build a
new database in phase 06, so neither is done on this day.

## Evidence

**What the deploy runs**
- The Railway api pre-deploy runs `scripts/pre-deploy.sh`. From phase 06 it
  runs `flask create-schema` and then `flask db upgrade`, both with
  `SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1`. `create-schema` builds
  and stamps head only when the database has no revision and no tables, leaves
  a database that has a revision to the upgrade, and refuses anything else.
- `migrations/env.py:54-66` refuses to upgrade a database with no revision,
  so `create-schema` has to run first on an empty database.
- The app's startup schema check refuses a database that isn't at head unless
  `SHUFFIFY_MIGRATION_STEP` is set (`shuffify/__init__.py:214, 284`). So
  `railway run … flask create-schema` against production, from a laptop,
  fails the startup check unless that variable is set, and it builds
  whatever schema the local checkout's models describe, not the deployed
  code's.
- The new migrations are never meant to run over production's old data:
  production is built by `create-schema` from an empty database, so the wipe
  has to land before the new api's pre-deploy starts.

**How Railway deploys**
- Both services build from `main` (`.railway/railway.ts:38`), so the merge
  redeploys the worker too, whatever was done to it beforehand.
- The repo trigger deploys every push to `main` without waiting for check
  suites (`checkSuites=false`, as the owner recorded; confirm in the service
  settings). The merge starts the build at once, and the api's pre-deploy runs
  after the build finishes.
- The worker has no pre-deploy step and refuses to start on a schema that
  isn't at head: `create_app()` raises `SchemaOutOfDateError`
  (`shuffify/__init__.py:223-300`, called from `shuffify/worker.py:628`;
  `documentation/guides/production.md`, Recent data changes). Its restart
  policy is `ON_FAILURE` with 10 retries (`.railway/railway.ts`). A worker that
  starts against the emptied database before the api has built it therefore
  builds the app, refuses and exits without touching any data, and uses up its
  retries, leaving the deployment FAILED. That does no harm, and step 11
  redeploys the worker by hand in every case, so nothing needs to hold the new
  worker back.

**Rolling back the database** is documented in
`documentation/guides/production.md`: "Recent data changes" and "Back to the
pre-cutover snapshot" give the `npx neonctl branches restore production …
--preserve-under-name production-before-restore` procedure with the project id,
and remove the worker's active deployment first. The 2026-10-17 cutover's
backup branch is the precedent for taking a snapshot branch first.

**Hosting**
- DNS is on DigitalOcean. By this phase raccoon.fm serves the site and
  shuffify.app redirects to it (01b). Vercel serves the web app, Railway the
  API, and production uses Neon branch `production`.
- Sessions and worker coordination live in Redis, so wiped users would leave
  sessions that point at rows that no longer exist.

## Implementation Plan

### Dependencies
01, 01b, 03 to 08. The staging gate below. Phase 09 is the only phase that
depends on 01b.

### Blocks
#121, #123.

### Steps

**Gate: staging**
1. Phases 06 to 08 have passed their staging checks against real Spotify and
   Google, and staging has rehearsed the mechanics of the day, so none is first
   tried on production:
   - the pre-deploy build of an emptied database (phase 06 step 7), through the
     pooled host production uses;
   - walking the setup pages against real Spotify with the existing app, so
     the copy is checked once before the timed run. Optionally a friend with a
     Premium Spotify account runs the guide from scratch on staging (that uses
     up their one development-mode client ID).

**Before cutover day**
2. **The owner creates production's Google OAuth client and publishes the
   consent screen:**
   - authorized origin `https://raccoon.fm`;
   - redirect `https://raccoon.fm/auth/google/callback`;
   - scopes `openid email profile`, which are basic scopes with no
     sensitive-scope review;
   - set the publishing status to In production. Google may ask for brand
     verification (an app name, logo and links to the legal pages), which can
     take days, so start it well before the day. Until it clears, only test
     users can finish sign-in without a warning; don't cut over before it works
     for an account that isn't a test user.
3. **Set the new Railway shared variables:** `GOOGLE_CLIENT_ID`,
   `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI`. Setting a shared variable
   redeploys the services, so do it now, not on the day. Keep the shared
   `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and `SPOTIFY_REDIRECT_URI`
   until step 14: rolling back to the old code needs them.
4. **The owner** clears their raid inbox and writes down their schedule
   settings.
5. **Merge main into `feat/workspaces` one last time,** confirm which of #126
   and #128 to #131 have landed and are in, run
   `python scripts/check-migration-chain.py`, and wait for CI to be fully
   green.
6. **Stage the commands** in a scratch file: the Neon identity check, the
   schema reset, the Redis flush, the old worker's removal and the new worker's
   redeploy, and the rollback. Verify the Neon branch's identity from Neon's
   metadata (`.railway/README.md`), not from a remembered id.
   - **Read back the variables the new api needs before it will start,** next
     to the Neon identity check. The new api refuses to start without
     `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI`
     (phase 05 step 2), and after the wipe the database is already empty, so a
     missing or mistyped value fails the deploy at the worst moment. Staging
     only rehearses staging's own variables. Read these four back from
     production's shared variables: `GOOGLE_CLIENT_ID` is production's Google
     client from step 2, `GOOGLE_REDIRECT_URI` is exactly
     `https://raccoon.fm/auth/google/callback`, `PUBLIC_ORIGIN` is exactly
     `https://raccoon.fm`, and `GOOGLE_CLIENT_SECRET` is set (compare it with
     the secret Google issued, without printing it into a shared log).

**Cutover, in one sitting.** The old site keeps serving until the wipe, and the
wipe is followed immediately by the merge, so the downtime is one build.
7. **Stop the old worker** so the old code doesn't run against the wiped
   database: in Railway, open the worker's active deployment and choose Remove
   (`production.md`), then confirm no operation is running. The merge builds a
   new worker from `main` anyway; if it starts before the api has built the
   schema it refuses on its schema check and exits (Evidence), and step 11
   redeploys it by hand either way.
8. **Take the snapshot.** With the old worker stopped, create a Neon backup branch of
   `production` (the 2026-10-17 precedent; `npx neonctl branches create --name
   backup-before-workspaces-<date> --parent production --project-id
   proud-dew-57305636`, flags confirmed with `neonctl branches create --help`
   because the repo docs don't show this command), and note its id.
9. **Wipe and merge, back to back.**
   1. Drop and recreate the public schema on `production`, through the branch's
      direct connection string (`neonctl connection-string`, as `production.md`
      does), and flush the production Redis.
   2. Merge `feat/workspaces` into main straight away. The old api serves
      errors until the new one is up: that is the downtime.
   3. Railway builds the api; its pre-deploy finds an empty database, builds
      the schema from the merged models, stamps head and logs the revision;
      `flask db upgrade` then has nothing to do. If a build is dropped or fails
      with a builder error, re-trigger it for the merge commit.
10. **Check the deploy:** the api deployment is SUCCESS on the merge SHA, the
    pre-deploy log shows the stamped revision and the Neon branch it ran on,
    `https://raccoon.fm/health` returns 200, and a Google sign-in as the owner
    reaches the setup page.
11. **Redeploy the worker** on the merge SHA by hand now that the api is at
    head (the merge's own worker build may have run out its restart retries
    against the unbuilt database), and confirm its deployment is SUCCESS and
    its health check passes.

**Acceptance, by the owner, straight after**
12. **The point of no return, and the timed setup (the threshold is 15
    minutes).** With a timer, on raccoon.fm:
    1. Delete the old Spotify app in Spotify's dashboard. From here the old
       site can't be rolled back to, because its tokens belong to a deleted
       app. (Spotify allows one development-mode app per person, so this is
       the only way to make the new one.)
    2. Sign in with Google, then follow the in-product guide alone: create a
       new Spotify app (step 1), add the redirect URL
       `https://raccoon.fm/spotify/callback` and the account (step 2), paste
       the client ID (step 3) and connect (step 4).
    3. Record the time from step 1 to the person's own playlists. **The
       threshold is 15 minutes.** If it is longer, the copy follow-up PR also
       fixes the slowest step. Write down every place the copy or screenshots
       differ from Spotify's live dashboard, and fix them in that PR.
13. Rebuild schedules and routines through the product. Watch the first
    scheduled run of each routine; snapshots (restore points) start fresh.
    **Restore from one:** once the first scheduled run has produced a snapshot,
    open that run in the activity view, restore the playlist from its snapshot
    through the workspace's app, and confirm the playlist is back as it was
    before the run. Then break the app on purpose (remove Raccoon's access in
    the Spotify account's apps page), confirm the "uh oh" page appears (the
    access-removed case) and schedules pause, connect again, and confirm they
    resume at their next slot.

**Clean-up**
14. Delete the `staging` Railway environment, the staging Neon branch, the
    Vercel branch variable, the `staging.raccoon.fm` domain with its DNS record
    and the staging Google client. Remove the shared `SPOTIFY_CLIENT_ID`,
    `SPOTIFY_CLIENT_SECRET` and `SPOTIFY_REDIRECT_URI` from Railway (the
    redirect URL is now `PUBLIC_ORIGIN` plus `/spotify/callback`, F13). Update
    `documentation/guides/production.md`, the README and `.railway/README.md`
    with the new variables and this runbook's outcome (phase 06 step 7 already
    rewrote their pre-deploy wording); their "no staging environment" lines are
    correct again. **Update the README's sign-in wording** too: the
    "Spotify sign-in" feature bullet (README.md:34) and Getting Started step 1
    (README.md:38) still describe signing in with Spotify, and sign-up is open
    from the cutover. They become: sign in with Google, create your own Spotify
    app, connect it.

**Rollback**
- **Before step 12.1** (the old app still exists): stop the worker as in step
  7, restore `production` from the backup branch from step 8, following
  `documentation/guides/production.md` ("Back to the pre-cutover snapshot",
  steps 1, 2, 4 and 5; its token-clearing step doesn't apply, because
  `TOKEN_ENCRYPTION_KEY` hasn't changed):
  ```sh
  npx neonctl branches restore production <backup branch id> \
    --project-id proud-dew-57305636 --preserve-under-name production-after-cutover
  ```
  Revert the merge on main, which also reverts `scripts/pre-deploy.sh`, so the
  old pre-deploy runs against the restored schema, then redeploy the api and
  then the worker. The domain is unchanged.
- **After step 12.1** the old app is gone, so the way out is a fix forward.

## Test Plan

The acceptance steps 12 and 13 are the test. Before the merge, CI on
`feat/workspaces` must be fully green and the staging gate passed.

## Verification Checklist
- [ ] Staging passed phases 06 to 08 and rehearsed the pre-deploy build of an
      emptied database.
- [ ] Production's `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
      `GOOGLE_REDIRECT_URI` and `PUBLIC_ORIGIN` were read back before the wipe
      (step 6).
- [ ] The Google consent screen is published and sign-in works for an account
      that isn't a test user.
- [ ] `curl -s https://raccoon.fm/health` returns 200.
- [ ] Railway api and worker deployments are SUCCESS on the merge SHA.
- [ ] The api's pre-deploy log shows the revision it stamped and the Neon
      branch it ran on, and no startup schema error was logged.
- [ ] The owner created a new Spotify app from the guide alone in under 15
      minutes, connected, and rebuilt their routines, with copy mismatches
      written down.
- [ ] A playlist was restored from the first scheduled run's snapshot through
      the workspace's app and matched its state before the run.
- [ ] The broken-app drill paused and then resumed schedules.
- [ ] The staging environment, Neon branch, Vercel variable and domain, and
      Google client are gone, and `SPOTIFY_CLIENT_ID`,
      `SPOTIFY_CLIENT_SECRET` and `SPOTIFY_REDIRECT_URI` are gone from Railway.
- [ ] The README describes Google sign-in and creating your own Spotify app.

## What NOT To Do
- Don't run `create-schema` through `railway run` from a laptop against
  production; the pre-deploy script runs it from the deployed code.
- Don't run `flask db upgrade` on the emptied database before `create-schema`.
- Don't merge before the wipe: the pre-deploy would upgrade the old database
  through the new migrations.
- Don't skip step 11: the merge's own worker build may have refused and run
  out its retries, so redeploy it by hand once the api is at head.
- Don't leave the old worker running through the wipe (step 7).
- Don't switch domain variables on this day; they moved in 01b.
- Don't delete the old Spotify app before the deploy is verified (step 10).
- Don't remove the old Spotify variables (`SPOTIFY_CLIENT_ID`,
  `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`) before step 14: rollback
  needs them until the old app is deleted.

## Context
- Source skill: forge · Area: ops, hosting, docs · Effort: S · Risk: High · Priority: High
