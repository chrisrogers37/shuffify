---
title: "[plan] Move the domain to raccoon.fm (DNS, Vercel, Railway, Spotify)"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-25
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
rather than application code. A failure in it is fixed forward, as at every
step of this plan (Fix forward).

## Evidence

- DNS is on DigitalOcean (zone `shuffify.app`); the apex `A` records point
  at Vercel (`documentation/guides/production.md`).
- Vercel project `shuffify` serves the web app and forwards `/api`,
  `/login`, `/callback`, `/logout` and `/health` to `RAILWAY_API_URL`
  (`web/scripts/build-vercel-output.mjs:56-57`).
- The API accepts unsafe requests only from `PUBLIC_ORIGIN`
  (`shuffify/csrf_origin.py`). `PUBLIC_ORIGIN` and `SPOTIFY_REDIRECT_URI` are
  shared Railway variables: the api references `PUBLIC_ORIGIN` at
  `.railway/railway.ts:90`, and `SPOTIFY_REDIRECT_URI` is in the shared block
  at `:50`. A change to a shared value is a staged change: nothing redeploys
  until it is deployed.
- The site already sends `Strict-Transport-Security` with
  `includeSubDomains; preload` (`web/security-headers.ts:18`).
- The Vercel project also answers at `shuffify.vercel.app`
  (`documentation/guides/production.md:12`).
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
   - Once `https://raccoon.fm` serves the site with its
     `Strict-Transport-Security` header, submit raccoon.fm at
     hstspreload.org. Inclusion ships with browser releases over weeks, so it
     starts here and not at the cutover.
   - The overview's staging address check (Staging, Address) can run from
     this point; it doesn't wait for phase 06.
2. Add two redirect URIs to the current Spotify app, next to the shuffify.app
   one: `https://raccoon.fm/callback` (the current site) and
   `https://raccoon.fm/spotify/callback` (the new flow's callback, F13). Don't
   remove the old one yet. In the same dashboard visit, rename the app to
   "Raccoon": Spotify's consent dialog shows the app's own name, to new grants
   and on staging's walk, which reuses this app.
3. In one sitting, on a quiet moment, change the shared Railway variables
   `PUBLIC_ORIGIN` to `https://raccoon.fm` and `SPOTIFY_REDIRECT_URI` to
   `https://raccoon.fm/callback`, and deploy the staged changes. Once that
   deploy is live, set shuffify.app, and `shuffify.vercel.app` with it, to
   redirect to raccoon.fm in Vercel with a 307. Writes on shuffify.app are
   refused from the deploy until the redirect is live, so do these back to
   back.
   - **The redirect is a 307 until the move has held, then a 308,** the
     permanent redirect the design's decision 4 asks for. Browsers cache a
     308, and a 307 keeps the redirect changeable while the move settles.
     Once the move has held, switch both domains' redirects to 308.
4. Sign in on raccoon.fm and confirm a write works (for example, a setting).
   Everyone signs in again once, because the cookie belongs to one host.
   Check a deep link with a query too: `https://shuffify.app/routines?x=1`
   lands on `https://raccoon.fm/routines?x=1`, since returning people arrive
   by bookmark.

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
- [ ] `curl -sI https://shuffify.app` returns a 307 to `https://raccoon.fm`
      after step 3, and a 308 once the move has held and both redirects
      were switched.
- [ ] `curl -s https://raccoon.fm/health` returns 200.
- [ ] Signing in on raccoon.fm returns to raccoon.fm, and a write succeeds.
- [ ] Railway api and worker deployments are SUCCESS.
- [ ] The current Spotify app lists the raccoon.fm `/callback` and
      `/spotify/callback` redirect URIs.
- [ ] `https://shuffify.app/routines?x=1` redirects to
      `https://raccoon.fm/routines?x=1`, and `https://shuffify.vercel.app`
      redirects to raccoon.fm.
- [ ] raccoon.fm has been submitted at hstspreload.org.
- [ ] The current Spotify app is named Raccoon.

## What NOT To Do
- Don't change the cookie name or other internal identifiers.
- Don't flip the Railway variables and leave shuffify.app unredirected.
- Don't make the redirect a 308 before the move has held.

## Fix forward
There is no rollback, here or at any step of this plan: every failure is
fixed forward. Deleting the old app at the cutover can't be undone, and a
rollback window would keep a client secret in Railway, which the design's
ratified decision 2 (no client secret anywhere) rules out.

## Context
- Source skill: forge · Area: ops, hosting, docs · Effort: S · Risk: Low · Priority: Medium
