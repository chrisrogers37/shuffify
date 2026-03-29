# Phase 05: Settings Sidebar

**Status**: PENDING
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

Port the workshop sidebar pattern (`workshopSidebar` JS object in workshop.html). The sidebar:
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

        {# Settings Form #}
        <div class="flex-1 overflow-y-auto p-4 space-y-6">
            {# Same form fields as settings.html #}
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

### 5c. AJAX settings save

**File**: `shuffify/routes/settings.py`

Add JSON response support to the existing POST handler. Check for `X-Requested-With: XMLHttpRequest` header:

```python
@main.route("/settings", methods=["POST"])
@require_auth_and_db
def update_settings(client=None, user=None):
    # ... existing validation and save logic ...

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "message": "Settings saved"})

    # Existing redirect for non-AJAX fallback
    flash("Settings updated!", "success")
    return redirect(url_for("main.settings"))
```

### 5d. Include in base.html

**File**: `shuffify/templates/base.html`

Include the sidebar partial for authenticated users:

```html
{% block settings_sidebar %}{% endblock %}
```

Or include it directly in the navbar block since it's always present for authenticated users.

### 5e. Wire nav bar Settings button

**File**: `shuffify/templates/partials/navbar.html`

Change Settings nav item from a link to a button that toggles the sidebar:

```html
<button onclick="toggleSettingsSidebar()"
        class="flex items-center gap-2 px-3 py-2 rounded-lg transition duration-150 text-white/60 hover:text-white hover:bg-white/10">
    {# gear icon #}
    <span class="hidden sm:inline text-sm font-medium">Settings</span>
</button>
```

### 5f. JS toggle functions

Add to sidebar partial or base.html:

```javascript
function toggleSettingsSidebar() {
    const sidebar = document.getElementById('settings-sidebar');
    const backdrop = document.getElementById('settings-sidebar-backdrop');
    const isOpen = !sidebar.classList.contains('translate-x-full');

    if (isOpen) {
        closeSettingsSidebar();
    } else {
        sidebar.classList.remove('translate-x-full');
        backdrop.classList.remove('hidden');
    }
}

function closeSettingsSidebar() {
    const sidebar = document.getElementById('settings-sidebar');
    const backdrop = document.getElementById('settings-sidebar-backdrop');
    sidebar.classList.add('translate-x-full');
    backdrop.classList.add('hidden');
}
```

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
