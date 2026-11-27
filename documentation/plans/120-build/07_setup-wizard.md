---
title: "[plan] Guided setup in the web app (web/src)"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-26
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
    `GET /spotify/connect` start, and the connect notice table (phase 06
    step 4): every code that can come back after `/spotify/callback`, and
    `app_in_use_elsewhere`, which the save returns;
  - from 08: `/api/session`'s broken state and the `spotify_app_broken`
    refusal.

  There is no workspace-rename endpoint to depend on.
- **What the shell and the notices do today:**
  - `AppShell` always renders the main navigation
    (`web/src/shell/Shell.tsx:5-11, 139-153`), every app route is `AppShell`
    then `AuthGuard` (for example `web/src/app/library-routes.tsx:7-13`), and
    the only logout form is on the Settings page
    (`web/src/pages/settings/SettingsPage.tsx:463-471`);
  - `RouteFocus` moves focus to the h1 when the path changes or a different
    h1 element appears (`Shell.tsx:90-138`);
  - a notice reaches the page only as a toast that `SessionProvider` takes
    from the session once (`web/src/auth/SessionProvider.tsx:30-50`,
    `session-store.ts:64-67`). It is `{code, category, message}` with no link
    (`web/src/api/contracts.ts:48-53`), and a reload loses it.

## Implementation Plan

### Dependencies
05 (session shape and `redirect_url`), 06 (endpoints) and 08
(`/api/session`'s broken state and the `spotify_app_broken` refusal; nothing
sets `broken` before 08 lands, so the redirect is harmless until then). It
can build against fixtures in parallel with 06 and 08, which supply the
endpoints and the broken state before this merges.

### Blocks
09.

### Steps

1. **Routing: one table, and the guard is built from it.** A signed-in
   person whose workspace isn't fully set up lands on setup, not the app, and
   a broken workspace can still reach the steps that fix it.

   | State | App routes | `/setup` | `/setup/problem` |
   |---|---|---|---|
   | Signed out | sign-in panel | sign-in panel | sign-in panel |
   | No app | `/setup/1` | open | `/setup` |
   | Unchecked, never connected | `/setup/4` | steps 1 to 3 open | `/setup` |
   | Unchecked, bound account (a replacement in progress) | `/setup/4` | open, with the paused banner on every step | `/setup` |
   | Broken | `/setup/problem` | steps 1 to 4 open; `/setup` itself resumes at `/setup/problem` | open |
   | Ok | open | the app | the app |

   - **`/setup` stays reachable while the app is broken.** The problem
     page's "Start setup again" and "Use a different app" go to step 1, and
     the status stays `broken` until a new client ID is saved on step 3
     (phase 08). A guard that sent a broken workspace to `/setup/problem`
     from every route would bounce the person back before they reached
     step 3.
   - **Each step has its own URL,** `/setup/1` to `/setup/4`. `RouteFocus`
     moves focus to the h1 when the path changes (Evidence), and Back moves
     between steps instead of leaving setup.
   - **Every redirect uses `replace`,** so Back never bounces between a page
     and its redirect.
   - `/spotify/callback` returns to `/` on success, and to `/setup/4` or
     `/setup/problem` with a notice (phase 06 step 4).
2. **A setup shell, not `AppShell`.** `/setup/*` and `/setup/problem` render
   in a shell with no app navigation (under `AppShell` the five navigation
   links would each bounce back to setup): the brand, the signed-in person's
   name from `/api/session`'s `user`, so a person can see whose workspace
   this is, and Log out. Log out is the same `POST /logout` form the
   Settings page has, which also clears the routine continuation
   (`web/src/navigation/NavigationGuard.tsx:122-132`).
