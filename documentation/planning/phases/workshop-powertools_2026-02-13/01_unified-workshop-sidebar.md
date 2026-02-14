# Phase 1: Unified Workshop Sidebar Framework

## PR Title
`feat: Add collapsible tabbed sidebar to workshop for powertools framework (#phase-1)`

## Risk Level
**Low** -- This phase is purely additive. It adds HTML, CSS, and JavaScript to the existing `workshop.html` template. No backend routes change. No existing JavaScript functions are modified. No database models are touched. The sidebar is inert (placeholder content only) and cannot break any existing workshop functionality.

## Estimated Effort
**Small** -- Approximately 3-4 hours of focused implementation.
- Sidebar HTML + CSS: ~1.5 hours
- Sidebar JavaScript (`workshopSidebar` namespace): ~1 hour
- Manual QA verification: ~30 minutes
- CHANGELOG + documentation: ~30 minutes

## Files Summary

| Action | File | Purpose |
|--------|------|---------|
| MODIFY | `shuffify/templates/workshop.html` | Add sidebar HTML, CSS, and JavaScript |
| MODIFY | `CHANGELOG.md` | Add entry under `[Unreleased]` |
| MODIFY | `documentation/README.md` | Add link to new planning directory |

**No files created. No files deleted. No backend changes. No test changes required** (this is a purely visual UI addition with placeholder content).

---

## Context

The Workshop Powertools enhancement suite is a multi-phase effort to surface advanced automation features directly inside the Playlist Workshop. The long-term goal is "set-and-forget" automation: users configure snapshot schedules, archive pairings, smart raids, and recurrence rules without leaving the workshop context.

Phase 1 establishes the **structural foundation** -- a collapsible sidebar with a tabbed interface. The sidebar itself does nothing functional in this phase; it provides empty, styled containers that Phases 2-5 will populate with real content. Getting the sidebar framework right (layout, animation, responsiveness, z-index stacking, localStorage persistence) in isolation makes future phases safer and faster because they only need to fill in tab content, not restructure layout.

---

## Dependencies

This is **Phase 1** -- it has no dependencies on other phases. Phases 2, 3, 4, and 5 all depend on this phase being merged first, as they inject content into the sidebar tabs created here.

---

## Detailed Implementation Plan

### Overview of Changes to `workshop.html`

The existing `workshop.html` template has this structure:

```
{% extends "base.html" %}
{% block content %}
<div class="min-h-screen ...">                          <!-- Line 6: root wrapper -->
    <div class="absolute inset-0 ...">                  <!-- Line 7: background pattern -->
    <div class="relative max-w-5xl mx-auto ...">        <!-- Line 10: workshop header -->
    <div class="relative max-w-5xl mx-auto ...">        <!-- Line 55: main workshop area -->
        <div class="grid grid-cols-1 lg:grid-cols-3 ..."> <!-- Line 56: 3-column grid -->
            <div class="lg:col-span-2">                 <!-- Line 59: track list (left 2/3) -->
            <div class="lg:col-span-1">                 <!-- Line 199: sidebar controls (right 1/3) -->
</div>
<script>...</script>                                     <!-- Lines 347-1464: all JavaScript -->
<style>...</style>                                       <!-- Lines 1466-1479: all CSS -->
{% endblock %}
```

The changes are:

1. **Add sidebar HTML** after the closing `</div>` of the main content container (after line 344, before the `<script>` tag on line 347). The sidebar is a fixed-position element, independent of the grid layout.
2. **Add sidebar CSS** inside the existing `<style>` block (lines 1466-1479).
3. **Add sidebar JavaScript** as a new script block, after the existing `<script>` block (after line 1464, before the `<style>` block).

This means existing JavaScript is untouched. The sidebar lives in its own namespace (`workshopSidebar`).

---

### Step 1: Add Sidebar HTML

**File**: `shuffify/templates/workshop.html`

**Location**: Insert immediately after line 344 (`</div>` closing the root `min-h-screen` div), BEFORE the `<!-- SortableJS via CDN -->` comment on line 347.

