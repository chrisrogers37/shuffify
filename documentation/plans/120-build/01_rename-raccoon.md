---
title: "[plan] Rename what users see to Raccoon — web, legal, backend strings"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, rename]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: S. Closes the
rename part of #122.

Every name a person sees becomes Raccoon. This PR doesn't touch the domain:
moving to raccoon.fm is configuration work in phase 01b. The internal code
name stays `shuffify`.

## Evidence

**User-facing strings in the web app**
- `web/src/shell/Shell.tsx`:
  - L14: `aria-label`;
  - L22 and L51: the wordmark;
  - L75-76: the footer;
  - L105: the `X · Shuffify` title template.
- `web/src/pages/public/meta.ts:3-15`: page titles and descriptions. There
  are no OG tags.
- `web/src/pages/errors/ErrorPage.tsx:5, 8, 21`
- `web/src/pages/workshop/tools/SnapshotsTool.tsx:47, 83`
- Page titles: `web/src/pages/routines/RoutineSetupPage.tsx:158-159`,
  `RoutinesPage.tsx:345`, `PlaylistSetupPage.tsx:179`.
- `web/src/pages/library/model.ts:251` and `LibraryPage.tsx:236`
- `web/src/pages/public/LandingPage.tsx:79` and `LegalPage.tsx:16`
- `web/index.html:7`
- `web/src/app/FixtureApp.tsx:338` (the fixture build only)

**Legal copy**
- The source is `assets/legal/terms.html` and `assets/legal/privacy.html`.
  It's generated into `web/src/pages/public/generated/legal.ts` by
  `web/scripts/generate-public-sources.mjs`.
- Both files are pinned by sha256 in `tests/test_legal_copy_pin.py:12-15`.

**Backend strings people see**
- `shuffify/error_handlers.py:356`
- `shuffify/services/auth_notice.py:9`
- `shuffify/services/live_executor_context_service.py:116`
- `shuffify/services/playlist_setup_service.py:129-130`: playlist
  descriptions, which are visible inside Spotify. They're asserted in
  `tests/services/operations/test_asset_capture.py:27-28`.

**Tests that assert the name**
- `web/tests/public-browser/public.spec.ts`
- `web/tests/static/public-html.test.mjs:18-20`
- `web/tests/library-model.test.mjs:181`
- `web/tests/library-browser/library.spec.ts:145`
- `web/tests/activity-browser/activity.spec.ts:46`
- `web/tests/settings-browser/settings.spec.ts:78`
- `web/tests/browser/foundation.spec.ts:703`
- `web/tests/public-sources.test.mjs:108`

**Docs:** `README.md`, `web/README.md`,
`documentation/design/after-hours/DESIGN.md:313-317`.

## Implementation Plan

### Dependencies
None.

### Blocks
01b (move the domain), 09 (cutover).

### Steps
1. In each web file listed above, replace the visible "Shuffify" and
   "shuffify" with "Raccoon".
   - The wordmark is lowercase today; keep the casing the design uses.
   - Leave internal identifiers alone: `shuffify:react-routine-continuation:v1`,
     `shuffify.invalid`, `shuffify-web`, `SHUFFIFY_SOURCE_ROOT`.
2. Update the legal copy in `assets/legal/*.html` (name only; the sign-in
   wording changes in phase 05). Regenerate `generated/legal.ts` and update
   the two sha256 pins.
3. Update the backend strings. Playlist descriptions become "…created in
   Raccoon setup", and the test assertion changes to match.
4. Update the tests that assert the name.
5. In the README, web README and DESIGN.md, change the product's name.
   Domain links stay as they are until phase 01b.
6. Keep the favicon and the shuffle-mark artwork; a Raccoon mark is a
   separate design task.

## Test Plan
- `npm test`, `npm run build` and `npm run test:static` in `web/`.
- The backend suite, including `tests/test_legal_copy_pin.py` and
  `test_asset_capture.py`.
- The CI harness job.

## Verification Checklist
- [ ] `git grep -in "shuffify" -- web/src web/index.html assets/legal` lists
      only the internal identifiers named in step 1.
- [ ] CI is green.
- [ ] The production site's title and header read Raccoon after deploy.

## What NOT To Do
- Don't rename the Python package, cookie, cache prefixes, Railway or
  Vercel project names, or the repository.
- Don't change domains or redirect URLs here.
- Don't rewrite the legal copy beyond the name.

## Context
- Source skill: forge · Area: web, assets/legal · Effort: S · Risk: Low · Priority: Medium
