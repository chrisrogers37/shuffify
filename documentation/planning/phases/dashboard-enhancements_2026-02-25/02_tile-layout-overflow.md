# Phase 02: Fix Playlist Tile Layout Overflow

**Status:** ✅ COMPLETE
**Started:** 2026-02-25
**Completed:** 2026-02-25

## Header

| Field | Value |
|-------|-------|
| **PR Title** | Fix playlist tile info bar overflow hiding Workshop button |
| **Risk Level** | Low |
| **Estimated Effort** | Low (15 minutes) |
| **Files Modified** | 1 |
| **Files Created** | 0 |
| **Files Deleted** | 0 |

## Context

On the dashboard, each playlist tile has a green info bar containing the playlist name on the left and a Workshop button + Spotify icon on the right. The info bar uses `flex` with `justify-between` to space these two groups apart. However, when a playlist has a long name, the left div refuses to shrink below the intrinsic width of its text content. This is because flex children default to `min-width: auto` in CSS, which prevents them from shrinking below the size of their content. The `truncate` class on the `<h3>` (which applies `text-overflow: ellipsis; overflow: hidden; white-space: nowrap`) never activates because the parent `<div>` never gets narrower than the text. The result is that the Workshop button and Spotify icon on the right are pushed off the visible edge of the tile.

The fix is a single Tailwind utility class addition: `min-w-0` on the left flex child. This overrides the default `min-width: auto` to `min-width: 0`, allowing the flex child to shrink. Once it can shrink, `truncate` on the `<h3>` activates and the long name is ellipsized instead of overflowing.

## Dependencies

| Dependency | Direction | Reason |
|------------|-----------|--------|
| Phase 01 | None | Phase 01 touches different files (error_handlers.py, route files) |
| Phase 03 | **This phase must complete BEFORE Phase 03** | Phase 03 (shuffle UX redesign) will heavily rewrite the tile structure including the info bar. Phase 02's fix should be merged first so that Phase 03's redesigned layout incorporates the correct flex behavior from the start. |

## Detailed Implementation Plan

### Step 1: Add `min-w-0` to the left flex child

**File:** `shuffify/templates/dashboard.html`
**Line:** 270

**Before (line 270):**
```html
                        <div>
```

**After (line 270):**
```html
                        <div class="min-w-0">
```

**Why:** The `min-w-0` utility sets `min-width: 0` on this div. In a flex container, children have an implicit `min-width: auto` which prevents them from shrinking below the intrinsic width of their content. By setting `min-width: 0`, the flex algorithm is allowed to shrink this child below its content width, which in turn allows the `truncate` class on the `<h3>` inside it to activate and ellipsize long playlist names.

### Step 2: Add `flex-shrink-0` to the right flex child

**File:** `shuffify/templates/dashboard.html`
**Line:** 274

**Before (line 274):**
```html
                        <div class="flex items-center space-x-2 ml-2">
```

**After (line 274):**
```html
                        <div class="flex items-center space-x-2 ml-2 flex-shrink-0">
```

**Why:** This ensures the Workshop button and Spotify icon container never shrinks. While `flex-shrink: 1` (the default) combined with `min-w-0` on the left child would likely work correctly in practice (since the left child is the one that can now shrink), adding `flex-shrink-0` is a defensive measure. It explicitly guarantees the right child maintains its natural size regardless of how the flex algorithm distributes space. This is the correct semantic intent: the buttons should always be fully visible at their natural size.

### Summary of all changes

Only **2 lines** in **1 file** change:

| File | Line | Change |
|------|------|--------|
| `shuffify/templates/dashboard.html` | 270 | Add `class="min-w-0"` to opening `<div>` tag |
| `shuffify/templates/dashboard.html` | 274 | Add `flex-shrink-0` to existing class list |

No other files are modified. No imports change. No JavaScript changes. No CSS changes.

## Test Plan

### No new automated tests needed

This is a CSS-only fix (two Tailwind utility classes). The existing test at `tests/routes/test_core_routes.py:80` (`test_authenticated_shows_dashboard`) verifies the dashboard renders successfully with a 200 status code. It does not (and should not) assert on specific CSS classes, as that would make tests brittle.

### Manual verification steps

These are the critical manual checks:

1. **Start the development server:**
   ```bash
   python run.py
   ```

2. **Log in and navigate to the dashboard** at `http://localhost:8000/`

3. **Verify with short playlist names** (e.g., "Chill Vibes"):
   - The playlist name displays fully (no truncation)
   - The Workshop button and Spotify icon are fully visible and right-aligned
   - Layout looks identical to the current behavior

