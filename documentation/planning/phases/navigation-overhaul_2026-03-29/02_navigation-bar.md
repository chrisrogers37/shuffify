# Phase 02: Navigation Bar

**Status**: PENDING
**Depends on**: Phase 01

## Objective

Introduce a persistent top-level navigation bar across all authenticated pages. 5 sections: Tiles, Workshop, Schedules, Activity, Settings. Remove old per-page nav buttons.

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

Glass-morphism nav bar matching the design system. Fixed/sticky at top of page.

```html
{# Navigation Bar — included on all authenticated pages #}
<nav class="sticky top-0 z-40 backdrop-blur-md bg-white/10 border-b border-white/20">
    <div class="max-w-6xl mx-auto px-4">
        <div class="flex items-center justify-between h-14">
            {# Logo/Brand #}
            <a href="{{ url_for('main.index') }}" class="text-white font-bold text-lg">
                Shuffify
            </a>

            {# Nav Items #}
            <div class="flex items-center gap-1">
                {% set nav_items = [
                    ('tiles', 'main.index', 'Tiles', 'grid-icon-svg'),
                    ('workshop', 'main.workshop_hub', 'Workshop', 'wrench-icon-svg'),
                    ('schedules', 'main.schedules', 'Schedules', 'clock-icon-svg'),
                    ('activity', 'main.activity', 'Activity', 'lightning-icon-svg'),
                    ('settings', 'main.settings', 'Settings', 'gear-icon-svg'),
                ] %}

                {% for key, endpoint, label, icon in nav_items %}
                <a href="{{ url_for(endpoint) }}"
                   class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150
                          {{ 'bg-white/20 text-white' if active_nav == key else 'text-white/60 hover:text-white hover:bg-white/10' }}">
                    {# icon SVG here #}
                    <span class="hidden sm:inline text-sm font-medium">{{ label }}</span>
                </a>
                {% endfor %}
            </div>
        </div>
    </div>
</nav>
```

Active state detection: Each page template sets `active_nav` variable. Or auto-detect via `request.endpoint`:
- `main.index` → tiles
- `main.workshop`, `main.workshop_hub` → workshop
- `main.schedules` → schedules
- `main.activity` → activity
- `main.settings` → settings

Note: `workshop_hub` and `activity` routes don't exist yet. Use `#` as placeholder href until Phases 3-4 ship. Or define stub routes that redirect.

### 2b. Add navbar block to base.html

**File**: `shuffify/templates/base.html`

Add a block above `{% block content %}`:

```html
{% block navbar %}{% endblock %}
{% block content %}{% endblock %}
```

### 2c. Include navbar in authenticated pages

Each authenticated template adds:

```html
{% block navbar %}
    {% include 'partials/navbar.html' %}
{% endblock %}
```

And sets `active_nav` in route context, or relies on auto-detection.

### 2d. Remove old navigation elements

**dashboard.html**: Remove the nav buttons from the welcome card header (Schedules, Settings, Logout buttons). Keep Manage + Refresh as local action buttons within the tiles view area (not in the nav).

**workshop.html**: Remove the home button added in Phase 1 (navbar now provides persistent navigation back to Tiles). Keep prev/next playlist arrows.

**schedules.html**: Remove the back-to-dashboard button from the header.

**settings.html**: Remove the back-to-dashboard button from the header.

## Verification

1. Nav bar visible on dashboard, workshop, schedules, settings
2. Active item highlighted correctly per page
3. Responsive: icons only on narrow screens, icon+text on wider
4. Old nav buttons gone — no duplicate navigation
5. Manage and Refresh still accessible on dashboard
6. `flake8 shuffify/` and `pytest tests/ -v`

## CHANGELOG Entry

```markdown
### Added
- **Navigation Bar** - Persistent top-level navigation across all authenticated pages
  - 5 sections: Tiles, Workshop, Schedules, Activity, Settings
  - Glass-morphism styling with active state highlighting
  - Responsive: icon-only on mobile, icon+text on desktop

### Removed
- **Old Navigation Buttons** - Replaced per-page nav buttons with unified nav bar
```
