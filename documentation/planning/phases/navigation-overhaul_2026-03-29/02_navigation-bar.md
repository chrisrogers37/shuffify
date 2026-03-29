# Phase 02: Navigation Bar

**Status**: IN PROGRESS
**Started**: 2026-03-29
**Depends on**: Phase 01

## Objective

Introduce a persistent top-level navigation bar across all authenticated pages. 6 items: Tiles, Workshop, Schedules, Activity, Settings, Logout. Remove old per-page nav buttons.

## Files

### New
- `shuffify/templates/partials/navbar.html`

### Modified
- `shuffify/templates/base.html`
- `shuffify/templates/dashboard.html`
- `shuffify/templates/workshop.html`
- `shuffify/templates/schedules.html`
- `shuffify/templates/settings.html`

## Changes

### 2a. Create navbar partial

**New file**: `shuffify/templates/partials/navbar.html`

Glass-morphism nav bar matching the design system. Sticky at top of page. Auto-detects active state from `request.endpoint` — no route changes needed.

```html
{# Navigation Bar — included on all authenticated pages #}
{% set endpoint_map = {
    'main.index': 'tiles',
    'main.workshop': 'workshop',
    'main.workshop_hub': 'workshop',
    'main.schedules': 'schedules',
    'main.activity': 'activity',
    'main.settings': 'settings',
} %}
{% set active_nav = endpoint_map.get(request.endpoint, '') %}

<nav class="sticky top-0 z-40 backdrop-blur-md bg-white/10 border-b border-white/20">
    <div class="max-w-6xl mx-auto px-4">
        <div class="flex items-center justify-between h-14">
            {# Logo/Brand #}
            <a href="{{ url_for('main.index') }}" class="text-white font-bold text-lg">
                Shuffify
            </a>

            {# Nav Items #}
            <div class="flex items-center gap-1">
                {# Tiles #}
                <a href="{{ url_for('main.index') }}"
                   class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150
                          {{ 'bg-white/20 text-white' if active_nav == 'tiles' else 'text-white/60 hover:text-white hover:bg-white/10' }}">
                    {# grid icon SVG #}
                    <span class="hidden sm:inline text-sm font-medium">Tiles</span>
                </a>

                {# Workshop — placeholder href until Phase 3 #}
                <a href="#" data-pending="workshop_hub"
                   class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150
                          {{ 'bg-white/20 text-white' if active_nav == 'workshop' else 'text-white/60 hover:text-white hover:bg-white/10' }}">
                    {# wrench icon SVG #}
                    <span class="hidden sm:inline text-sm font-medium">Workshop</span>
                </a>

                {# Schedules #}
                <a href="{{ url_for('main.schedules') }}"
                   class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150
                          {{ 'bg-white/20 text-white' if active_nav == 'schedules' else 'text-white/60 hover:text-white hover:bg-white/10' }}">
                    {# clock icon SVG #}
                    <span class="hidden sm:inline text-sm font-medium">Schedules</span>
                </a>

                {# Activity — placeholder href until Phase 4 #}
                <a href="#" data-pending="activity"
                   class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150
                          {{ 'bg-white/20 text-white' if active_nav == 'activity' else 'text-white/60 hover:text-white hover:bg-white/10' }}">
                    {# lightning icon SVG #}
                    <span class="hidden sm:inline text-sm font-medium">Activity</span>
                </a>

                {# Settings #}
                <a href="{{ url_for('main.settings') }}"
                   class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150
                          {{ 'bg-white/20 text-white' if active_nav == 'settings' else 'text-white/60 hover:text-white hover:bg-white/10' }}">
                    {# gear icon SVG #}
                    <span class="hidden sm:inline text-sm font-medium">Settings</span>
                </a>

                {# Logout — rightmost, subtle styling. Phase 5 moves this into settings sidebar. #}
                <a href="{{ url_for('main.logout') }}"
                   class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150 text-white/40 hover:text-red-300 hover:bg-white/10 ml-2"
                   onclick="return confirm('Are you sure you want to log out?')">
                    {# exit icon SVG #}
                    <span class="hidden sm:inline text-sm font-medium">Logout</span>
                </a>
            </div>
        </div>
    </div>
</nav>
```

Active state auto-detection: The `endpoint_map` dict maps `request.endpoint` to nav keys. No `active_nav` variable needed from routes. Non-existent endpoints (`workshop_hub`, `activity`) are in the map for when Phases 3-4 ship — they won't cause errors since `request.endpoint` simply won't match them until the routes exist.

### 2b. Include navbar in base.html

**File**: `shuffify/templates/base.html`

Include the navbar directly in base.html with a session check, above `{% block content %}`. No per-template block needed — DRY, one include for all authenticated pages:

```html
{% if session.get('access_token') %}
    {% include 'partials/navbar.html' %}
{% endif %}
{% block content %}{% endblock %}
```

This is better than a `{% block navbar %}` pattern because:
- DRY: one line vs. repeating the block override in every template
- Future-proof: new templates automatically get the navbar
- base.html already owns layout concerns

### 2c. Remove old navigation elements

**dashboard.html** (lines 60-106): Remove Schedules button (L60-67), Settings button (L69-77), and Logout button (L99-106). Keep Manage (L79-87) and Refresh (L89-97) as local action buttons.

**workshop.html** (lines 15-21): Remove the home button added in Phase 1. Navbar provides persistent navigation. Keep prev/next playlist arrows.

**schedules.html** (lines 16-22): Remove the back-to-dashboard chevron button.

**settings.html** (lines 15-21): Remove the back-to-dashboard chevron button.

## Verification

1. Nav bar visible on dashboard, workshop, schedules, settings
2. Active item highlighted correctly per page (auto-detected from endpoint)
3. Responsive: icons only on narrow screens, icon+text on wider
4. Old nav buttons gone — no duplicate navigation
5. Manage and Refresh still accessible on dashboard
6. Logout accessible from navbar on all pages (with confirmation)
7. Workshop and Activity links render as `#` (no crash from missing routes)
8. `flake8 shuffify/` and `pytest tests/ -v`

## CHANGELOG Entry

```markdown
### Added
- **Navigation Bar** - Persistent top-level navigation across all authenticated pages
  - 6 items: Tiles, Workshop, Schedules, Activity, Settings, Logout
  - Glass-morphism styling with active state auto-detection
  - Responsive: icon-only on mobile, icon+text on desktop

### Removed
- **Old Navigation Buttons** - Replaced per-page nav buttons with unified nav bar
  - Dashboard: removed Schedules, Settings, Logout buttons from header
  - Workshop: removed home button (navbar replaces it)
  - Schedules: removed back-to-dashboard button
  - Settings: removed back-to-dashboard button
```
