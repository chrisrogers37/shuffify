---
title: "[plan] Cutover: wipe, merge, owner acceptance (ops)"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-25
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
test. Sign-up does not open at the cutover: the consent screen stays in
Testing mode until #123, and the owner adds people by hand.

The domain moved in phase 01b, and the pre-deploy script learned to build a
new database in phase 06 (while `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA` is set,
from step 3 to step 11), so neither is done on this day.

**The wipe removes every account, and nobody else is told:** the other
accounts hold stale data from the original app. That is the plan's decision.
There is no rollback at any step: every failure is fixed forward (Fix
forward).

## Evidence

**What the deploy runs**
- The Railway api pre-deploy runs `scripts/pre-deploy.sh`. From phase 06 it
  runs `flask create-schema` and then `flask db upgrade`, both with
  `SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1`, while
  `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA=1` is set on the api, and only
  `flask db upgrade` otherwise. `create-schema` builds
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
- Both services build from `main` (the branch input given when the config
  was last applied, `.railway/README.md:13`), so the merge redeploys the
  worker too, whatever was done to it beforehand.
- Railway reads `.railway/` only when the config is applied, and a variable
  edit is a staged change that has to be deployed (overview, Evidence).
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

**Backups:** the 2026-10-17 cutover's backup branch is the precedent for
taking a backup branch first (step 8). Both backups are kept only for step
14's check of the rebuilt configuration, and step 14 deletes them.

**Vercel:** Vercel's Instant Rollback (`production.md:88`) turns off
auto-assignment of production domains until it is undone, so a fix-forward
deploy after one wouldn't go live.

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
   - **a dress rehearsal of this runbook at the cutover candidate SHA** (the
     branch as it will merge, after phases 07 and 08 and the last merge of
     main). Run step 6's staged command file against staging: remove
     staging's worker and api deployments; take a Neon snapshot branch; run
     the wipe statements as the role in staging's `DATABASE_URL`; flush
     staging's Redis by the method step 6 names; deploy the candidate SHA;
     run step 10's checks; redeploy the worker. Record how long each step
     takes: that is the expected downtime. The rehearsal builds the final
     models, which staging's first build in phase 06 did not, and it clears
     any drift left by a main migration that was re-parented beneath the
     branch's (overview, Dependencies);
   - the regression tests carried over with the security fixes merged from
     main (overview, Dependencies) are listed here and pass at the candidate
     SHA;
   - walking the setup pages against real Spotify with the existing app, so
     the copy is checked once before the timed run;
   - **the run of setup step 1 from scratch, on staging, with the owner's
     second Spotify account.** That account needs Premium to own an app
     (setup step 1 says so), and the run uses up its one development-mode
     client ID. It creates a new app from the guide alone, which the walk
     with the existing app can't show. It gates the cutover only: no build
     phase waits on it.

**Before cutover day**
2. **The owner creates production's Google OAuth client.** Sign-up does not
   open at the cutover: the consent screen stays in Testing mode until #123,
   and the owner adds people by hand.
   - authorized origin `https://raccoon.fm`;
   - redirect `https://raccoon.fm/auth/google/callback`;
   - scopes `openid email profile`, which are basic scopes with no
     sensitive-scope review;
   - the owner, and anyone added, as the consent screen's test users;
   - brand verification, and the privacy copy that describes Google
     sign-in, wait until #123.