**What to insert**:

```html
<!-- =========================================================================
     Workshop Powertools Sidebar
     A collapsible tabbed panel on the right edge of the viewport.
     Phases 2-5 will populate each tab with real content.
     ========================================================================= -->

<!-- Sidebar Toggle Button (visible when sidebar is collapsed) -->
<button id="sidebar-toggle-btn"
        onclick="workshopSidebar.toggle()"
        class="fixed right-0 top-1/2 -translate-y-1/2 z-30 flex items-center justify-center w-10 h-24 rounded-l-xl bg-white/15 backdrop-blur-md border border-r-0 border-white/20 text-white/70 hover:text-white hover:bg-white/25 transition-all duration-200 shadow-lg"
        title="Open Powertools"
        aria-label="Toggle powertools sidebar"
        aria-expanded="false"
        aria-controls="sidebar-panel">
    <div class="flex flex-col items-center gap-1">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7"></path>
        </svg>
        <span class="text-xs font-bold tracking-widest" style="writing-mode: vertical-rl; text-orientation: mixed;">TOOLS</span>
    </div>
</button>

<!-- Sidebar Panel -->
<div id="sidebar-panel"
     class="fixed top-0 right-0 h-full z-30 flex transition-transform duration-300 ease-in-out translate-x-full"
     aria-hidden="true">

    <!-- Vertical Tab Bar (left edge of sidebar) -->
    <div class="flex flex-col items-center py-4 w-14 bg-black/30 backdrop-blur-xl border-l border-white/10 flex-shrink-0">
        <!-- Close / collapse button -->
        <button onclick="workshopSidebar.toggle()"
                class="mb-4 p-2 rounded-lg text-white/60 hover:text-white hover:bg-white/10 transition duration-150"
                title="Close sidebar"
                aria-label="Close powertools sidebar">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7"></path>
            </svg>
        </button>

        <div class="w-8 border-t border-white/10 mb-4"></div>

        <!-- Tab: Snapshots -->
        <button class="sidebar-tab-btn mb-2 p-2 rounded-lg transition duration-150 text-white/40 hover:text-white hover:bg-white/10"
                data-tab="snapshots"
                onclick="workshopSidebar.switchTab('snapshots')"
                title="Snapshots"
                aria-label="Snapshots tab"
                aria-selected="false"
                role="tab">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"></path>
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"></path>
            </svg>
        </button>

        <!-- Tab: Archive -->
        <button class="sidebar-tab-btn mb-2 p-2 rounded-lg transition duration-150 text-white/40 hover:text-white hover:bg-white/10"
                data-tab="archive"
                onclick="workshopSidebar.switchTab('archive')"
                title="Archive"
                aria-label="Archive tab"
                aria-selected="false"
                role="tab">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"></path>
            </svg>
        </button>

        <!-- Tab: Raids -->
        <button class="sidebar-tab-btn mb-2 p-2 rounded-lg transition duration-150 text-white/40 hover:text-white hover:bg-white/10"
                data-tab="raids"
                onclick="workshopSidebar.switchTab('raids')"
                title="Raids"
                aria-label="Raids tab"
                aria-selected="false"
                role="tab">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
            </svg>
        </button>

        <!-- Tab: Schedules -->
        <button class="sidebar-tab-btn mb-2 p-2 rounded-lg transition duration-150 text-white/40 hover:text-white hover:bg-white/10"
                data-tab="schedules"
                onclick="workshopSidebar.switchTab('schedules')"
                title="Schedules"
                aria-label="Schedules tab"
                aria-selected="false"
                role="tab">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
        </button>
    </div>

    <!-- Tab Content Area -->
    <div class="w-80 bg-black/40 backdrop-blur-xl border-l border-white/10 overflow-y-auto sidebar-scrollbar">

        <!-- Tab Content: Snapshots -->
        <div id="sidebar-tab-snapshots" class="sidebar-tab-content hidden p-5" role="tabpanel">
            <h3 class="text-white font-bold text-lg mb-2">Snapshots</h3>
            <div class="rounded-xl bg-white/5 border border-white/10 p-6 text-center">
                <svg class="w-12 h-12 mx-auto text-white/20 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"></path>
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"></path>
                </svg>
                <p class="text-white/60 text-sm mb-1">Browse and restore playlist snapshots</p>
                <p class="text-white/30 text-xs">View saved states, compare changes, and roll back to any previous version of this playlist.</p>
                <span class="inline-block mt-3 px-3 py-1 rounded-full bg-white/10 text-white/40 text-xs font-semibold">Coming in Phase 2</span>
            </div>
        </div>

        <!-- Tab Content: Archive -->
        <div id="sidebar-tab-archive" class="sidebar-tab-content hidden p-5" role="tabpanel">
            <h3 class="text-white font-bold text-lg mb-2">Archive</h3>
            <div class="rounded-xl bg-white/5 border border-white/10 p-6 text-center">
                <svg class="w-12 h-12 mx-auto text-white/20 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"></path>
                </svg>
                <p class="text-white/60 text-sm mb-1">Pair playlists with archive targets</p>
                <p class="text-white/30 text-xs">Link this playlist to an archive so removed tracks are automatically preserved instead of lost.</p>
                <span class="inline-block mt-3 px-3 py-1 rounded-full bg-white/10 text-white/40 text-xs font-semibold">Coming in Phase 3</span>
            </div>
        </div>

        <!-- Tab Content: Raids -->
        <div id="sidebar-tab-raids" class="sidebar-tab-content hidden p-5" role="tabpanel">
            <h3 class="text-white font-bold text-lg mb-2">Raids</h3>
            <div class="rounded-xl bg-white/5 border border-white/10 p-6 text-center">
                <svg class="w-12 h-12 mx-auto text-white/20 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
                </svg>
                <p class="text-white/60 text-sm mb-1">Smart playlist raids</p>
                <p class="text-white/30 text-xs">Pull tracks from source playlists with intelligent deduplication, filtering, and one-click raid execution.</p>
                <span class="inline-block mt-3 px-3 py-1 rounded-full bg-white/10 text-white/40 text-xs font-semibold">Coming in Phase 4</span>
            </div>
        </div>

        <!-- Tab Content: Schedules -->
        <div id="sidebar-tab-schedules" class="sidebar-tab-content hidden p-5" role="tabpanel">
            <h3 class="text-white font-bold text-lg mb-2">Schedules</h3>
            <div class="rounded-xl bg-white/5 border border-white/10 p-6 text-center">
                <svg class="w-12 h-12 mx-auto text-white/20 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                </svg>
                <p class="text-white/60 text-sm mb-1">Schedule automated operations</p>
                <p class="text-white/30 text-xs">Set up recurring shuffles, raids, and snapshots that run automatically on your chosen schedule.</p>
                <span class="inline-block mt-3 px-3 py-1 rounded-full bg-white/10 text-white/40 text-xs font-semibold">Coming in Phase 5</span>
            </div>
        </div>
    </div>
</div>

<!-- Sidebar Backdrop (mobile overlay) -->
<div id="sidebar-backdrop"
     class="fixed inset-0 z-20 bg-black/50 backdrop-blur-sm hidden transition-opacity duration-300 opacity-0 lg:hidden"
     onclick="workshopSidebar.close()">
</div>
```