3. **The step pages** (`web/src/pages/setup/`). The step bar is
   `sh-routine-progress` with `aria-current="step"`, four steps.
   1. **Create a Spotify app.** Says first that Spotify requires a Premium
      account to own an app, and that Spotify allows one development app
      per developer. It offers two paths:
      - *Create a new app.* Numbered instructions, plus a button that opens
        `https://developer.spotify.com/dashboard` in a new tab, with a label
        that says so. It suggests the name "Raccoon", with a copy button, and
        a one-line description: the name is what Spotify's consent dialog
        shows at every connect and what the Spotify account's Apps page
        lists. An old app the person no longer uses has to be deleted in the
        dashboard before a new one can be made.
      - *"I already have a Spotify app."* For a developer whose one app
        serves something else. It goes to step 2 (add the redirect URL and
        the account to that app) and then step 3. An app takes several
        redirect URIs, and its client ID only has to be unused by other
        workspaces, so nothing has to be deleted.
   2. **Redirect URL and allow-list,** two panels from the mockup and one
      confirm checkbox for both.
      - *Add the redirect URL.* A read-only field with the site's redirect
        URL from `/api/session` (not hard-coded) and a copy button. The whole
        URL is visible at every width: it is the value the person checks was
        saved exactly, and the mockup's single row (the URL plus Copy) is
        wider than a 360px screen, so the URL wraps and Copy sits below it.
        Copy announces "Copied" through the existing announcer
        (`web/src/a11y/Announcer`, as `Toast` does). The panel says that if
        the URL is wrong or unsaved, Spotify stops on its own page, which
        reads "INVALID_CLIENT: Invalid redirect URI", and never returns to
        Raccoon, and to come back here and check it was saved exactly.
      - *Allow your Spotify account.* "Under **User Management**, add the
        name and email of the Spotify account you'll connect." It links to
        Spotify's account overview, which shows the account's email: people
        who joined Spotify through Facebook, Apple or Google often don't
        know it. It says an app allows five accounts and that the account
        added must be the one connected in step 4, because the workspace is
        bound to the first account it connects.
      - "I've done both" enables Continue.
   3. **Paste the client ID.** `Field` and `Input` for the ID, checked for
      32 hexadecimal characters on paste, on blur and on submit (not on each
      keystroke, which would have a screen reader announce an error
      mid-paste), and by `PUT /api/workspace/spotify-app` on save.
      - It says where the ID is: shown in full on the app's Settings page.
        It says plainly: only the client ID, never the secret, and don't
        open "View client secret".
      - The format error says what a client ID looks like. It doesn't say or
        imply that a pasted secret would be caught: the format can't tell
        the two apart (phase 06 step 3).
      - It says that a wrong ID takes you to a Spotify page reading
        "INVALID_CLIENT: Invalid client", that you'll never come back to
        Raccoon from it, and that you should return here and fix the ID.
   4. **Connect Spotify.** A same-tab form `GET` to `/spotify/connect`, with
      a link back to step 3 to change the ID.
      - **Under Connect, both of Spotify's dead-end pages are quoted in
        full, each with its fix.** Both begin "INVALID_CLIENT", the person
        reaches them from this step, and Back returns here:
        - "INVALID_CLIENT: Invalid redirect URI": go to step 2, paste the
          URL exactly and save it;
        - "INVALID_CLIENT: Invalid client": go to step 3 and check the
          client ID.

        It says how to come back: the browser's Back button. The quoted
        texts are the fake Spotify's until staging has recorded the live
        wording (phase 06's staging checks).
      - **Connect notices render inline here** (and on `/setup/problem`),
        from the notice's code, each with its fix and a link to the step it
        names, and they are still there after a reload. A notice today is a
        toast shown once, with no link (Evidence), which can't carry a fix.
      - The notices are phase 06 step 4's connect table. Their fixes:
        - `access_denied`: "You cancelled before Spotify finished
          connecting." Press Connect again; nothing has changed.
        - `invalid_state` and `missing_code`: the attempt expired or didn't
          finish. Press Connect again.
        - `spotify_user_not_allowed`: "This Spotify account isn't allowed on
          your app." Links to step 2's allow-list panel.
        - `spotify_app_rejected`: "Spotify didn't recognise this app." The
          client ID is wrong or the app was deleted. Links to step 3 to check
          the ID, and to step 1 if the app no longer exists.
        - `oauth_failed`: "Spotify didn't finish connecting. The sign-in
          expired or was already used." Connect Spotify again; nothing else
          changes and the app isn't marked broken.
        - `spotify_account_in_use_elsewhere`: "This Spotify account is already
          connected to another workspace. Have you used Raccoon with another
          Google account? Log out and sign in with that one." Log out is in
          the setup shell; otherwise use a different account.
        - `wrong_spotify_account`: "This isn't the Spotify account this
          workspace started with." Connect the original account, which the
          page names by its `spotify_display_name`. It also says that
          switching to a different account isn't available yet.
        - `app_in_use_elsewhere`, which comes back from saving the client ID
          in step 3 rather than from connecting: "This app is already used by
          another workspace. Each Spotify app can belong to only one
          workspace, so create a new app (step 1) and paste its client ID."
          Links to step 1.
4. **Resume where they left off.** `/setup` resolves to the current step by
   the routing table: no app saved goes to `/setup/1`; an app saved
   (`unchecked`) goes to `/setup/4`; a broken app goes to `/setup/problem`;
   connected goes to the app. Steps 1 and 2 are instructions, so going back
   and forth between them is free.
5. **Copy follows the mockups.** Screenshots of Spotify's dashboard are
   placeholders until they've been checked against the live dashboard
   (design: left to the builder). The staging walk records, for the copy
   follow-up PR:
   - the fields of Spotify's Create app form (if it asks for redirect URIs
     at creation, step 2's URL moves into step 1);
   - what the allow-list section is called now ("User Management");
   - whether an app's owner must add their own account (if not, step 2's
     second panel is optional for most people);
   - what an account that isn't on the allow-list sees: Spotify's own page,
     or the "not registered" profile error the plan expects;
   - the live wording of the two "INVALID_CLIENT" pages, and the client
     secret's shape.
