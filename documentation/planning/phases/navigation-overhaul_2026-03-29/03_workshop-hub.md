# Phase 03: Workshop Hub

**Status**: PENDING
**Depends on**: Phase 02

## Objective

Make the Workshop a standalone hub accessible from the nav bar without requiring a playlist selection. Empty state with playlist dropdown selector. Selecting a playlist loads the full workshop.

## Files to Modify

- `shuffify/routes/workshop.py`
- `shuffify/templates/workshop.html`

## Changes

### 3a. New `/workshop` route

**File**: `shuffify/routes/workshop.py`

Add a new route before the existing `/workshop/<playlist_id>`:

```python
@main.route("/workshop")
@require_auth_and_db
def workshop_hub(client=None, user=None):
    """Render the Workshop hub with no playlist selected."""
    algorithms = ShuffleService.list_algorithms()
    return render_template(
        "workshop.html",
        playlist=None,
        tracks=[],
        algorithms=algorithms,
        prev_playlist_id=None,
        next_playlist_id=None,
        upstream_sources_json=[],
        active_nav='workshop',
    )
```

Minimal context — just enough to render the shell. The playlist dropdown already fetches from `/api/user-playlists` independently.

### 3b. Workshop empty state

**File**: `shuffify/templates/workshop.html`

Wrap the main content sections in `{% if playlist %}` / `{% else %}`:

**Header card**: Always show, but when `playlist` is None:
- Hide prev/next arrows
- Show "Workshop" as the title instead of playlist name
- The playlist dropdown toggle button becomes the primary CTA: "Select a playlist"
- Hide the visibility toggle, track count, modified badge, undo/save buttons

**Shuffle bar**: Wrap in `{% if playlist %}`

**Track list**: Wrap in `{% if playlist %}`

**Sidebar**: Wrap in `{% if playlist %}`

**Empty state block** (shown when no playlist):

```html
{% else %}
<div class="relative max-w-5xl mx-auto px-4 pt-8">
    <div class="p-12 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 text-center">
        <svg class="w-16 h-16 text-white/30 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"></path>
        </svg>
        <h2 class="text-2xl font-bold text-white mb-2">Select a playlist</h2>
        <p class="text-white/60 mb-6">Choose a playlist to start shuffling, reordering, and managing tracks.</p>
        {# Inline the playlist dropdown here, or a prominent button that triggers it #}
    </div>
</div>
{% endif %}
```

**Dropdown behavior change**: When on the hub (no playlist), the dropdown should be the centerpiece. After selecting a playlist, navigate to `/workshop/<id>` (existing behavior — already works this way in the JS).

### 3c. Template conditionals

Key sections to wrap in `{% if playlist %}`:

1. **Header card internals** (lines 14-126): Show simplified header when no playlist
2. **Shuffle bar** (lines 129-180): Hide entirely
3. **Track list** (lines ~200-440): Hide entirely
4. **Sidebar** (lines ~443-839): Hide entirely
5. **JS that references playlist data**: Guard with `{% if playlist %}` to avoid JS errors from undefined template variables

## Verification

1. Navigate to `/workshop` → see empty state with "Select a playlist" prompt
2. Open dropdown → playlists appear (ordered by preferences)
3. Select a playlist → navigates to `/workshop/<id>` with full workshop
4. Direct URL `/workshop/<id>` still works as before
5. Nav bar highlights "Workshop" on both `/workshop` and `/workshop/<id>`
6. No JS console errors on the empty state page
7. `flake8 shuffify/` and `pytest tests/ -v`

## CHANGELOG Entry

```markdown
### Added
- **Workshop Hub** - Workshop is now accessible as a standalone page from the navigation bar
  - Empty state with playlist selector when no playlist is loaded
  - Select a playlist to enter the full workshop experience
```