**Why this placement**: The sidebar is a fixed-position element so it does not participate in the CSS grid layout of the workshop. Placing it after the main content div but before the scripts keeps the DOM orderly. The `z-30` value puts it above the main content but below notifications (`z-50`) and flash messages (`z-50`).

**Key design decisions**:

- **`z-30` for sidebar, `z-20` for backdrop**: The existing notification system uses `z-50`, and flash messages in `base.html` also use `z-50`. The sidebar at `z-30` stays below these so notifications always show above the sidebar.
- **`position: fixed`** instead of absolute: The sidebar is viewport-anchored, not scroll-dependent. It stays in place regardless of track list scroll position.
- **`translate-x-full` for hiding**: The sidebar slides off-screen to the right when collapsed. When opened, the transform is removed. This is a GPU-accelerated CSS transform animation.
- **Mobile backdrop at `z-20`**: On screens smaller than `lg` (1024px), a semi-transparent backdrop appears behind the sidebar. Clicking the backdrop closes the sidebar. On desktop (`lg:hidden` on the backdrop), no backdrop is shown because the sidebar overlays the right edge without blocking interaction with the main content.

---

### Step 2: Add Sidebar JavaScript

**File**: `shuffify/templates/workshop.html`

**Location**: Insert a new `<script>` block after the existing closing `</script>` tag (after line 1464) and BEFORE the existing `<style>` tag (line 1466).