3. **Give production's services the new variables, as a superset of the old
   ones.** Do it now, not on the day.
   1. Set the shared variables `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
      `GOOGLE_REDIRECT_URI`. A variable edit is a staged change: deploy it.
   2. Land a small PR on main that adds the `GOOGLE_*` references to
      `.railway/railway.ts` and keeps the `SPOTIFY_*` ones. A service sees a
      shared variable only through such a reference, and Railway reads the
      file only when the config is applied (Evidence), so the branch's own
      edit to that file gives production nothing. The old code ignores the
      new names.
   3. Apply that config to production (confirm the link with
      `railway status` and inspect the plan first) and deploy.
   4. Set `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA=1` on production's api service,
      not as a shared variable, after that apply (an apply can remove a
      variable the file doesn't name), and deploy the staged change. Main's
      pre-deploy doesn't read it, so it does nothing until the merge brings
      phase 06's script, whose pre-deploy then builds the wiped database
      (step 9). It goes at step 11.

   Keep the shared `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and
   `SPOTIFY_REDIRECT_URI`, and their references, until step 11: the old code
   reads them until step 7 stops it. Step 11 applies the merged
   `.railway/railway.ts`, which has no `SPOTIFY_*`, and removes them.
4. **The owner** clears their raid inbox and writes down what the wipe
   removes and has to be rebuilt by hand: schedule settings and routines,
   archive pairs, raid links, upstream sources, settings, and track locks if
   any exist by then. Make the list from a read-only export of the owner's
   own rows in production (ids and settings only, no names or emails), with a
   count of each kind, track locks included, read when the list is made, so
   the rebuild in step 13 doesn't depend on memory. The same rows can be read
   from step 8's backup after the wipe, until step 14 checks the rebuild
   against this list.
   **The wipe removes every account, and nobody else is told:** the other
   accounts hold stale data from the original app, so the list is the
   owner's alone. That is the plan's decision.
5. **Merge main into `feat/workspaces` one last time,** confirm which of #126
   and #128 to #131 have landed and are in, each with its regression test
   carried into the branch (overview, Dependencies), run
   `python scripts/check-migration-chain.py`, and wait for CI to be fully
   green.
   - **Main is frozen from this step through step 11.** No merge lands on
     main in that time except the cutover's own (step 9), the owner's and
     Dependabot's included. Announce the freeze before this step starts.
   - **If main moves anyway before the merge,** merge it into the branch
     again, run the chain check again, and merge only once CI has passed on
     that exact result. Railway deploys without waiting for checks and
     main's ruleset doesn't require an up-to-date branch, so a migration that
     landed on main in the gap would give the merge two Alembic heads, and
     `create-schema` fails on two heads, on the wiped database.
6. **Stage the commands** in a scratch file: the Neon identity check, the
   wipe statements, the Redis flush, the removal of the old worker's and
   api's deployments, and the new worker's redeploy.
   Verify the Neon branch's identity from Neon's metadata
   (`.railway/README.md`), not from a remembered id, and re-check whether
   production's `DATABASE_URL` is the pooled or the direct host. The dress
   rehearsal (step 1) runs this same file against staging.
   - **The wipe statements,** run through the branch's direct connection
     string (`neonctl connection-string`, as `production.md` does), as the
     role in the api's `DATABASE_URL`:
     ```sql
     SET lock_timeout = '5s';
     DROP SCHEMA public CASCADE;
     CREATE SCHEMA public;
     ```
     Then confirm, through the api's own `DATABASE_URL`, that `current_user`
     can create a table in `public`. **When staging the file, read** the
     api's role and the role `neonctl connection-string` hands out, the owner
     of every table in `public`, and the installed extensions. If the two
     roles are the same and own every table, the recreated schema belongs to
     the role `create-schema` runs as; if they differ, create the schema with
     `AUTHORIZATION <the api's role>`. If an extension other than `plpgsql`
     is installed, add its `CREATE EXTENSION` after `CREATE SCHEMA`, so the
     wipe loses none.
   - **The Redis flush:** `FLUSHDB` on database 0 of `REDIS_URL` (the app
     uses one URL and one database). Redis is a private Railway service, so
     the command has to run inside the project: for example through
     `railway ssh` into a running service, or a one-off command that uses
     `REDIS_URL`. The dress rehearsal fixes which, given that step 7 removes
     the api's deployment, and proves it on staging's Redis. Remove any TCP
     proxy added for it afterwards.
   - **Read back the variables the new api needs before it will start,** next
     to the Neon identity check. The new api refuses to start without
     `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI`
     (phase 05 step 2), and after the wipe the database is already empty, so a
     missing or mistyped value fails the deploy at the worst moment. Staging
     only rehearses staging's own variables, and its services were created by
     an apply of the branch's file, so it can't show a reference that is
     missing on production. **Read what each service resolves, not the
     shared list:** for the api, `GOOGLE_CLIENT_ID` is production's Google
     client from step 2, `GOOGLE_REDIRECT_URI` is exactly
     `https://raccoon.fm/auth/google/callback`, `PUBLIC_ORIGIN` is exactly
     `https://raccoon.fm`, `GOOGLE_CLIENT_SECRET` is set (compare it with
     the secret Google issued, without printing it into a shared log), and
     `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA` is `1` (step 3): without it the new
     pre-deploy can't build the wiped database.

