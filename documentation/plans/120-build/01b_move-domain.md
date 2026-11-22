---
title: "[plan] Move the domain to raccoon.fm — DNS, Vercel, Railway, Spotify"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, ops, rename]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: S. Triggered
when the owner buys raccoon.fm.

The current site moves to raccoon.fm on main, long before the workspaces
code ships. The redirect URL every person will paste into their Spotify app
is `https://raccoon.fm/spotify/callback`, and it is already on the current
site's Spotify app by the time the new flow arrives (staging uses that app).
The current site keeps using `https://raccoon.fm/callback` until the cutover. Cutover day (phase 09) then
needs no domain work: it is the wipe and the merge.

This is its own phase, not a second PR of phase 01. The rename has no
external dependency and should merge as soon as it's ready; this phase
waits for a purchase and changes DNS, Vercel, Railway and Spotify settings
rather than application code. Each gets its own rollback.

## Evidence

- DNS is on DigitalOcean (zone `shuffify.app`); the apex `A` records point
  at Vercel (`documentation/guides/production.md`).
- Vercel project `shuffify` serves the web app and forwards `/api`,
  `/login`, `/callback`, `/logout` and `/health` to `RAILWAY_API_URL`
  (`web/scripts/build-vercel-output.mjs:56-57`).
- The API accepts unsafe requests only from `PUBLIC_ORIGIN`
  (`shuffify/csrf_origin.py`). `PUBLIC_ORIGIN` and `SPOTIFY_REDIRECT_URI` are
  shared Railway variables read by `.railway/railway.ts:90`, so a change to
  the shared value redeploys the services.
- The session cookie has no `Domain` attribute (`config.py:164-184`), so it
  belongs to one host. Signing in again once on the new domain is expected.
- The old domain appears in `README.md`, `.env.example`,
  `.railway/README.md` and `documentation/guides/production.md`.
- The current site's Spotify app has `https://shuffify.app/callback` as its
  redirect URI today.

## Implementation Plan

### Dependencies
01 (the rename). The owner buys raccoon.fm.

### Blocks
09. Phase 06's code doesn't need it; only 06's staging step needs the domain
bought, because staging is `staging.raccoon.fm`.

### Steps

**The owner, in order**
1. Buy raccoon.fm and point its DNS at Vercel. Add `raccoon.fm` to the
   Vercel project as a second domain. Both domains now serve the site.
2. Add two redirect URIs to the current Spotify app, next to the shuffify.app
   one: `https://raccoon.fm/callback` (the current site) and
   `https://raccoon.fm/spotify/callback` (the new flow's callback, F13). Don't
   remove the old one yet.
3. In one sitting, on a quiet moment, change the shared Railway variables
   `PUBLIC_ORIGIN` to `https://raccoon.fm` and `SPOTIFY_REDIRECT_URI` to
   `https://raccoon.fm/callback`, then set shuffify.app to permanently
   redirect to raccoon.fm in Vercel. Writes on shuffify.app are refused from
   the variable change until the redirect is live, so do these back to back.
4. Sign in on raccoon.fm and confirm a write works (for example, a setting).
   Everyone signs in again once, because the cookie belongs to one host.

**In the repository**
5. Update the domain and variable values in `README.md`, `.env.example`,
   `.railway/README.md` and `documentation/guides/production.md`, and record
   the move in the production guide.
6. Update `tests/test_split_api_public_origin_config.py` fixtures if they
   assert the old domain. Nothing in the application code or the legal copy
   names the domain.

## Test Plan
- The configuration tests that read `PUBLIC_ORIGIN` still pass with the new
  value.
- By hand, after step 3: sign in on raccoon.fm, make a write, and confirm the
  Spotify callback returns to raccoon.fm.

## Verification Checklist
- [ ] `curl -sI https://shuffify.app` returns a permanent redirect to
      `https://raccoon.fm`.
- [ ] `curl -s https://raccoon.fm/health` returns 200.
- [ ] Signing in on raccoon.fm returns to raccoon.fm, and a write succeeds.
- [ ] Railway api and worker deployments are SUCCESS.
- [ ] The current Spotify app lists the raccoon.fm `/callback` and
      `/spotify/callback` redirect URIs.

## What NOT To Do
- Don't remove `https://shuffify.app/callback` from the Spotify app until
  the cutover has finished: it is the rollback path.
- Don't change the cookie name or other internal identifiers.
- Don't flip the Railway variables and leave shuffify.app unredirected.

## Rollback
Set the two Railway variables back, remove the Vercel redirect and the
raccoon.fm domain. The Spotify app keeps both redirect URIs throughout.

## Context
- Source skill: forge · Area: ops, hosting, docs · Effort: S · Risk: Low · Priority: Medium