```html
<script>
// =============================================================================
// Workshop Powertools Sidebar
// Self-contained namespace -- does NOT modify any existing workshopState,
// SortableJS, or search functionality.
// =============================================================================

const workshopSidebar = {
    isOpen: false,
    activeTab: null,
    _animating: false,

    /** Safely read from localStorage (handles disabled/full storage). */
    _getStorage(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    },

    /** Safely write to localStorage (handles disabled/full storage). */
    _setStorage(key, value) {
        try { localStorage.setItem(key, value); } catch (e) { /* silent */ }
    },

    /** Initialize sidebar on page load. */
    init() {
        // Restore collapsed/open state from localStorage
        const savedState = this._getStorage('shuffify_sidebar_open');
        const savedTab = this._getStorage('shuffify_sidebar_tab');

        if (savedState === 'true') {
            this.open(savedTab || 'snapshots', false);
        }

        // Close sidebar on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isOpen) {
                this.close();
            }
        });
    },

    /** Toggle sidebar open/closed. */
    toggle() {
        if (this._animating) return;
        if (this.isOpen) {
            this.close();
        } else {
            this.open(this.activeTab || 'snapshots');
        }
    },

    /**
     * Open the sidebar, optionally selecting a tab.
     * @param {string} tabName - Tab to activate (snapshots|archive|raids|schedules)
     * @param {boolean} animate - Whether to animate the transition (default true)
     */
    open(tabName, animate) {
        if (typeof animate === 'undefined') animate = true;
        const panel = document.getElementById('sidebar-panel');
        const toggleBtn = document.getElementById('sidebar-toggle-btn');
        const backdrop = document.getElementById('sidebar-backdrop');

        if (animate) {
            this._animating = true;
            setTimeout(() => { this._animating = false; }, 300);
        }

        // Show panel
        panel.classList.remove('translate-x-full');
        panel.setAttribute('aria-hidden', 'false');

        // Hide toggle button
        toggleBtn.classList.add('opacity-0', 'pointer-events-none');
        toggleBtn.setAttribute('aria-expanded', 'true');

        // Show backdrop on mobile
        backdrop.classList.remove('hidden');
        // Force reflow before adding opacity for transition
        void backdrop.offsetWidth;
        backdrop.classList.add('opacity-100');
        backdrop.classList.remove('opacity-0');

        this.isOpen = true;
        this.switchTab(tabName || 'snapshots');

        // Persist state
        this._setStorage('shuffify_sidebar_open', 'true');
    },

    /** Close the sidebar. */
    close() {
        if (this._animating) return;
        this._animating = true;

        const panel = document.getElementById('sidebar-panel');
        const toggleBtn = document.getElementById('sidebar-toggle-btn');
        const backdrop = document.getElementById('sidebar-backdrop');

        // Hide panel
        panel.classList.add('translate-x-full');
        panel.setAttribute('aria-hidden', 'true');

        // Show toggle button
        toggleBtn.classList.remove('opacity-0', 'pointer-events-none');
        toggleBtn.setAttribute('aria-expanded', 'false');

        // Hide backdrop
        backdrop.classList.remove('opacity-100');
        backdrop.classList.add('opacity-0');
        setTimeout(() => {
            backdrop.classList.add('hidden');
        }, 300);

        this.isOpen = false;

        // Persist state
        this._setStorage('shuffify_sidebar_open', 'false');

        setTimeout(() => { this._animating = false; }, 300);
    },

    /**
     * Switch to a specific tab.
     * @param {string} tabName - Tab to activate
     */
    switchTab(tabName) {
        // Deactivate all tabs
        document.querySelectorAll('.sidebar-tab-btn').forEach(btn => {
            btn.classList.remove('bg-white/20', 'text-white');
            btn.classList.add('text-white/40');
            btn.setAttribute('aria-selected', 'false');
        });

        // Hide all tab content
        document.querySelectorAll('.sidebar-tab-content').forEach(panel => {
            panel.classList.add('hidden');
        });

        // Activate selected tab button
        const activeBtn = document.querySelector('.sidebar-tab-btn[data-tab="' + tabName + '"]');
        if (activeBtn) {
            activeBtn.classList.add('bg-white/20', 'text-white');
            activeBtn.classList.remove('text-white/40');
            activeBtn.setAttribute('aria-selected', 'true');
        }

        // Show selected tab content
        const activePanel = document.getElementById('sidebar-tab-' + tabName);
        if (activePanel) {
            activePanel.classList.remove('hidden');
        }

        this.activeTab = tabName;
        this._setStorage('shuffify_sidebar_tab', tabName);

        // Fire tab activation hooks for lazy-loading (Phases 2-5)
        if (tabName === 'snapshots' && typeof onSnapshotsTabActivated === 'function') {
            onSnapshotsTabActivated();
        }
        if (tabName === 'raids' && typeof onRaidsTabActivated === 'function') {
            onRaidsTabActivated();
        }
    },
};

// Initialize sidebar after DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    workshopSidebar.init();
});
</script>
```

