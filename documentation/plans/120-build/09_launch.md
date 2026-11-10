---
title: "[plan] Launch: wipe, cut over to raccoon.fm, owner acceptance — ops"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, ops]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: S, but ordered
and irreversible.

`feat/workspaces` merges into main, production's database is wiped and
rebuilt, the site moves to raccoon.fm, and the owner signs up as the first
user and rebuilds their routines. That's the acceptance test.

## Evidence

**What the deploy runs**
- The Railway api pre-deploy runs `flask db upgrade` (`scripts/pre-deploy.sh`).
- `migrations/env.py:54-66` refuses to upgrade a database with no revision.
- `flask create-schema` (`shuffify/cli.py:15-53`) builds the schema and
  stamps head.

**Hosting**
- DNS is on DigitalOcean (`documentation/guides/production.md:9-16`).
- Vercel serves the web app, Railway the API. Domain settings:
  `PUBLIC_ORIGIN`, `SPOTIFY_REDIRECT_URI`, `RAILWAY_API_URL`.
- Production uses Neon branch `production`.

## Implementation Plan

### Dependencies
01, 03–08.

### Blocks
#121, #123.

### Steps

**Before launch day**
1. **The owner:**
   - buys raccoon.fm and adds DNS records for Vercel;
   - creates the Google OAuth client:
     - authorized origin `https://raccoon.fm`;
     - redirect `https://raccoon.fm/auth/google/callback`;
     - consent screen **published**, scopes `openid email profile`.
2. **Set the new Railway variables:**
   - `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`;
   - `GOOGLE_REDIRECT_URI`;
   - `APP_SECRET_KEYS`, a newly generated Fernet key;
   - `PUBLIC_ORIGIN=https://raccoon.fm`;
   - `SPOTIFY_REDIRECT_URI=https://raccoon.fm/callback`.

   Keep the old variables until step 7.
3. **The owner:**
   - adds `https://raccoon.fm/callback` to their existing Spotify app's
     redirect URIs;
   - clears their raid inbox;
   - writes down their schedule settings.
4. **Merge main into `feat/workspaces` one last time.** CI must be green.

**Launch, on a quiet day**
5. **Prepare the database.**
   1. Pause the Railway worker.
   2. Take a Neon branch snapshot of `production` as a rollback point.
   3. Drop and recreate the public schema.
   4. Run `flask create-schema` once against it, through
      `railway run --service api`.
6. **Deploy.**
   1. Merge `feat/workspaces` into main.
   2. The pre-deploy upgrade finds the schema at head and is a no-op.
   3. Add `raccoon.fm` to the Vercel project.
   4. Set `shuffify.app` to permanently redirect to `raccoon.fm`.
   5. Resume the worker.
7. **Remove the old variables** from Railway: `SPOTIFY_CLIENT_ID` and
   `SPOTIFY_CLIENT_SECRET`.
8. **Update the docs:** `documentation/guides/production.md`, the README and
   `.railway/README.md` get the new domain, the variables and this runbook's
   outcome.

**Acceptance, by the owner**
9. On raccoon.fm:
   1. Sign in with Google.
   2. Name the workspace.
   3. Paste the existing Spotify app's ID and secret.
   4. Check them.
   5. Connect Spotify.
   6. Rebuild schedules and routines through the product.
10. Watch the first scheduled run of each routine. Snapshots (restore
    points) start fresh.
11. Break the app on purpose, by resetting the secret on Spotify's
    dashboard. Confirm the "uh oh" page appears and schedules pause. Paste
    the new secret, then confirm schedules resume at their next slot.

**Rollback** before step 7: restore the Neon snapshot branch, revert the
merge on main, and point DNS back.

## Test Plan

The acceptance steps 9–11 are the test. Before the merge, CI on
`feat/workspaces` must be fully green.

## Verification Checklist
- [ ] `curl -sI https://shuffify.app` returns a permanent redirect to
      `https://raccoon.fm`.
- [ ] `curl -s https://raccoon.fm/health` returns 200.
- [ ] Railway api and worker deployments are SUCCESS on the merge SHA.
- [ ] The owner has completed setup and rebuilt their routines, and the first
      scheduled runs succeeded.
- [ ] Step 11's broken-app drill paused and then resumed schedules.

## What NOT To Do
- Don't run `flask db upgrade` on the emptied database before
  `create-schema`.
- Don't remove the old variables before the new deploy is verified.
- Don't launch with the Google consent screen in "testing".

## Context
- Source skill: forge · Area: ops, hosting, docs · Effort: S · Risk: High · Priority: High