6. **Add fixtures** to `web/src/fixtures/transport.ts` for each row of the
   routing table and each notice, plus a Playwright suite
   `playwright.setup.config.ts`.
7. **Make the "uh oh" page, `/setup/problem`,** from the design's mockup
   (`documentation/plans/120-mockups/uh-oh.html`), in the setup shell
   (step 2) and reached by the routing table (step 1):
   - the paused-schedules banner: "N schedules are paused", where N is the
     count of the workspace's enabled schedules (all of them are paused). It
     counts schedules, so it says schedules, not "scheduled runs", and it is
     hidden when there are none;
   - it says "Nothing will run on your playlists until this is fixed". It
     doesn't say that nothing has been changed on them: the run in flight
     when the failure hit ends `failed` and may have applied part of its
     change first. After the fix, Activity shows any failed run and its
     snapshot;
   - the fix for `broken_reason`, from phase 08's status and action matrix,
     with the screens for each. Where it names the bound Spotify account it
     uses `spotify_display_name`, and the access-removed copy says "the app
     you created for Raccoon";
   - connect notices render inline here, as on step 4;
   - "Use a different app" always offered. It says to delete the old app in
     Spotify's dashboard first (one app per developer), then goes to step 1.
     "Start setup again" goes there too. Both work because `/setup` stays
     reachable while the app is broken (the routing table).

   While a replacement is in progress (`unchecked` with a bound Spotify
   account), `/setup` shows the same paused-schedules banner.

## Test Plan
- **Playwright** at 360px and desktop:
  - each step renders, at its own URL;
  - **one test per row of the routing table,** and the round trip for each
    broken reason: from `/setup/problem`, "Start setup again" reaches step 3
    and saves;
  - the "uh oh" page for each reason and its fix, the banner's wording, and
    no banner when there are no enabled schedules;
  - **a break seen mid-session:** with a page open and the connection then
    marked broken, the page's next request gets the 401 and the page lands
    on `/setup/problem`, with no sign-in panel and no retry loop;
  - the shell shows the signed-in person's name and no app navigation, and
    Log out works from each step and from `/setup/problem`;
  - saving a client ID: the format error (on paste, blur and submit), the
    saved state, and the INVALID_CLIENT guidance;
  - step 4 carries both dead-end texts and their fixes;
  - each connect notice renders inline with its fix and is still there after
    a reload, and `app_in_use_elsewhere` on save;
  - the "Allow your Spotify account" panel's copy and the confirm checkbox
    that gates Continue;
  - the copy button and its "Copied" announcement, and at 360px the whole
    redirect URL is visible (asserted: "renders" doesn't catch clipping);
  - step 1's suggested name and its "I already have a Spotify app" path;
  - accessibility (headings, focus moves to the h1 on each step, alerts
    announced).
- **`npm test`** contract tests for the new endpoints.
- **The harness e2e first-run test** from 06 now drives the real setup
  pages, and a harness e2e reaches each of the fake Spotify's two error
  pages from step 4 and returns with Back.

## Verification Checklist
- [ ] `npx playwright test -c playwright.setup.config.ts` passes.
- [ ] The harness e2e first-run test passes through the setup pages.
- [ ] No setup page has an input for a client secret, and the only mention of
      the secret on any setup page is step 3's warning not to paste it
      (a Playwright assertion over the setup pages).
- [ ] Step 1 states the Premium requirement.
- [ ] A broken workspace reaches step 3 from `/setup/problem` and saves a new
      client ID (the routing table's round trip).
- [ ] Log out is reachable from every setup page.
- [ ] On staging, the owner walks the setup pages against real Spotify and
      Google, and the copy follow-up PR's description lists every place the
      copy differs from Spotify's live dashboard, or says none was found,
      with step 5's list answered. Setup step 1's run from scratch, on
      staging with the owner's second Spotify account, gates the cutover
      only (phase 09 step 1), not this phase; the timed run of the whole
      guide is at cutover (phase 09 step 12).

## What NOT To Do
- Don't hard-code `raccoon.fm` in the web app; the redirect URL comes from
  the API.
- Don't invent new design tokens; reuse the existing recipes.
- Don't add a "skip setup" path into the app.
- Don't add a "name your workspace" step or call a rename endpoint.
- Don't add a field for the client secret.
- Don't put the setup pages under `AppShell`, and don't send a broken
  workspace away from `/setup`.
- Don't say or imply that a pasted client secret would be detected.

## Context
- Source skill: forge · Area: web/src/pages/setup, auth guard · Effort: M · Risk: Low · Priority: High