**Key design decisions**:

- **`workshopSidebar` namespace**: All sidebar logic is contained in a single object literal. This avoids polluting the global scope and prevents any name collisions with existing globals (`workshopState`, `searchState`, `sortableInstance`, `sourceSortableInstance`, etc.).
- **`_animating` guard**: Prevents rapid toggling from creating broken visual states. The 300ms timeout matches the CSS `transition-duration`.
- **`localStorage` persistence with defensive wrappers**: The open/closed state and active tab survive page refreshes. The keys are prefixed with `shuffify_` to avoid collisions. The `_getStorage` and `_setStorage` helpers catch exceptions for users with localStorage disabled or full.
- **Default collapsed**: On the very first page load (no localStorage entry), `savedState` is `null`, so the sidebar stays collapsed. This matches the requirement to default to collapsed.
- **Escape key**: Standard UX pattern for closing overlays.
- **Tab activation hooks**: The `switchTab` method calls `onSnapshotsTabActivated()` and `onRaidsTabActivated()` if those functions exist. These are defined in Phases 2 and 4 respectively. This allows lazy-loading of tab content without modifying Phase 1 code.
- **No dependency on existing JS**: The sidebar script does not import, reference, or modify `workshopState`, `sortableInstance`, or any other existing variable. It is completely self-contained.

---

### Step 3: Add Sidebar CSS

**File**: `shuffify/templates/workshop.html`

**Location**: Add the following rules inside the existing `<style>` block (lines 1466-1479). Insert them after line 1477 (after `.source-track-item:hover { background: rgba(255, 255, 255, 0.06); }`) and before the closing `</style>` tag.

```css
/* Workshop Powertools Sidebar */
.sidebar-scrollbar::-webkit-scrollbar { width: 6px; }
.sidebar-scrollbar::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.15); border-radius: 6px; }
.sidebar-scrollbar::-webkit-scrollbar-track { background: transparent; }
.sidebar-scrollbar { scrollbar-color: rgba(255, 255, 255, 0.15) transparent; scrollbar-width: thin; }

#sidebar-toggle-btn {
    transition: opacity 0.2s ease, background-color 0.2s ease;
}

/* On mobile/tablet, sidebar overlays the full viewport width area */
@media (max-width: 1023px) {
    #sidebar-panel {
        box-shadow: -8px 0 32px rgba(0, 0, 0, 0.5);
    }
}

/* On desktop (lg+), sidebar sits alongside content without backdrop */
@media (min-width: 1024px) {
    #sidebar-panel {
        box-shadow: -4px 0 16px rgba(0, 0, 0, 0.3);
    }
}
```

