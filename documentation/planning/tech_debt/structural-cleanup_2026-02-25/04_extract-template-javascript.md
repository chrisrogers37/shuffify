# Phase 04: Extract Template JavaScript to Static Files

`📋 PENDING`

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `refactor: Extract inline JavaScript from templates to static JS files` |
| **Risk Level** | Low |
| **Estimated Effort** | High (6-8 hours) |
| **Dependencies** | None |
| **Blocks** | Nothing |

### Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/templates/base.html` | Add `{% block scripts %}`, move shared JS to `static/js/base.js` |
| `shuffify/templates/dashboard.html` | Remove `<script>` blocks, add config data attributes |
| `shuffify/templates/workshop.html` | Remove `<script>` blocks, add config data attributes |
| `shuffify/templates/index.html` | Remove `<script>` blocks, add config data attributes |
| `shuffify/static/js/base.js` | NEW — shared JS (flash messages, intersection observer) |
| `shuffify/static/js/dashboard.js` | NEW — dashboard manage mode, drag-and-drop, shuffle overlay |
| `shuffify/static/js/workshop.js` | NEW — workshop UI interactions |
| `shuffify/static/js/landing.js` | NEW — landing page animations |

---

## Problem

JavaScript is embedded inline in Jinja2 templates:

| Template | Total Lines | Estimated JS Lines |
|----------|------------|-------------------|
| `workshop.html` | 3,102 | ~1,500+ |
| `dashboard.html` | 1,261 | ~400+ |
| `index.html` | 878 | ~200+ |
| `base.html` | 262 | ~80 |

Only 24 lines of external JS exist (`static/js/notifications.js`). All other JS is embedded, making it:
- **Untestable** — no way to lint or unit test inline JS
- **Uncacheable** — browser re-downloads JS with every page load
- **Unmaintainable** — JS logic interleaved with Jinja2 markup

### Current `base.html` structure (lines 177-261)

`base.html` has no `{% block scripts %}`. It loads `notifications.js` and defines inline functions: `showFlashMessage()`, `handlePlaylistAction()`, and an IntersectionObserver setup.

---

## Approach: Config Object Pattern

For each template, use a small inline `<script>` block that creates a config object with server-side data, followed by an external JS file that reads from it:

```html
<!-- Minimal inline: just data, no logic -->
<script>
    window.SHUFFIFY = {
        urls: {
            shuffleUrl: "{{ url_for('main.shuffle') }}",
            undoUrl: "{{ url_for('main.undo') }}",
        },
        playlists: {{ playlists|tojson }},
        algorithms: {{ algorithms|tojson }},
    };
</script>
<script src="{{ url_for('static', filename='js/dashboard.js') }}"></script>
```

This keeps Jinja2 data injection minimal and all logic in cacheable external files.

---

## Step-by-Step Implementation

### Step 1: Add `{% block scripts %}` to `base.html`

Add before `</body>`:

```html
    <!-- Shared JavaScript -->
    <script src="{{ url_for('static', filename='js/notifications.js') }}"></script>
    <script src="{{ url_for('static', filename='js/base.js') }}"></script>

    {% block scripts %}{% endblock %}
</body>
```

Remove the existing inline `<script>` block (lines 181-260).

### Step 2: Create `static/js/base.js`

Move the shared functions from `base.html`:
- `showFlashMessage(message, category)`
- `handlePlaylistAction(form, action)`
- IntersectionObserver setup for `.animate-on-scroll`

These functions are used across multiple pages.

### Step 3: Extract `dashboard.html` JavaScript

**Identify all `<script>` blocks:**
- Manage mode toggle, toolbar controls
- Drag-and-drop initialization
- Pin/hide/unhide/reset AJAX handlers
- Shuffle overlay interactions
- `saveCurrentOrder()`, `initDragAndDrop()`

**Create `static/js/dashboard.js`:**
- Read config from `window.SHUFFIFY`
- All functions moved from inline scripts
- Initialize on `DOMContentLoaded`

**Add config block to `dashboard.html`:**
```html
{% block scripts %}
<script>
    window.SHUFFIFY = {
        urls: {
            saveOrder: "{{ url_for('main.save_playlist_order') }}",
            toggleHidden: "/api/playlist-preferences/{id}/toggle-hidden",
            togglePinned: "/api/playlist-preferences/{id}/toggle-pinned",
            resetPrefs: "{{ url_for('main.reset_playlist_preferences') }}",
        },
    };
</script>
<script src="{{ url_for('static', filename='js/dashboard.js') }}"></script>
{% endblock %}
```

### Step 4: Extract `index.html` JavaScript

**Identify all `<script>` blocks:**
- Scroll-reveal animations
- Hero section particle effects or animations
- CTA interactions

**Create `static/js/landing.js`** with all landing page JS.

### Step 5: Extract `workshop.html` JavaScript

This is the largest extraction (~1,500+ lines of JS). **Do this last** as it's the most complex.

**Identify all `<script>` blocks:**
- Session management (save, load, delete)
- Track search and add
- External playlist import
- Merge/commit workflow
- Drag-and-drop track reordering
- Sidebar panel interactions
- Snapshot browser
- Raid functionality

**Create `static/js/workshop.js`:**
- All workshop interactions
- Config object for URLs and initial data

**Add config block to `workshop.html`** with session data, URLs, and initial state.

### Step 6: Verify each template incrementally

After extracting each template's JS, manually test the page before moving to the next. This prevents cascading issues.

---

## Verification Checklist

```bash
# 1. Lint JS files (if eslint available)
# npx eslint static/js/*.js

# 2. Python lint (template changes only)
./venv/bin/python -m flake8 shuffify/

# 3. Full test suite
./venv/bin/python -m pytest tests/ -v

# 4. Manual browser testing (CRITICAL for this phase)
# - Landing page: all animations work, scroll reveals trigger
# - Dashboard: manage mode toggles, drag-and-drop reorders, pin/hide/unhide work
# - Dashboard: shuffle overlay appears on hover, all shuffle types work
# - Workshop: all 8+ interaction areas function correctly
# - Check browser console for JS errors on each page

# 5. Verify caching works
# - Load dashboard twice, check Network tab shows JS served from cache
# - Hard refresh clears cache and re-downloads
```

---

## What NOT To Do

1. **Do NOT use a JS bundler (webpack, vite, etc.).** This project uses vanilla JS with Tailwind CDN. Keep it simple — plain `.js` files served from `static/js/`.

2. **Do NOT introduce ES modules (`import`/`export`).** The project targets broad browser compatibility. Use the global `window.SHUFFIFY` config pattern and IIFEs if namespace isolation is needed.

3. **Do NOT change any JS logic during extraction.** Copy-paste the JS code exactly, only changing Jinja2 template expressions to `window.SHUFFIFY.*` references. This is a structural refactor, not a rewrite.

4. **Do NOT extract the Tailwind config from `base.html`.** The `tailwind.config` script block in `<head>` must stay inline because Tailwind CDN reads it synchronously on page load.

5. **Do NOT try to extract workshop.html JS in one pass.** Extract it function-by-function or section-by-section, testing after each extraction.

6. **Do NOT remove the `notifications.js` file.** It already works as an external file. Keep it separate from `base.js`.

7. **Do NOT put the config `<script>` blocks in `<head>`.** They must come before the external JS file in `{% block scripts %}` at the bottom of `<body>`, so the DOM is available.

8. **Do NOT use `data-*` attributes for large data sets** (like playlist arrays). Use the `window.SHUFFIFY` config object for structured data. Reserve `data-*` for per-element metadata (e.g., `data-playlist-id` on a tile).
