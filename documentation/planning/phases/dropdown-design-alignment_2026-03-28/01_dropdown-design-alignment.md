# Phase 01: Dropdown Design System Alignment

**Status**: ✅ COMPLETE
**Started**: 2026-03-28
**Completed**: 2026-03-28

## Objective

Restyle the workshop playlist dropdown to match the green glass-morphism design system, add open/close animation, and polish item rendering.

## File to Modify

- `shuffify/templates/workshop.html`
  - **HTML**: lines 60-73 (dropdown container + search input)
  - **JS**: lines 1234-1337 (toggle, render, close functions)

## Changes

### 1. Glass-Morphism Container (HTML, line 62)

**Current** (`workshop.html:62`):
```html
<div id="playlist-dropdown"
     class="hidden absolute top-full left-0 mt-2 w-80 max-h-96 bg-spotify-dark/95 backdrop-blur-lg rounded-xl shadow-2xl border border-white/20 z-50 overflow-hidden">
```

**New**:
```html
<div id="playlist-dropdown"
     class="absolute top-full left-0 mt-2 w-80 max-h-96 bg-black/70 backdrop-blur-xl rounded-2xl shadow-2xl border border-white/20 z-50 overflow-hidden transition-[opacity,transform] duration-150 ease-out origin-top-left opacity-0 scale-95 pointer-events-none">
```

Key changes:
- `bg-spotify-dark/95` → `bg-black/70` (solid enough for readability, still glass-like with blur)
- `backdrop-blur-lg` → `backdrop-blur-xl` (stronger blur to ensure readability against green background)
- `rounded-xl` → `rounded-2xl` (matches card system)
- Remove `hidden`, add `opacity-0 scale-95 pointer-events-none` (for CSS animation)
- Add `transition-[opacity,transform] duration-150 ease-out origin-top-left` (GPU-friendly, avoids transitioning layout properties)

### 2. Search Input with Icon (HTML, lines 63-69)

**Current** (`workshop.html:63-69`):
```html
<div class="p-2 border-b border-white/10">
    <input id="playlist-dropdown-search"
           type="text"
           placeholder="Search playlists..."
           autocomplete="off"
           class="w-full px-3 py-2 bg-white/10 text-white text-sm rounded-lg border border-white/10 focus:border-white/30 focus:outline-none placeholder-white/40">
</div>
```

**New**:
```html
<div class="p-3 border-b border-white/10">
    <div class="relative">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/40 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
        </svg>
        <input id="playlist-dropdown-search"
               type="text"
               placeholder="Search playlists..."
               autocomplete="off"
               class="w-full pl-9 pr-3 py-2 bg-white/10 text-white text-sm rounded-lg border border-white/10 focus:ring-2 focus:ring-white/30 focus:border-transparent focus:outline-none placeholder-white/40 transition duration-150">
    </div>
</div>
```

Key changes:
- Wrapping `<div class="relative">` for search icon positioning
- Magnifying glass SVG icon, absolutely positioned left
- Input `px-3` → `pl-9 pr-3` to accommodate icon
- `focus:border-white/30 focus:outline-none` → `focus:ring-2 focus:ring-white/30 focus:border-transparent focus:outline-none` (system standard)
- Added `transition duration-150` for smooth focus state
- Outer div `p-2` → `p-3` for more comfortable spacing

### 3. Polished Item Rendering (JS, `renderPlaylistDropdown` function, line 1286)

**Current** item template (`workshop.html:1296-1305`):
```javascript
return `<button data-playlist-id="${escapeHtml(p.id)}"
            class="w-full flex items-center gap-3 px-3 py-2 hover:bg-white/10 transition duration-100 text-left ${isCurrent ? 'bg-white/10' : ''}"
            ${isCurrent ? 'disabled' : ''}>
            ${imgHtml}
            <div class="min-w-0 flex-1">
                <div class="text-white text-sm font-medium truncate ${isCurrent ? 'text-spotify-green' : ''}">${safeName}</div>
                <div class="text-white/40 text-xs">${p.track_count} tracks</div>
            </div>
            ${isCurrent ? '<svg ...checkmark...</svg>' : ''}
        </button>`;
```

**New** item template:
```javascript
return `<button data-playlist-id="${escapeHtml(p.id)}"
            class="w-full flex items-center gap-3 px-4 py-2.5 rounded-lg hover:bg-white/10 transition duration-150 text-left ${isCurrent ? 'bg-white/5 border-l-2 border-spotify-green' : 'border-l-2 border-transparent'}"
            ${isCurrent ? 'disabled' : ''}>
            ${imgHtml}
            <div class="min-w-0 flex-1">
                <div class="text-sm font-medium truncate ${isCurrent ? 'text-spotify-green' : 'text-white'}">${safeName}</div>
                <div class="text-white/40 text-xs">${p.track_count} tracks</div>
            </div>
            ${isCurrent ? '<svg class="w-4 h-4 text-spotify-green flex-shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"></path></svg>' : ''}
        </button>`;
```

Key changes:
- `px-3 py-2` → `px-4 py-2.5` (more comfortable spacing)
- Add `rounded-lg` (rounded hover highlight)
- `duration-100` → `duration-150` (system standard)
- Current playlist: `bg-white/10` → `bg-white/5 border-l-2 border-spotify-green` (green left accent bar)
- Non-current: `border-l-2 border-transparent` (maintain alignment)

**Also update the list container** to add horizontal padding for inset items (`workshop.html:70`):