**Why minimal CSS**: Almost all styling is handled by Tailwind utility classes directly in the HTML. The CSS block only adds:
1. Scrollbar styling for the tab content area (matching the existing `workshop-scrollbar` pattern but with lighter thumb color appropriate for the darker sidebar background).
2. A smooth opacity transition on the toggle button.
3. Responsive box-shadow differences (stronger shadow on mobile where the sidebar overlays content).

---

### Step 4: Update CHANGELOG.md

**File**: `CHANGELOG.md`

**Location**: Add a new entry at the top of the `## [Unreleased]` section, under `### Added`.

```markdown
- **Workshop Powertools Sidebar** - Collapsible tabbed sidebar on the workshop page
  - Four tab placeholders: Snapshots, Archive, Raids, Schedules
  - Smooth slide-in/out animation with localStorage state persistence
  - Responsive design: overlays on mobile with backdrop, side panel on desktop
  - Self-contained `workshopSidebar` JavaScript namespace (no impact on existing workshop JS)
  - Foundation for Phases 2-5 of the Workshop Powertools enhancement suite
```

---

### Step 5: Update Documentation README

**File**: `documentation/README.md`

**Location**: In the `## Planning (Archived)` section heading, rename it to include an active subsection, or add a new `## Planning (Active)` section above it. Add the following entry:

```markdown
## Planning (Active)

- **[Workshop Powertools Enhancement Suite](planning/phases/workshop-powertools_2026-02-13/00_OVERVIEW.md)** - Multi-phase plan for sidebar powertools framework (Phase 1 in progress)
```

The planning directory `documentation/planning/phases/workshop-powertools_2026-02-13/` already exists, ready for this phase plan file to be placed there.

---

## Complete File Diff Summary

### `shuffify/templates/workshop.html`

Three insertion points, zero modifications to existing code:

| Line Reference | Action | What |
|---------------|--------|------|
| After line 344 (before `<!-- SortableJS via CDN -->`) | INSERT | ~130 lines of sidebar HTML (toggle button, panel, tabs, placeholders, backdrop) |
| After line 1464 (after existing `</script>`) | INSERT | ~130 lines of sidebar JavaScript in new `<script>` block |
| After line 1477 (inside existing `<style>`) | INSERT | ~18 lines of sidebar CSS |

**Total lines added**: ~278 lines
**Total lines modified**: 0
**Total lines deleted**: 0

---

## Test Plan

Since this phase is a purely visual, frontend-only addition with no backend changes, the testing strategy is manual verification. No existing tests need to change because:

1. No routes are added or modified.
2. No Python code changes.
3. The workshop template still renders the same Jinja2 variables (`playlist`, `user`, `algorithms`).
4. All existing test assertions about the workshop page content (checking for elements like `#track-list`, `#save-btn`, `#search-input`) remain valid because none of those elements are moved or removed.

### Manual Verification Checklist

**Sidebar Toggle**:
- [ ] On page load, sidebar is collapsed (only toggle button visible on right edge)
- [ ] Clicking toggle button opens sidebar with smooth slide animation
- [ ] Clicking close button (chevron-right) inside sidebar closes it
- [ ] Clicking toggle button again after opening closes sidebar
- [ ] Pressing Escape key closes sidebar when it is open
- [ ] Rapid double-clicking the toggle button does not cause broken state

**Tab Navigation**:
- [ ] All 4 tabs (Snapshots, Archive, Raids, Schedules) are visible in the vertical tab bar
- [ ] Clicking a tab highlights it and shows the corresponding content panel
- [ ] Only one tab content panel is visible at a time
- [ ] Each tab shows the correct icon, title, description, and "Coming in Phase X" badge

