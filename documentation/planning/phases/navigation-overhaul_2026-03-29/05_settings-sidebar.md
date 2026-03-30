# Phase 05: Settings Sidebar

**Status**: IN PROGRESS
**Started**: 2026-03-30
**Depends on**: Phase 02
**Parallel with**: Phases 03 and 04

## Objective

Convert the Settings page into a slide-out sidebar accessible from the nav bar. Move Logout into the sidebar. Port the workshop sidebar pattern for consistency.

## Files

### New
- `shuffify/templates/partials/settings_sidebar.html`

### Modified
- `shuffify/templates/base.html` (or navbar partial)
- `shuffify/templates/partials/navbar.html`
- `shuffify/routes/settings.py`

## Changes

### 5a. Settings sidebar partial

**New file**: `shuffify/templates/partials/settings_sidebar.html`

Port the open/close/backdrop mechanics from the workshop sidebar (not the full `workshopSidebar` object — that has tabs and localStorage which aren't needed). The sidebar:
- Slides in from the right edge
- Glass-morphism styling: `bg-black/70 backdrop-blur-xl border-l border-white/20`
- Contains the settings form fields currently in `settings.html`:
  - Default algorithm selector
  - Theme selection
  - Auto-snapshot toggle
  - Max snapshots per playlist
  - Show recent activity toggle
- **Logout button** at the bottom with confirmation dialog (moved from navbar — Phase 02 added it there as interim location)
- Close button (X) at top-right
- Backdrop overlay to close on click-outside

```html
{# Settings Sidebar #}
<div id="settings-sidebar-backdrop" class="fixed inset-0 bg-black/40 z-40 hidden" onclick="closeSettingsSidebar()"></div>
<div id="settings-sidebar" class="fixed top-0 right-0 h-full w-80 bg-black/70 backdrop-blur-xl border-l border-white/20 z-50 transform translate-x-full transition-transform duration-200 ease-out">
    <div class="flex flex-col h-full">
        {# Header #}
        <div class="flex items-center justify-between p-4 border-b border-white/10">
            <h2 class="text-white font-bold text-lg">Settings</h2>
            <button onclick="closeSettingsSidebar()" class="p-1 rounded-lg hover:bg-white/10 text-white/60 hover:text-white transition">
                {# X icon #}
            </button>
        </div>

        {# Settings Form — fields populated by JS via AJAX on open #}
        <div class="flex-1 overflow-y-auto p-4 space-y-6">
            {# Static HTML form fields: default_algorithm (select), theme (select),
               auto_snapshot_enabled (toggle), max_snapshots_per_playlist (number),
               dashboard_show_recent_activity (toggle), notifications_enabled (toggle).
               Values populated by populateSettingsForm() after fetch. #}
        </div>

        {# Footer: Save + Logout #}
        <div class="p-4 border-t border-white/10 space-y-3">
            <button onclick="saveSettings()" class="w-full px-4 py-2 bg-white text-spotify-dark font-bold rounded-lg hover:bg-green-100 transition">
                Save Settings
            </button>
            <a href="{{ url_for('main.logout') }}" onclick="return confirm('Are you sure you want to log out?')"
               class="block w-full px-4 py-2 text-center text-red-300 hover:text-red-200 hover:bg-white/5 rounded-lg transition text-sm">
                Log Out
            </a>
        </div>
    </div>
</div>
```

### 5b. Remove Logout from navbar

**File**: `shuffify/templates/partials/navbar.html`

Remove the Logout nav item added in Phase 02 (it now lives in the settings sidebar footer). The navbar should have 5 items after this: Tiles, Workshop, Schedules, Activity, Settings.

### 5c. AJAX settings load (GET route JSON support)

**File**: `shuffify/routes/settings.py`

The POST handler already has full AJAX/JSON support (lines 182-195). No changes needed there.

Add JSON response support to the existing **GET** handler so the sidebar can fetch settings data on open. Check for `X-Requested-With: XMLHttpRequest` header:

```python
@main.route("/settings")
def settings():
    # ... existing auth and data loading ...

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if is_ajax:
        return jsonify({
            "settings": user_settings.to_dict(),
            "algorithm_options": algorithm_options,
        })

    # Existing template render for non-AJAX fallback
    return render_template("settings.html", ...)
```

This means the sidebar loads data on demand — no DB query on every page, no context processor needed. The sidebar opens, fetches `GET /settings` with XMLHttpRequest header, populates the form dynamically.

### 5d. Include in base.html

**File**: `shuffify/templates/base.html`

Include the sidebar partial for authenticated users:

```html
{% block settings_sidebar %}{% endblock %}
```

Or include it directly in the navbar block since it's always present for authenticated users.

### 5e. Wire nav bar Settings button

**File**: `shuffify/templates/partials/navbar.html`

Change Settings nav item from a link to a button that toggles the sidebar. Keep `active_nav == 'settings'` conditional so the `/settings` fallback page still highlights it:

```html
<button onclick="toggleSettingsSidebar()"
        class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150
               {{ 'bg-white/20 text-white' if active_nav == 'settings' else 'text-white/60 hover:text-white hover:bg-white/10' }}">
    {# gear icon #}
    <span class="hidden sm:inline text-sm font-medium">Settings</span>
</button>
```

### 5f. JS functions

Add to sidebar partial (inline `<script>` at bottom). Simple functions — NOT an object like `workshopSidebar` (that pattern has tabs, localStorage, animation guards which are overkill for a single form panel).

```javascript
function openSettingsSidebar() {
    // Fetch current settings via AJAX, populate form, then show
    fetch('/settings', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(r => r.json())
        .then(data => {
            populateSettingsForm(data.settings, data.algorithm_options);
            document.getElementById('settings-sidebar').classList.remove('translate-x-full');
            document.getElementById('settings-sidebar-backdrop').classList.remove('hidden');
        });
}

function closeSettingsSidebar() {
    document.getElementById('settings-sidebar').classList.add('translate-x-full');
    document.getElementById('settings-sidebar-backdrop').classList.add('hidden');
}

function toggleSettingsSidebar() {
    const sidebar = document.getElementById('settings-sidebar');
    if (sidebar.classList.contains('translate-x-full')) {
        openSettingsSidebar();
    } else {
        closeSettingsSidebar();
    }
}

// Escape key closes sidebar
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeSettingsSidebar();
});
```

`populateSettingsForm()` sets form field values from the JSON response — select values, toggle states, number inputs.

### 5g. Keep /settings as fallback

Keep the existing `/settings` GET route and `settings.html` template as a no-JS fallback. The sidebar is the primary interaction path but the full page still works.

## Verification

1. Click Settings in nav → sidebar slides in from right
2. All settings fields present and functional
3. Save button → AJAX save → success notification (no page reload)
4. Logout button at bottom → confirmation → logs out
5. Click backdrop or X → sidebar closes
6. Escape key closes sidebar
7. `/settings` direct URL still renders the full settings page
8. `flake8 shuffify/` and `pytest tests/ -v`

## CHANGELOG Entry

```markdown
### Changed
- **Settings** - Converted to slide-out sidebar accessible from navigation bar
  - AJAX save without page reload
  - Logout button moved into settings sidebar
  - Full settings page preserved as fallback
```