Current: `<div id="playlist-dropdown-list" class="overflow-y-auto max-h-72 py-1">`
New: `<div id="playlist-dropdown-list" class="overflow-y-auto max-h-72 py-1 px-1">`

**Also update thumbnail size** in the `imgHtml` variable (`workshop.html:1289-1295`):

Current:
```javascript
const imgHtml = p.image_url
    ? `<img src="${escapeHtml(p.image_url)}" alt="" class="w-8 h-8 rounded object-cover flex-shrink-0">`
    : `<div class="w-8 h-8 rounded bg-white/10 ...">`;
```

New:
```javascript
const imgHtml = p.image_url
    ? `<img src="${escapeHtml(p.image_url)}" alt="" class="w-10 h-10 rounded-lg object-cover flex-shrink-0">`
    : `<div class="w-10 h-10 rounded-lg bg-white/10 ...">`;
```

Changes: `w-8 h-8 rounded` → `w-10 h-10 rounded-lg` (larger thumbnails, rounded corners match system)

Also update the fallback icon SVG size: `w-4 h-4` → `w-5 h-5` to match larger container.

### 4. Open/Close Animation (JS functions)

**Replace `togglePlaylistDropdown`** (`workshop.html:1238-1245`):

```javascript
function togglePlaylistDropdown() {
    const opening = !isPlaylistDropdownOpen();
    if (opening) {
        playlistDropdownEl.classList.remove('pointer-events-none', 'opacity-0', 'scale-95');
        playlistDropdownEl.classList.add('opacity-100', 'scale-100');
        loadPlaylistDropdown();
        requestAnimationFrame(() => playlistDropdownSearchEl.focus());
    } else {
        closePlaylistDropdown();
    }
}
```

**Replace `isPlaylistDropdownOpen`** (`workshop.html:1234-1236`):

```javascript
function isPlaylistDropdownOpen() {
    return playlistDropdownEl.classList.contains('opacity-100');
}
```

**Replace `closePlaylistDropdown`** (`workshop.html:1247-1251`):

```javascript
function closePlaylistDropdown() {
    playlistDropdownEl.classList.remove('opacity-100', 'scale-100');
    playlistDropdownEl.classList.add('opacity-0', 'scale-95', 'pointer-events-none');
    playlistDropdownSearchEl.value = '';
    clearTimeout(playlistSearchTimer);
}
```

Key changes:
- No longer toggles `hidden` class
- Uses `opacity-0 scale-95 pointer-events-none` ↔ `opacity-100 scale-100` for CSS-driven animation
- `pointer-events-none` prevents interaction when closed (replaces `hidden` behavior)
- The `transition-[opacity,transform] duration-150 ease-out origin-top-left` on the container handles the animation

### 5. Loading/Empty State Polish (JS)

**Loading state** — replace the initial HTML (`workshop.html:71`):

Current:
```html
<div class="px-4 py-3 text-white/50 text-sm text-center">Loading playlists...</div>
```

New:
```html
<div class="px-4 py-6 text-white/50 text-sm text-center flex flex-col items-center gap-2">
    <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
    </svg>
    Loading playlists...
</div>
```

**Empty state** — update in `renderPlaylistDropdown` (`workshop.html:1282`):

Current:
```javascript
playlistDropdownListEl.innerHTML = '<div class="px-4 py-3 text-white/50 text-sm text-center">No playlists found</div>';
```

New:
```javascript
playlistDropdownListEl.innerHTML = `<div class="px-4 py-6 text-white/50 text-sm text-center flex flex-col items-center gap-2">
    <svg class="w-6 h-6 text-white/30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"></path>
    </svg>
    No playlists found
</div>`;
```

**Error state** — update in `loadPlaylistDropdown` (`workshop.html:1267, 1271`):

Current:
```javascript
'<div class="px-4 py-3 text-red-300 text-sm text-center">Failed to load playlists</div>';
```

New:
```javascript
`<div class="px-4 py-6 text-red-300 text-sm text-center flex flex-col items-center gap-2">
    <svg class="w-6 h-6 text-red-300/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"></path>
    </svg>
    Failed to load playlists
</div>`;
```

## Verification

1. **Visual**: Open workshop page → click playlist name → dropdown should appear with glass-morphism styling matching the surrounding cards
2. **Animation**: Dropdown should fade+scale in over 150ms, and fade+scale out smoothly
3. **Search**: Type in search box → playlists filter correctly, search icon visible
4. **Switching**: Click a different playlist → navigates to that playlist's workshop
5. **Current playlist**: Should show green left border and green text with checkmark
6. **Close behavior**: Click outside or press Escape → dropdown closes with animation
7. **Loading state**: Brief spinner visible before playlists load
8. **Empty state**: Search for nonsense → music note icon with "No playlists found"
9. **Responsive**: Dropdown doesn't overflow viewport on narrow screens
10. **Run**: `flake8 shuffify/` (should pass — no Python changes)
11. **Run**: `pytest tests/ -v` (should pass — no backend changes)

## CHANGELOG Entry

```markdown
### Changed
- **Workshop Playlist Dropdown** - Redesigned dropdown to match green glass-morphism design system
  - Glass container with frosted dark backdrop replacing opaque dark background
  - Smooth open/close animation (scale + opacity, 150ms)
  - Larger playlist thumbnails with rounded corners
  - Search input with magnifying glass icon and standardized focus ring
  - Active playlist highlighted with green left border accent
  - Polished loading spinner, empty state, and error state
```