**State Persistence**:
- [ ] Open sidebar, select "Archive" tab, refresh page -- sidebar should reopen on "Archive" tab
- [ ] Close sidebar, refresh page -- sidebar should remain closed
- [ ] Clear localStorage, reload -- sidebar defaults to collapsed

**Responsive Behavior**:
- [ ] Desktop (>= 1024px): Sidebar overlays right edge, no backdrop, main content still visible and interactive
- [ ] Mobile/tablet (< 1024px): Semi-transparent backdrop appears behind sidebar
- [ ] Mobile: Clicking backdrop closes sidebar
- [ ] Mobile: Sidebar does not push main content sideways

**Existing Functionality Preserved**:
- [ ] Track list drag-and-drop (SortableJS) still works with sidebar open and closed
- [ ] Search Spotify panel still works (type query, get results, add track)
- [ ] Shuffle Preview still works (select algorithm, click preview, tracks reorder)
- [ ] Save to Spotify button still works
- [ ] Undo Changes button still works
- [ ] Source Playlists panel still expands/collapses
- [ ] External Playlist loading still works
- [ ] Delete track (X button) still works
- [ ] Notifications still appear at bottom-right and are visible even when sidebar is open
- [ ] Track count updates correctly after add/delete
- [ ] Modified badge appears/disappears correctly

**Accessibility**:
- [ ] Toggle button has `aria-label` and `aria-expanded`
- [ ] Sidebar panel has `aria-hidden` that toggles with state
- [ ] Tab buttons have `role="tab"` and `aria-selected`
- [ ] Tab content panels have `role="tabpanel"`
- [ ] Tab navigation works with keyboard focus (Tab key)

---

## Documentation Updates

| File | Change |
|------|--------|
| `CHANGELOG.md` | Add entry under `[Unreleased] > Added` describing the sidebar framework |
| `documentation/README.md` | Add link to the workshop-powertools planning directory |

No changes to `CLAUDE.md` or `README.md` are needed. The sidebar is an internal UI element that does not change the project's external interface, commands, or architecture.

---

## Stress Testing and Edge Cases

### 1. Mobile Viewport Behavior

**Scenario**: User is on a phone (320px-428px width).
**Expected**: The sidebar opens as a right-side overlay (width 56px tab bar + 320px content = 376px total). On very narrow screens, this may nearly fill the viewport. The backdrop prevents interaction with underlying content. Closing via backdrop click or Escape is always available.
**Edge case**: If the viewport is narrower than the sidebar, the sidebar still renders at its fixed width and extends beyond the left edge. This is acceptable for Phase 1 (placeholders only); if real Phase 2-5 content requires mobile optimization, it can adjust the content area width at that time.

### 2. Very Long Track Lists (500+ tracks)

**Scenario**: User loads a playlist with 500 tracks. The track list DOM is large.
**Expected**: No performance impact. The sidebar is a separate DOM subtree with no queries against `.track-item` elements. SortableJS only operates on `#track-list`. The sidebar's CSS `position: fixed` means it is not part of the document flow and does not cause reflow of the track list.

### 3. Interaction with Existing Search Panel

**Scenario**: User has the search panel open on the right sidebar (the existing `lg:col-span-1` column) and also opens the powertools sidebar.
**Expected**: The powertools sidebar overlays on top of the existing right column content. Since the powertools sidebar uses `position: fixed` with `z-30` and the existing content is in the normal document flow, the sidebar appears above it. The user can close the sidebar to see the search panel again.

### 4. Multiple Rapid Toggle Clicks

**Scenario**: User clicks the toggle button 5 times rapidly.
**Expected**: The `_animating` guard prevents toggle during the 300ms transition. At most, the first click fires immediately, the next 4 are ignored. After 300ms, clicking works again. No broken state, no partially-visible sidebar.

### 5. localStorage Disabled or Full

**Scenario**: User has localStorage disabled in their browser, or localStorage is full.
**Expected**: The `_getStorage` and `_setStorage` helper methods wrap all localStorage access in try/catch blocks. If localStorage is unavailable, the sidebar still functions correctly -- it simply does not persist state across page loads. On each page load it will default to collapsed.

