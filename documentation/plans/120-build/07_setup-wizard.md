---
title: "[plan] Guided setup in the web app — web/src"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, web]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: M.

This builds the five setup steps from the design's mockups in the production
design system. A signed-in person whose workspace isn't fully set up lands
on the setup steps, not the app.

## Evidence

- **Mockups:** `documentation/plans/120-mockups/setup-steps.html`. They use
  inline CSS that copies the design tokens, not `sh-*` classes.
- **Components to reuse:**
  - `web/src/components/ui/controls.tsx`: `Button`, `Input`, `Field`,
    `ConsentCheckbox`;
  - `web/src/components/ui/States.tsx`;
  - `Toast`.
- **CSS recipes to reuse** (`web/src/design/generated/recipes.css`):
  - `sh-routine-progress` (L2830-2835), already written but unused;
  - `sh-routine-callout-{warning,success}` (L1362-1374);
  - `sh-surface`, `sh-spinner`, `sh-setup-nav`.
- **Route guards:** `AuthGuard` (`web/src/auth/SessionProvider.tsx:67-119`);
  the routers are in `web/src/app/*-routes.tsx`.
- The session shape comes from phase 05, and the endpoints from phase 06.

## Implementation Plan

### Dependencies
05. It can build against fixtures in parallel with 06.

### Blocks
09.

### Steps

1. **Add a `/setup` route** under `AppShell`.
   - `AuthGuard` sends a signed-in person with `setup.spotify_connected = false`
     (and an app that isn't `broken`) to `/setup`.
   - It sends a person whose app is `broken` to `/setup/problem` (phase 08).
2. **The step pages** (`web/src/pages/setup/`). The step bar is
   `sh-routine-progress` with `aria-current="step"`.
   1. **Name the workspace.** This calls a new `PATCH /api/workspace`
      `{name}` in the backend.
   2. **Create a Spotify app.** Numbered instructions, plus a button that
      opens `https://developer.spotify.com/dashboard` in a new tab.
   3. **Redirect URL and allow-list.** A read-only field with the site's
      redirect URL from `/api/session` (not hard-coded), a copy button and
      a confirm checkbox.
   4. **Paste the keys.** `Field` and `Input` for the ID and a password-type
      input for the secret. "Check with Spotify" saves, then checks, and
      shows the checking, rejected and accepted states as callouts.
   5. **Connect Spotify.** A form `GET` to `/spotify/connect`. Coming back
      with a notice shows that notice's fix, linking to the step it names.
3. **Resume where they left off.** The current step comes from the session's
   setup state (no app, unchecked, ok but not connected). Steps 1–3 are
   instructions, so going back and forth between them is free.
4. **Copy follows the mockups.** Screenshots of Spotify's dashboard are
   placeholders until they've been checked against the live dashboard
   (design: left to the builder).
5. **Add fixtures** to `web/src/fixtures/transport.ts` for each setup state
   and each notice, plus a Playwright suite `playwright.setup.config.ts`.

## Test Plan
- **Playwright** at 360px and desktop:
  - each step renders;
  - the guard sends each setup state to the right step;
  - the key check's three states;
  - each connect notice's fix;
  - the copy button;
  - accessibility (headings, focus moves to the h1, alerts announced).
- **`npm test`** contract tests for the new endpoints.
- **The harness e2e first-run test** from 06 now drives the real setup
  pages.

## Verification Checklist
- [ ] `npx playwright test -c playwright.setup.config.ts` passes.
- [ ] The harness e2e first-run test passes through the setup pages.
- [ ] No setup page shows the secret after it's saved.

## What NOT To Do
- Don't hard-code `raccoon.fm` in the web app; the redirect URL comes from
  the API.
- Don't invent new design tokens; reuse the existing recipes.
- Don't add a "skip setup" path into the app.

## Context
- Source skill: forge · Area: web/src/pages/setup, auth guard · Effort: M · Risk: Low · Priority: High
