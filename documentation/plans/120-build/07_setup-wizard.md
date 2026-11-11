---
title: "[plan] Guided setup in the web app — web/src"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, web]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: M.

This builds the four setup steps from the design's mockups in the production
design system. A signed-in person whose workspace isn't fully set up lands
on the setup steps, not the app. There is no workspace-naming step and no
secret to paste: the person creates a Spotify app, adds the redirect URL and
their account to it, pastes the client ID and connects.

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
- **What this phase needs from earlier phases:**
  - from 05: `/api/session` with `setup.spotify_app`, `setup.spotify_connected`
    and `setup.redirect_url` (`PUBLIC_ORIGIN` plus `/spotify/callback`, so the
    page shows it without hard-coding a domain);
  - from 06: `PUT /api/workspace/spotify-app` `{client_id}`, the
    `GET /spotify/connect` start and the notice codes
    (`spotify_user_not_allowed`, `wrong_spotify_account`,
    `spotify_account_in_use_elsewhere`, and `oauth_failed` for an
    `invalid_grant` at the code exchange) that come back after
    `/spotify/callback`,
    and `app_in_use_elsewhere`, which the save returns;
  - from 08: the `/setup/problem` page.

  There is no workspace-rename endpoint to depend on.

## Implementation Plan

### Dependencies
05 (session shape and `redirect_url`), 06 (endpoints) and 08 (the
`/setup/problem` page that a `broken` workspace is sent to; nothing sets
`broken` before 08 lands, so the redirect is harmless until then). It can
build against fixtures in parallel with 06 and 08, which supply the
endpoints and the problem page before this merges.

### Blocks
09.

### Steps

1. **Add a `/setup` route** under `AppShell`.
   - `AuthGuard` sends a signed-in person with `setup.spotify_connected = false`
     (and an app that isn't `broken`) to `/setup`.
   - It sends a person whose app is `broken` to `/setup/problem` (phase 08).
2. **The step pages** (`web/src/pages/setup/`). The step bar is
   `sh-routine-progress` with `aria-current="step"`, four steps.
   1. **Create a Spotify app.** Says first that Spotify requires a Premium
      account to own an app, and that Spotify allows one development app
      per developer (an old one has to be deleted in the dashboard before a
      new one can be made). Numbered instructions, plus a button that opens
      `https://developer.spotify.com/dashboard` in a new tab.
   2. **Redirect URL and allow-list,** two panels from the mockup and one
      confirm checkbox for both.
      - *Add the redirect URL.* A read-only field with the site's redirect
        URL from `/api/session` (not hard-coded) and a copy button. It says
        that if the URL is wrong or unsaved, Spotify stops on its own error
        page and never returns to Raccoon, and to come back here and check it
        was saved exactly.
      - *Allow your Spotify account.* "Under **User Management**, add the
        name and email of the Spotify account you'll connect." It says an app
        allows five accounts and that the account added must be the one
        connected in step 4, because the workspace is bound to the first
        account it connects.
      - "I've done both" enables Continue.
   3. **Paste the client ID.** `Field` and `Input` for the ID, checked for
      32 hexadecimal characters as the person types and by
      `PUT /api/workspace/spotify-app` on save. It says plainly: only the
      client ID, never the secret. It also says that a wrong ID takes you to
      a Spotify page reading "INVALID_CLIENT", that you'll never come back
      to Raccoon from it, and that you should return here and fix the ID.
   4. **Connect Spotify.** A form `GET` to `/spotify/connect`, with a link
      back to step 3 to change the ID. Coming back with a notice shows that
      notice's fix, linking to the step it names. The notices:
      - `spotify_user_not_allowed`: "This Spotify account isn't allowed on
        your app." Links to step 2's allow-list panel.
      - `spotify_app_rejected`: "Spotify didn't recognise this app." The
        client ID is wrong or the app was deleted. Links to step 3 to check
        the ID, and to step 1 if the app no longer exists.
      - `oauth_failed`: "Spotify didn't finish connecting. The sign-in
        expired or was already used." Connect Spotify again; nothing else
        changes and the app isn't marked broken.
      - `spotify_account_in_use_elsewhere`: "This Spotify account is already
        connected to another workspace." Use a different account.
      - `wrong_spotify_account`: "This isn't the Spotify account this
        workspace started with." Connect the original account. It also says
        that switching to a different account isn't available yet.
      - `app_in_use_elsewhere`, which comes back from saving the client ID
        in step 3 rather than from connecting: "This app is already used by
        another workspace. Each Spotify app can belong to only one
        workspace, so create a new app (step 1) and paste its client ID."
        Links to step 1.
3. **Resume where they left off.** The current step comes from the session's
   setup state: no app saved goes to step 1; an app saved (`unchecked`) goes
   to step 4; connected goes to the app. Steps 1 and 2 are instructions, so
   going back and forth between them is free.
4. **Copy follows the mockups.** Screenshots of Spotify's dashboard are
   placeholders until they've been checked against the live dashboard
   (design: left to the builder).
5. **Add fixtures** to `web/src/fixtures/transport.ts` for each setup state
   and each notice, plus a Playwright suite `playwright.setup.config.ts`.

## Test Plan
- **Playwright** at 360px and desktop:
  - each step renders;
  - the guard sends each setup state to the right step;
  - saving a client ID: the format error, the saved state, and the
    INVALID_CLIENT guidance;
  - each connect notice's fix, and `app_in_use_elsewhere` on save;
  - the "Allow your Spotify account" panel's copy and the confirm checkbox
    that gates Continue;
  - the copy button;
  - accessibility (headings, focus moves to the h1, alerts announced).
- **`npm test`** contract tests for the new endpoints.
- **The harness e2e first-run test** from 06 now drives the real setup
  pages.

## Verification Checklist
- [ ] `npx playwright test -c playwright.setup.config.ts` passes.
- [ ] The harness e2e first-run test passes through the setup pages.
- [ ] No setup page has an input for a client secret, and the only mention of
      the secret on any setup page is step 3's warning not to paste it
      (a Playwright assertion over the setup pages).
- [ ] Step 1 states the Premium requirement.
- [ ] On staging, the owner walks the setup pages against real Spotify and
      Google, and the copy follow-up PR's description lists every place the
      copy differs from Spotify's live dashboard, or says none was found. The
      from-scratch test of the guide happens at cutover (phase 09).

## What NOT To Do
- Don't hard-code `raccoon.fm` in the web app; the redirect URL comes from
  the API.
- Don't invent new design tokens; reuse the existing recipes.
- Don't add a "skip setup" path into the app.
- Don't add a "name your workspace" step or call a rename endpoint.
- Don't add a field for the client secret.

## Context
- Source skill: forge · Area: web/src/pages/setup, auth guard · Effort: M · Risk: Low · Priority: High