### 6. Sidebar and Notification Overlap

**Scenario**: User triggers a notification ("Track removed from working copy") while the sidebar is open.
**Expected**: Notifications use `z-50` and are positioned `fixed bottom-4 right-4`. The sidebar uses `z-30`. Notifications will appear above the sidebar. They may visually overlap with the sidebar's right edge. This is cosmetically acceptable; the notification auto-dismisses after 3 seconds.

---

## Verification Checklist

After implementation, the engineer should run through these exact steps:

1. **Start the dev server**: `python run.py`
2. **Log in** with Spotify credentials
3. **Open any playlist** in the workshop
4. **Verify sidebar toggle button** is visible on the right edge of the viewport (vertically centered, says "TOOLS")
5. **Click the toggle button** -- sidebar should slide in from the right with 4 tab icons visible
6. **Click each tab icon** (top to bottom): verify Snapshots, Archive, Raids, Schedules content appears
7. **Click the close button** (chevron-right at top of tab bar) -- sidebar slides out
8. **Open sidebar, then press Escape** -- sidebar closes
9. **Open sidebar, select "Raids" tab, refresh the page** -- sidebar should reopen on Raids tab
10. **Close sidebar, refresh** -- sidebar stays closed
11. **Resize browser to < 1024px wide** -- open sidebar, verify backdrop appears
12. **Click the backdrop** -- sidebar closes
13. **With sidebar open, drag a track** in the track list -- verify SortableJS drag-and-drop works
14. **With sidebar open, type a search query** and add a track -- verify search and add still work
15. **With sidebar open, click "Preview Shuffle"** -- verify shuffle preview works
16. **Run existing tests**: `pytest tests/ -v` -- all tests should pass (no Python changes)
17. **Run linter**: `flake8 shuffify/` -- should have 0 errors (no Python changes)

---

## What NOT To Do

1. **Do NOT modify the existing grid layout**. The sidebar is `position: fixed`, completely independent of the `grid-cols-1 lg:grid-cols-3` layout. Do not change `max-w-5xl`, do not change `lg:col-span-2` or `lg:col-span-1`. The sidebar floats above the existing layout.

2. **Do NOT add any code inside the existing `<script>` block** (lines 350-1464). The sidebar JavaScript goes in a separate, new `<script>` block. This ensures zero risk of accidentally breaking the existing `workshopState`, `Sortable`, or search logic.

3. **Do NOT reference `workshopState` from sidebar code**. The sidebar is purely structural in Phase 1. Future phases will bridge the sidebar and workshop state, but Phase 1 must not create any coupling.

4. **Do NOT use `z-40` or higher for the sidebar**. Notifications and flash messages use `z-50`. The modal in `schedules.html` also uses `z-50`. The sidebar should sit below these at `z-30`, with the backdrop at `z-20`.

5. **Do NOT add a `<link>` tag for external CSS**. All styling is handled via Tailwind utility classes and the inline `<style>` block, consistent with every other template in the project.

6. **Do NOT make the sidebar push content**. Some sidebar implementations use `margin-right` or `padding-right` on the main content to make room. This approach would break the existing grid layout and cause track list reflow. The sidebar must overlay, not push.

7. **Do NOT add backend routes or Python code**. Phase 1 is frontend-only. The placeholder tabs contain static HTML. No API calls, no database queries, no imports.

8. **Do NOT use `document.getElementById('track-list')` or similar in sidebar code**. The sidebar must not query, modify, or observe DOM elements that belong to the existing workshop. Keep the namespaces completely separate.

9. **Do NOT forget the mobile backdrop**. Without the backdrop, mobile users would have no way to close the sidebar by tapping outside it (there is no visible "close" affordance in the tab bar on small screens without deliberate hunting). The backdrop is essential for mobile UX.

10. **Do NOT use `position: absolute` instead of `position: fixed`**. The workshop page scrolls (track lists, source panel, etc.). An absolutely-positioned sidebar would scroll with the page, creating confusing behavior. Fixed positioning keeps the sidebar anchored to the viewport.