**Cutover, in one sitting.** The old site serves until step 7, and the wipe
is followed immediately by the merge, so the downtime is the snapshot, the
wipe and one build. The dress rehearsal's timings say how long that is.
7. **Stop the old worker and the old api** so the old code doesn't run
   against the wiped database: in Railway, open each service's active
   deployment and choose Remove (`production.md`), then confirm no operation
   is running. Removing the api here, and not only the worker, means
   `DROP SCHEMA` doesn't wait behind a transaction the old api has open, and
   the old api never serves against the newly built schema while the new api
   is still starting. The merge builds a new worker from `main` anyway; if it
   starts before the api has built the schema it refuses on its schema check
   and exits (Evidence), and step 11 redeploys it by hand either way.
8. **Take the snapshot.** With the old worker and api stopped, create a Neon
   backup branch of `production` (the 2026-10-17 precedent; `npx neonctl
   branches create --name backup-before-workspaces-<date> --parent
   production --project-id proud-dew-57305636`, flags confirmed with
   `neonctl branches create --help` because the repo docs don't show this
   command), and note its id. It is kept only for step 14's check of the
   rebuilt configuration.
9. **Wipe and merge, back to back.**
   1. Run the staged wipe statements on `production` and flush the
      production Redis (step 6).
   2. Give production new `TOKEN_ENCRYPTION_KEY` and `SECRET_KEY` values,
      taking effect with the new code's deploy. The wipe leaves nothing
      encrypted and the flush ends every session, so nothing has to be
      carried across. The dress rehearsal settles how the change is staged
      and deployed with the merge.
   3. Merge `feat/workspaces` into main straight away (squash). The site
      serves errors until the new api is up: that is the downtime.
   4. Railway builds the api; its pre-deploy, with
      `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA` set (step 3), finds an empty
      database, builds the schema from the merged models, stamps head and
      logs the revision and the Neon endpoint id; `flask db upgrade` then has
      nothing to do. If a build is dropped or fails with a builder error,
      re-trigger it for the merge commit.
10. **Check the deploy:** the api deployment is SUCCESS on the merge SHA, the
    pre-deploy log shows the stamped revision and the Neon endpoint id it ran
    on (phase 06 step 7 logs both), `https://raccoon.fm/health` returns 200,
    and a Google sign-in as the owner reaches the setup page.