4. **Verify with long playlist names** (e.g., "This Is My Super Long Playlist Name That Should Definitely Get Truncated"):
   - The playlist name is truncated with an ellipsis (`...`)
   - The Workshop button is fully visible and clickable
   - The Spotify icon is fully visible and clickable
   - No horizontal overflow or scrollbar on the tile

5. **Verify the Workshop button still works:**
   - Click the Workshop button on a tile with a long name
   - Confirm it navigates to `/workshop/<playlist_id>`

6. **Verify the Spotify link still works:**
   - Click the Spotify icon on a tile with a long name
   - Confirm it opens Spotify in a new tab

7. **Verify the shuffle menu toggle still works:**
   - Click on the body of a tile (not on Workshop or Spotify)
   - Confirm the shuffle menu expands below the info bar
   - Click again to collapse
   - Confirm only one menu is open at a time

8. **Verify responsive behavior:**
   - Check at mobile width (1 column): tiles are full-width, truncation works
   - Check at tablet width (2 columns): tiles are narrower, truncation activates on medium-length names
   - Check at desktop width (3 columns): standard behavior

### Quick browser DevTools test

To test without needing a playlist with an actually long name, use the browser DevTools:

1. Right-click a playlist name `<h3>` element and select "Inspect"
2. Edit the text content to something very long (e.g., 100+ characters)
3. Verify the text truncates with ellipsis and the Workshop button remains visible

## Documentation Updates

### CHANGELOG.md

Add under `## [Unreleased]` in the `### Fixed` section:

```markdown
### Fixed
- **Playlist Tile Overflow** - Fixed Workshop button being pushed off-screen when playlist names are long
  - Added `min-w-0` to info bar left flex child to enable text truncation
  - Added `flex-shrink-0` to right flex child to protect button visibility
```

No other documentation updates needed. This is a visual bug fix with no API, configuration, or behavioral changes.

## Stress Testing & Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Playlist name is empty string (`""`) | Left div collapses to just the track count line. Right side stays in place. |
| Playlist name is 1 character | Name displays fully, no truncation |
| Playlist name is 200+ characters | Name truncates with ellipsis, Workshop button fully visible |
| Playlist name contains only emoji | Truncation works (emoji are wider per character, truncation activates sooner) |
| Playlist name contains RTL characters | `truncate` class handles this via `overflow: hidden` |
| Playlist with 0 tracks | Track count shows "0 tracks", layout unchanged |
| Browser zoom at 200% | Tiles are wider relative to viewport; truncation still works because `min-w-0` is zoom-independent |
| Very narrow viewport (320px) | Grid collapses to 1 column, tile is full-width. If name is still too long, truncation activates. |

## Verification Checklist

- [ ] `shuffify/templates/dashboard.html` line 270 has `class="min-w-0"` on the left div
- [ ] `shuffify/templates/dashboard.html` line 274 has `flex-shrink-0` added to the right div's class list
- [ ] No other lines in the file changed
- [ ] `flake8 shuffify/` passes (no Python files changed, but always verify)
- [ ] `pytest tests/ -v` passes (no test changes, but always verify)
- [ ] Manual: long playlist name truncates with ellipsis
- [ ] Manual: Workshop button is fully visible and clickable on all tile sizes
- [ ] Manual: Spotify icon is fully visible and clickable
- [ ] Manual: Shuffle menu toggle still works
- [ ] Manual: Layout looks correct on mobile, tablet, and desktop widths

## What NOT To Do

1. **Do NOT add `overflow-hidden` to the info bar container (`<div class="bg-spotify-green px-4 py-3 flex items-center justify-between">`).** This would clip the Workshop button rather than allowing the left child to shrink. The fix must be on the *child* level, not the parent.

2. **Do NOT add `max-w-*` or `w-*` classes to the left div.** Hardcoding a width would break responsiveness. The correct fix uses `min-w-0` which works at all widths.

3. **Do NOT add `truncate` to the `<p>` (track count) element.** The track count line ("123 tracks") is always short and does not need truncation. Adding it would be unnecessary and could hide useful information if someone has 1,000,000+ tracks.

4. **Do NOT restructure the tile layout.** This is a minimal, targeted fix. The tile structure will be redesigned in Phase 03. Any structural changes here would create merge conflicts with Phase 03.

5. **Do NOT modify the JavaScript click handlers.** The overflow bug is purely a CSS issue. The JS for shuffle menu toggling, Workshop link clicks, and Spotify link clicks all work correctly once the layout is fixed.

6. **Do NOT add `whitespace-nowrap` to the `<h3>`.** The `truncate` utility class already includes `white-space: nowrap`. Adding it redundantly would be harmless but misleading, suggesting the class was missing.
