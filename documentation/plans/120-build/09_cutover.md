---
title: "[plan] Cutover: wipe, merge, owner acceptance — ops"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, ops]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: S, but ordered
and irreversible once the owner deletes their old Spotify app.

`feat/workspaces` merges into main, production's database is wiped and
rebuilt, and the owner signs up as the first user, creates a new Spotify app
from the in-product guide alone, and rebuilds their routines. That is the
acceptance test. This is the cutover, not the public launch: the public
launch is #123, and #120 stays closed to strangers until its safeguards ship.

The domain moved in phase 01b, so there is no domain work on this day.

## Evidence

**What the deploy runs**
- The Railway api pre-deploy runs `scripts/pre-deploy.sh`, which is
  `SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 flask db upgrade`.
- `migrations/env.py:54-66` refuses to upgrade a database with no revision.
- `flask create-schema` (`shuffify/cli.py:15-53`) builds the schema from the
  models and stamps head. Its docstring says to run it ahead of
  `flask db upgrade` wherever the database may be new. It does nothing when
  the database already has a revision, and refuses a database that has the
  tables but no revision.
- The app's startup schema check refuses a database that isn't at head unless
  `SHUFFIFY_MIGRATION_STEP` is set (`shuffify/__init__.py:214, 284`). So
  `railway run … flask create-schema` against production, from a laptop,
  fails the startup check unless that variable is set, and it builds
  whatever schema the local checkout's models describe, not the deployed
  code's.

**Hosting**
- DNS is on DigitalOcean (`documentation/guides/production.md`). By this
  phase raccoon.fm serves the site and shuffify.app redirects to it (01b).
- Vercel serves the web app, Railway the API. Production uses Neon branch
  `production`.
- Sessions and worker coordination live in Redis, so wiped users would leave
  sessions that point at rows that no longer exist.

## Implementation Plan

### Dependencies
01, 01b, 03 to 08. The staging gate below.

### Blocks
#121, #123.

### Steps

**Gate: staging**
1. Phases 06 to 08 have passed their staging checks against real Spotify and
   Google. The database steps of the runbook below (empty the Neon branch,
   deploy with the pre-deploy change, flush Redis) have been rehearsed on
   staging. Optionally, a friend with a Premium Spotify account walks the
   guide on staging first (they're added as a Google test user).

**Before cutover day**
2. **Change `scripts/pre-deploy.sh` to run `flask create-schema` before
   `flask db upgrade`,** on `feat/workspaces`, with the same
   `SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1` prefix. On a database
   that has a revision it's a no-op; on an empty one it builds the schema
   from the models being deployed and stamps head, and the upgrade then finds
   nothing to do. Update `tests/test_runtime_launch.py` and the README's
   migration note.
3. **The owner creates production's Google OAuth client:**
   - authorized origin `https://raccoon.fm`;
   - redirect `https://raccoon.fm/auth/google/callback`;
   - scopes `openid email profile`;
   - the consent screen **stays in Testing mode**, with the owner and any
     invited tester listed. That keeps #120 closed to strangers; publishing
     moves to #123.
4. **Set the new Railway shared variables:** `GOOGLE_CLIENT_ID`,
   `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI`. `PUBLIC_ORIGIN` and
   `SPOTIFY_REDIRECT_URI` already point at raccoon.fm. Keep the old
   `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` until step 11.
5. **The owner** clears their raid inbox and writes down their schedule
   settings.
6. **Merge main into `feat/workspaces` one last time.** CI must be green.

**Cutover, on a quiet day**
7. **Prepare the database.**
   1. Pause the Railway worker.
   2. Take a Neon branch snapshot of `production` as a rollback point,
      checking the branch identity from Neon's metadata first.
   3. Drop and recreate the public schema on `production`.
   4. Flush the production Redis.
8. **Deploy.**
   1. Merge `feat/workspaces` into main.
   2. The Railway pre-deploy runs `create-schema`, which builds the schema
      from the merged models and stamps head; `flask db upgrade` is then a
      no-op.
   3. The api starts at head; resume the worker.
9. **Check the deploy** before anything irreversible: both Railway
   deployments are SUCCESS on the merge SHA, `https://raccoon.fm/health`
   returns 200, and a Google sign-in as the owner reaches the setup page.
10. **The point of no return: the owner deletes their old Spotify app** in
    Spotify's dashboard (one app per developer, so the new one can't be made
    otherwise). From here, rolling back to the old site isn't possible; the
    old app's tokens are the only thing the snapshot holds.
11. **Remove the old variables** from Railway: `SPOTIFY_CLIENT_ID` and
    `SPOTIFY_CLIENT_SECRET`.
12. **Update the docs:** `documentation/guides/production.md`, the README and
    `.railway/README.md` get the new variables and this runbook's outcome.

**Acceptance, by the owner**
13. On raccoon.fm, with a timer, follow the in-product guide from scratch:
    1. Sign in with Google.
    2. Create a new Spotify app (step 1), add the redirect URL and the
       account (step 2), paste the client ID (step 3) and connect (step 4).
    3. Confirm it took about ten minutes, and write down every place the
       copy or screenshots differ from Spotify's live dashboard. Fix the
       copy in a follow-up PR.
    4. Rebuild schedules and routines through the product.
14. Watch the first scheduled run of each routine. Snapshots (restore
    points) start fresh.
15. Break the app on purpose: remove Raccoon's access in the Spotify
    account's apps page. Confirm the "uh oh" page appears and schedules
    pause. Connect again, then confirm schedules resume at their next slot.

**Rollback** before step 10: restore the Neon snapshot branch, revert the
merge on main, and redeploy; the domain is unchanged. After step 10 the old
app is gone, so the way out is a fix forward.

## Test Plan

The acceptance steps 13 to 15 are the test. Before the merge, CI on
`feat/workspaces` must be fully green and the staging gate passed.

## Verification Checklist
- [ ] Staging passed phases 06 to 08 and rehearsed the database steps.
- [ ] `curl -s https://raccoon.fm/health` returns 200.
- [ ] Railway api and worker deployments are SUCCESS on the merge SHA.
- [ ] The schema is stamped at the merged head and no startup schema error
      was logged.
- [ ] The owner has created a new Spotify app from the guide alone, connected
      and rebuilt their routines, and the first scheduled runs succeeded.
- [ ] Step 15's broken-app drill paused and then resumed schedules.
- [ ] The retired Spotify variables are gone from Railway.

## What NOT To Do
- Don't run `create-schema` through `railway run` from a laptop against
  production; the pre-deploy script runs it from the deployed code.
- Don't run `flask db upgrade` on the emptied database before
  `create-schema`.
- Don't switch domain variables on this day; they moved in 01b.
- Don't delete the old Spotify app before the deploy is verified (step 9).
- Don't remove the old variables before the new deploy is verified.
- Don't publish the Google consent screen or announce the site; #123 owns
  the public opening.

## Context
- Source skill: forge · Area: ops, hosting, docs · Effort: S · Risk: High · Priority: High