11. **Redeploy the worker** on the merge SHA by hand now that the api is at
    head (the merge's own worker build may have run out its restart retries
    against the unbuilt database), and confirm its deployment is SUCCESS and
    its health check passes. **Then retire the old Spotify settings,** with
    the new api and worker both up: apply the merged `.railway/railway.ts`
    (it has no `SPOTIFY_*` references) to production, confirming the link
    with `railway status` and inspecting the plan first, and deploy; then
    remove the shared `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and
    `SPOTIFY_REDIRECT_URI` from Railway (the redirect URL is now
    `PUBLIC_ORIGIN` plus `/spotify/callback`, F13) and deploy any staged
    change. Nothing running reads them, and no client secret stays in Railway
    (the design's decision 2). `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA` goes here
    too, now that the schema exists: the file doesn't name it, so it goes
    with the apply if the apply removes the variables the file doesn't
    declare, and otherwise delete it from the api by hand with the
    `SPOTIFY_*` removal. Confirm it is gone: from then on an empty database
    fails the deploy again, as the repo's rule says (phase 06 step 7). These
    deploys restart the api and the worker once more, here: wait until both
    are SUCCESS before step 12, so the apply redeploys nothing during steps 12
    and 13.

**Acceptance, part one: by the owner, in the same sitting**
12. **The point of no return, and the timed setup (the threshold is 15
    minutes).** With a timer, on raccoon.fm:
    1. Delete the old Spotify app in Spotify's dashboard. This can't be
       undone, and the old site's tokens go with the app. (Spotify allows one
       development-mode app per person, so this is the only way to make the
       new one.)
    2. Sign in with Google, then follow the in-product guide alone: create a
       new Spotify app (step 1), add the redirect URL
       `https://raccoon.fm/spotify/callback` and the account (step 2), paste
       the client ID (step 3) and connect (step 4).
    3. Record the time from step 1 to the person's own playlists. **The
       threshold is 15 minutes.** If it is longer, the copy follow-up PR also
       fixes the slowest step. Write down every place the copy or screenshots
       differ from Spotify's live dashboard, and fix them in that PR.

**Acceptance, part two: at the next schedule slots.** A slot can be a day
away, so this part isn't one sitting.
13. Rebuild through the product, in this order: archive pairs, raid links,
    upstream sources and settings (and any track locks) from step 4's list
    first, then schedules and routines. Turn a routine on only once what it
    works with is back: a routine that runs before its pairs, sources or
    locks are rebuilt changes real playlists on Spotify, which the wipe
    itself never touched. Watch the first scheduled run of each routine;
    snapshots (restore points) start fresh. If any track lock was rebuilt,
    confirm that a `super`-locked track keeps its position through the first
    scheduled shuffle.
    **Restore from one:** once the first scheduled run has produced a snapshot,
    open that run in the activity view, restore the playlist from its snapshot
    through the workspace's app, and confirm the playlist is back as it was
    before the run. Then break the app on purpose (remove Raccoon's access in
    the Spotify account's apps page), confirm the "uh oh" page appears (the
    access-removed case) and schedules pause, connect again, and confirm they
    resume at their next slot.

**Clean-up, after step 13**
14. In this order:
    1. Check the owner's hand-rebuilt configuration against step 4's list,
       then delete step 8's backup branch and the 2026-10-17 backup. They
       have no other end date.
    2. Delete staging: the `staging` Railway environment, staging's Neon
       branch or project, the Vercel branch variable and the Deployment
       Protection Exception for `staging.raccoon.fm`, the `staging` DNS
       record and then the `staging.raccoon.fm` domain in Vercel (the record
       first), and the staging Google client with its project.
    3. Update `documentation/guides/production.md`, the README and
       `.railway/README.md` with the new variables and this runbook's outcome
       (phase 06 step 7 already documented the pre-deploy variable); their "no
       staging environment" lines are correct again. Correct their database
       host lines too: `production.md:16` and `.railway/README.md:5` say the
       api, the pre-deploy and the worker use the direct endpoint, and
       production uses the pooled host (as re-checked at step 6).
       **Update the README's sign-in wording** too: the "Spotify sign-in"
       feature bullet (README.md:34) and Getting Started step 1
       (README.md:38) still describe signing in with Spotify. They become:
       sign in with Google, create your own Spotify app, connect it. Sign-up
       does not open at the cutover: the consent screen stays in Testing mode
       until #123, and the owner adds people by hand.

**Fix forward**

