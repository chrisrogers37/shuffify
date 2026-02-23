# Phase 05: Template Decomposition & Jinja2 Macros
**Status:** ✅ COMPLETE
**Started:** 2026-02-23
**Completed:** 2026-02-23
**PR:** #96

## Header

| Field | Value |
|-------|-------|
| **PR Title** | Refactor: Extract reusable Jinja2 macros and shared notification JS from templates |
| **Risk Level** | Low |
| **Effort** | Medium (2-3 hours) |
| **Files Modified** | 4 |
| **Files Created** | 5 |
| **Files Deleted** | 0 |
| **Dependencies** | None |
| **Blocks** | Future template decomposition phases |

### Files Created

| File | Purpose |
|------|---------|
| `shuffify/templates/macros/cards.html` | `glass_card` macro for reusable card containers |
| `shuffify/templates/macros/forms.html` | `select_field` and `toggle_field` macros for form inputs |
| `shuffify/templates/macros/states.html` | `state_loading` and `state_empty` macros for UI states |
| `shuffify/static/js/notifications.js` | Shared `showNotification()` function extracted from 3 templates |

### Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/templates/base.html` | Add `<script>` tag for shared notifications.js |
| `shuffify/templates/settings.html` | Replace inline patterns with macros, remove inline `showNotification()` |
| `shuffify/templates/dashboard.html` | Replace welcome card with macro, remove inline `showNotification()` |
| `shuffify/templates/schedules.html` | Replace header card and empty state with macros, remove inline `showNotification()` |

## Scope

Extract the most commonly repeated template patterns into reusable Jinja2 macros and consolidate duplicated JavaScript. This is Phase 1 of a larger template decomposition effort.

### What IS in scope
- `glass_card` macro (cards.html)
- `select_field` and `toggle_field` macros (forms.html)
- `state_loading` and `state_empty` macros (states.html)
- Shared `showNotification()` JS function
- Apply macros to `settings.html`, `dashboard.html`, `schedules.html`

### What is NOT in scope
- `workshop.html` changes (needs its own dedicated phase)
- `index.html` changes (landing page is off-limits)
- Button macros, modal macros, component includes
- CSS extraction
- Workshop JS extraction