There is no rollback, at any step: every failure is fixed forward. Deleting
the old app can't be undone, and a rollback window would keep a client secret
in Railway, which the design's ratified decision 2 (no client secret
anywhere) rules out. The backups stay only for step 14's check of the rebuilt
configuration.

## Test Plan

The acceptance steps 12 and 13 are the test. Before the merge, CI on
`feat/workspaces` must be fully green and the staging gate passed, the dress
rehearsal and the run of setup step 1 from scratch included.

## Verification Checklist
- [ ] Staging passed phases 06 to 08 and rehearsed the pre-deploy build of an
      emptied database.
- [ ] Staging ran the dress rehearsal of this runbook at the candidate SHA,
      and its timings are written down.
- [ ] The owner's second Spotify account ran setup step 1 from scratch on
      staging (step 1).
- [ ] The `GOOGLE_*` references were applied to production, and what the api
      resolves for `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
      `GOOGLE_REDIRECT_URI`, `PUBLIC_ORIGIN` and
      `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA` was read back before the wipe
      (steps 3 and 6).
- [ ] Main took no merge from step 5 through step 11 except the cutover's
      own, and the freeze was announced before step 5.
- [ ] The Google consent screen is in Testing mode, with the owner and anyone
      added as test users, and sign-in works for them.
- [ ] The regression tests carried over with the security fixes pass at the
      merge SHA.
- [ ] `curl -s https://raccoon.fm/health` returns 200.
- [ ] Railway api and worker deployments are SUCCESS on the merge SHA.
- [ ] The api's pre-deploy log shows the revision it stamped and the Neon
      endpoint id it ran on, and no startup schema error was logged.
- [ ] The owner created a new Spotify app from the guide alone in under 15
      minutes, connected, and rebuilt their routines, with copy mismatches
      written down.
- [ ] Pairs, raid links, sources and settings were rebuilt from step 4's
      list before any routine was turned on.
- [ ] A playlist was restored from the first scheduled run's snapshot through
      the workspace's app and matched its state before the run.
- [ ] The broken-app drill paused and then resumed schedules.
- [ ] The merged `.railway/railway.ts` was applied to production once the
      new api and worker were up, and `SPOTIFY_CLIENT_ID`,
      `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` and
      `SHUFFIFY_PREDEPLOY_CREATE_SCHEMA` are gone from Railway (step 11).
- [ ] The rebuilt configuration matched step 4's list before both backups
      were deleted, and the staging environment, database, Vercel variable,
      protection exception, DNS record and domain, and Google client are gone
      (step 14).
- [ ] The README describes Google sign-in and creating your own Spotify app.

## What NOT To Do
- Don't run `create-schema` through `railway run` from a laptop against
  production; the pre-deploy script runs it from the deployed code.
- Don't run `flask db upgrade` on the emptied database before `create-schema`.
- Don't merge before the wipe: the pre-deploy would upgrade the old database
  through the new migrations. (Phase 04's migration refuses a database that
  has rows, which would fail that deploy and leave the data alone, but don't
  lean on it.)
- Don't skip step 11: the merge's own worker build may have refused and run
  out its retries, so redeploy it by hand once the api is at head.
- Don't leave the old worker or the old api running through the wipe (step
  7).
- Don't switch domain variables on this day; they moved in 01b.
- Don't delete the old Spotify app before the deploy is verified (step 10).
- Don't remove the old Spotify variables (`SPOTIFY_CLIENT_ID`,
  `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`), or apply the merged
  `.railway/railway.ts` to production, before step 11: the old code reads
  them until step 7, and the apply redeploys the services, so it waits until
  the merge's api and worker are up.
- Don't merge anything else to main from step 5 through step 11.
- Don't start step 14 before step 13 is done.
- Don't use Vercel's Instant Rollback for the frontend (Evidence).

## Context
- Source skill: forge · Area: ops, hosting, docs · Effort: S · Risk: High · Priority: High
