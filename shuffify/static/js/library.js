/**
 * Library page: playlist preferences, Organize mode and refresh. Favorite, hide and show save app
 * preferences only, so a confirmed reply moves its row in place. Move up, Move down, Move
 * to position and pointer drops share one order path (moveTile): the row takes its new
 * place in its own section, the move is announced once through AppPanels.announce, and
 * the order is saved (the sections in ORDERED_SECTIONS, in turn; Hidden keeps no order).
 * Refresh and reset reload through the shared navigation guard; a declined reload, or a
 * change whose outcome is unknown, leaves one persistent notice.
 * Spotify changes (shuffle, restore, visibility) belong to library-shuffle.js.
 */
(function () {
    'use strict';

    // The page's persistent reload notice; each reason it names stays until the page reloads.
    const LIBRARY_NOTICE = 'library-reload-notice';
    // The list each saved state belongs in, as a fresh page places it.
    const LIBRARY_GRIDS = {favorites: 'favorites-grid', regular: 'playlist-grid', hidden: 'hidden-grid'};
    // The sections that keep an order, in saved order, by the name a move announces. Only
    // their rows drag and show order controls; Hidden keeps no order.
    const ORDERED_SECTIONS = {favorites: 'Favorites', regular: 'All playlists'};

    // Every Library reload asks the shared navigation guard first: Ready reloads,
    // Busy waits, and Attention needs an explicit decision to lose page details.
    function refreshLibraryPage(reason) {
        return window.AppNavigation.reloadOrNotice(LIBRARY_NOTICE, reason);
    }

    // Refreshing reads Spotify again, then reloads to show what it read. It keeps the
    // button focusable while it waits, so focus never falls back to the page.
    let refreshingPlaylists = false;
    async function refreshPlaylists() {
        const btn = document.getElementById('refresh-playlists-btn');
        const icon = document.getElementById('refresh-icon');
        if (refreshingPlaylists) return false;
        refreshingPlaylists = true;
        btn.setAttribute('aria-disabled', 'true');
        icon.classList.add('sh-spinner');
        let reply;
        try {
            reply = await window.AppRequest.send(btn.dataset.refreshUrl, {method: 'GET', cache: 'no-store'});
        } finally {
            refreshingPlaylists = false;
            btn.removeAttribute('aria-disabled');
            icon.classList.remove('sh-spinner');
        }
        if (!reply.ok) {
            showNotification(reply.data.message || 'Failed to refresh playlists. Please try again.', 'error');
            return false;
        }
        showNotification(reply.data.message || 'Playlists refreshed successfully.', 'success');
        return refreshLibraryPage('Your playlists were refreshed from Spotify, but this page still shows the earlier list.');
    }

    /* --- Organize mode --- */
    let organizeMode = false;

    // Organize drags the rows of the sections that keep an order.
    function syncTileDrag(tile) {
        const draggable = organizeMode && Boolean(ORDERED_SECTIONS[tile.dataset.section]);
        tile.draggable = draggable;
        tile.classList.toggle('organize-mode-active', draggable);
    }

    // The toggle's aria-expanded also turns its chevron (components/library.css).
    function toggleHiddenSection() {
        const wrapper = document.getElementById('hidden-grid-wrapper');
        if (!wrapper) return;
        const opening = wrapper.classList.contains('hidden');
        wrapper.classList.toggle('hidden');
        document.getElementById('hidden-toggle-btn').setAttribute('aria-expanded', String(opening));
    }

    // Organize shows each row's preference controls and the note saying they save at once.
    function toggleOrganizeMode() {
        organizeMode = !organizeMode;
        document.getElementById('organize-note').hidden = !organizeMode;
        document.getElementById('organize-btn-text').textContent = organizeMode ? 'Done' : 'Organize';
        document.querySelectorAll('.library-organize').forEach(controls => { controls.hidden = !organizeMode; });
        document.querySelectorAll('.card-tile').forEach(syncTileDrag);
        // Leaving Organize collapses the Hidden list again.
        const wrapper = document.getElementById('hidden-grid-wrapper');
        if (!organizeMode && wrapper && !wrapper.classList.contains('hidden')) toggleHiddenSection();
    }

    /* --- Rows: favorite, hide and show, in place, from the saved reply --- */
    // One request per row at a time; a repeat activation while it runs is ignored.
    const preferenceRequests = new Set();

    function libraryTile(playlistId) {
        return Array.from(document.querySelectorAll('.card-tile'))
            .find(tile => tile.dataset.playlistId === playlistId) || null;
    }

    // The rows of one section, in their order on the page.
    function sectionTiles(section) {
        return Array.from(document.querySelectorAll('#' + LIBRARY_GRIDS[section] + ' .card-tile'));
    }

    function isShown(element) {
        return Boolean(element) && element.isConnected && element.getClientRects().length > 0;
    }

    // Shows a row's saved favorite and hidden state on every control that names it.
    function renderTileState(tile) {
        const pinned = tile.dataset.pinned === 'true', isHidden = tile.dataset.hidden === 'true';
        const name = tile.dataset.playlistName || '';
        const favorite = tile.querySelector('.fav-btn');
        if (favorite) {
            const label = pinned ? 'Unfavorite' : 'Favorite';
            favorite.setAttribute('aria-label', label + ': ' + name);
            const words = favorite.querySelector('.library-favorite-label');
            if (words) words.textContent = label;
        }
        tile.querySelectorAll('.library-star').forEach(icon => {
            icon.classList.toggle('sh-library-favorite', pinned);
            icon.classList.toggle('sh-text-muted', !pinned);
            icon.setAttribute('fill', pinned ? 'currentColor' : 'none');
        });
        tile.querySelectorAll('.hide-btn').forEach(button => { button.hidden = isHidden; });
        tile.querySelectorAll('.unhide-btn').forEach(button => { button.hidden = !isHidden; });
        // Only a section that keeps an order gives its rows order controls.
        const order = tile.querySelector('.library-order');
        if (order) order.hidden = !ORDERED_SECTIONS[tile.dataset.section];
    }

    function markEnd(button, atEnd) {
        if (!button) return;
        if (atEnd) button.setAttribute('aria-disabled', 'true');
        else button.removeAttribute('aria-disabled');
    }

    // A row's place in its section: Move up and Move down mark the first and last place,
    // and the position field shows where the row is. A field being typed in keeps its entry.
    function renderPosition(tile, position, count) {
        markEnd(tile.querySelector('.library-move-up'), position <= 1);
        markEnd(tile.querySelector('.library-move-down'), position >= count);
        const field = tile.querySelector('.library-position');
        if (field) {
            field.max = String(count);
            field.removeAttribute('aria-invalid');
            if (field !== document.activeElement) field.value = String(position);
        }
        const range = tile.querySelector('.library-position-range');
        if (range) range.textContent = 'of ' + count;
    }

    // Shows the places first…last (1-based) of one section's rows, given in page order.
    function renderPositions(rows, first, last) {
        for (let position = first; position <= last; position++) renderPosition(rows[position - 1], position, rows.length);
    }

    // Each section's count follows its list. An empty Favorites or Hidden section is
    // hidden, never removed, and an empty All playlists says why. Every row of a section
    // that keeps an order shows its place there.
    function updateLibrarySections() {
        const rows = {};
        Object.keys(LIBRARY_GRIDS).forEach(section => { rows[section] = sectionTiles(section); });
        const favorites = rows.favorites.length, regular = rows.regular.length, hidden = rows.hidden.length;
        document.getElementById('favorites-count').textContent = String(favorites);
        document.getElementById('favorites-section').hidden = favorites === 0;
        document.getElementById('playlists-count').textContent = String(regular);
        document.getElementById('playlists-empty').hidden = regular > 0;
        document.getElementById('hidden-count').textContent = String(hidden);
        document.getElementById('hidden-playlists-section').hidden = hidden === 0;
        Object.keys(ORDERED_SECTIONS).forEach(section => renderPositions(rows[section], 1, rows[section].length));
    }

    // The row controls a preference change can start from, and the control each becomes
    // when the change makes it no longer apply: Hide becomes Show, and Show becomes Hide.
    const TILE_CONTROLS = ['hide-btn', 'unhide-btn', 'fav-btn'];
    const COUNTERPARTS = {'hide-btn': 'unhide-btn', 'unhide-btn': 'hide-btn', 'fav-btn': 'fav-btn'};

    // Moves a row to the section its saved state belongs in. Focus that was elsewhere never
    // moves. While the row is still shown, focus stays on it: the control that was used,
    // else the control it became, else the row itself (moving a row takes focus out of it,
    // so it is given back). Only a row that went into the collapsed Hidden list hands focus
    // to the row that took its place (the same control, else that row), else the Hidden toggle.
    function placeTile(tile, change) {
        const focused = tile.contains(document.activeElement) ? document.activeElement : null;
        const neighbor = tile.nextElementSibling || tile.previousElementSibling;
        Object.keys(change).forEach(key => { tile.dataset[key] = String(change[key]); });
        const section = tile.dataset.hidden === 'true' ? 'hidden' : tile.dataset.pinned === 'true' ? 'favorites' : 'regular';
        const grid = document.getElementById(LIBRARY_GRIDS[section]);
        if (tile.parentElement !== grid) {
            // A row leaving Favorites or Hidden leads All playlists; others join the end of their section.
            if (section === 'regular') grid.prepend(tile);
            else grid.append(tile);
        }
        tile.dataset.section = section;
        renderTileState(tile);
        syncTileDrag(tile);
        updateLibrarySections();
        if (!focused) return;
        const control = TILE_CONTROLS.find(name => focused.classList.contains(name));
        const inRow = (owner, name) => (owner && name ? owner.querySelector('.' + name) : null);
        const target = (isShown(tile) ? [focused, inRow(tile, COUNTERPARTS[control]), tile]
            : [inRow(neighbor, control), neighbor, document.getElementById('hidden-toggle-btn')]).find(isShown);
        if (target) window.AppPanels.focusNear(target);
    }

    async function updateTilePreference(playlistId, field) {
        const tile = libraryTile(playlistId);
        if (!tile || preferenceRequests.has(playlistId)) return null;
        const name = tile.dataset.playlistName || 'this playlist';
        const key = field === 'pinned' ? 'is_pinned' : 'is_hidden';
        preferenceRequests.add(playlistId);
        tile.setAttribute('aria-busy', 'true');
        let reply;
        try {
            reply = await window.AppRequest.send('/api/playlist-preferences/' + encodeURIComponent(playlistId) +
                (field === 'pinned' ? '/toggle-pinned' : '/toggle-hidden'), {method: 'POST'});
        } finally {
            preferenceRequests.delete(playlistId);
            tile.removeAttribute('aria-busy');
        }
        const saved = reply.data[key];
        if (reply.ok && typeof saved === 'boolean') {
            placeTile(tile, {[field]: saved});
            showNotification(field === 'pinned' ? (saved ? 'Added to Favorites' : 'Removed from Favorites')
                : (saved ? 'Playlist hidden' : 'Playlist shown'), 'success');
            return reply.data;
        }
        if (reply.ok) {
            window.AppNavigation.showReloadNotice(LIBRARY_NOTICE,
                'Your change to “' + name + '” was saved, but this page could not show it.');
        } else if (reply.answered) {
            showNotification(reply.data.message ||
                (field === 'pinned' ? 'Failed to update favorite state' : 'Failed to update hidden state'), 'error');
        } else {
            window.AppNavigation.showReloadNotice(LIBRARY_NOTICE, 'Your change to “' + name + '” could not be confirmed.');
        }
        return null;
    }

    function toggleFavorite(playlistId) {
        return updateTilePreference(playlistId, 'pinned');
    }

    // The server toggles; each control asks only for the change it shows.
    function hidePlaylist(playlistId) {
        const tile = libraryTile(playlistId);
        return tile && tile.dataset.hidden !== 'true' ? updateTilePreference(playlistId, 'hidden') : Promise.resolve(null);
    }

    function unhidePlaylist(playlistId) {
        const tile = libraryTile(playlistId);
        return tile && tile.dataset.hidden === 'true' ? updateTilePreference(playlistId, 'hidden') : Promise.resolve(null);
    }

    function togglePlaylistVisibility(playlistId, btnEl) {
        return window.LibraryPlaylistActions.setVisibility(playlistId, btnEl);
    }

    // Resetting clears every saved row preference, so it asks first and then reloads.
    async function resetPreferences(trigger) {
        const confirmed = await window.AppConfirm.ask({
            title: 'Reset Library preferences?',
            message: 'This clears your favorites, hidden playlists and tile order, and returns the Library to ' +
                'Spotify’s order. Your playlists on Spotify do not change.',
            confirmLabel: 'Reset preferences', trigger: trigger});
        if (!confirmed) return null;
        // An order save already sending lands before the reset, and none is sent while it runs.
        resetting = true;
        let reply;
        try {
            await orderSaving;
            reply = await window.AppRequest.send('/api/playlist-preferences/reset', {method: 'POST'});
        } finally {
            resetting = false;
        }
        if (reply.ok) {
            // The reset replaced any order shown meanwhile.
            orderQueued = false;
            showNotification(reply.data.message || 'Preferences reset.', 'success');
            await refreshLibraryPage('Your Library preferences were reset, but this page still shows the earlier layout.');
            return reply.data;
        }
        // A refused reset leaves the user's order on the rows, and an order left unsaved is saved
        // now.
        if (reply.answered) {
            if (orderQueued) saveCurrentOrder();
            showNotification(reply.data.message || 'Failed to reset preferences', 'error');
            return null;
        }
        // A reset whose outcome is unknown may have cleared the order, so an order left unsaved
        // is dropped (a later reset never sends it), and the notice names it beside the reset:
        // the reload it offers shows what was kept, and the next move sends the order shown.
        window.AppNavigation.showReloadNotice(LIBRARY_NOTICE, 'The preferences reset could not be confirmed.');
        if (orderQueued) {
            window.AppNavigation.showReloadNotice(LIBRARY_NOTICE, 'Your new Library order could not be confirmed.');
            orderQueued = false;
        }
        return null;
    }

    /* --- Order: keyboard moves and pointer drops share one path --- */
    // The saved order is each ordered section's rows in turn: Favorites, then All playlists.
    function sendOrder() {
        const playlistIds = Object.keys(ORDERED_SECTIONS).flatMap(sectionTiles).map(tile => tile.dataset.playlistId);
        // headers replaces AppRequest's own, so it names X-Requested-With too.
        return window.AppRequest.send('/api/playlist-preferences/order', {method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify({playlist_ids: playlistIds})});
    }

    // One order save at a time. A move while one is sending queues one more save, which
    // sends the order shown when it starts, so the last order shown is the one saved. Only
    // the latest save says how it went: its move was already announced, so a saved order
    // is shown without being read out. A refusal is read out with its reason. A reply that
    // cannot be read may still have saved the order, so the persistent notice says it is
    // unconfirmed and offers the reload that shows the saved order. While a reset runs no
    // order is sent: the reset decides the saved order, and the order shown waits for it.
    let orderSaving = null, orderQueued = false, resetting = false;
    function saveCurrentOrder() {
        if (orderSaving || resetting) {
            orderQueued = true;
            return orderSaving || Promise.resolve();
        }
        orderSaving = sendLatestOrder();
        return orderSaving;
    }
    async function sendLatestOrder() {
        let reply;
        try {
            do {
                orderQueued = false;
                reply = await sendOrder();
            } while (orderQueued && !resetting);
        } finally {
            orderSaving = null;
        }
        // A reset began: an order it does not confirm is saved again only if the reset fails.
        if (resetting) {
            if (!reply.ok) orderQueued = true;
            return;
        }
        if (reply.ok) showNotification('Order saved', 'success', {spoken: false});
        else if (reply.answered) showNotification(reply.data.message || 'Failed to save order', 'error');
        else window.AppNavigation.showReloadNotice(LIBRARY_NOTICE, 'Your new Library order could not be confirmed.');
    }

    // Puts a row at a 1-based place in its own section, held between the first and the last
    // place. A move to where the row already is changes nothing and says so. Otherwise only
    // the rows from its old place to its new one show their places again (a move within a
    // section changes no count), the control that moved it keeps focus (moving a row takes
    // focus out of it), the move is announced once and the new order is saved.
    function moveTile(tile, position, control) {
        const section = tile.dataset.section, label = ORDERED_SECTIONS[section];
        if (!label) return;
        const rows = sectionTiles(section), from = rows.indexOf(tile) + 1;
        const to = Math.min(Math.max(position, 1), rows.length);
        if (to === from) {
            tile.querySelector('.library-position').value = String(to);
            window.AppPanels.announce((tile.dataset.playlistName || 'This playlist') + ' is already at position ' +
                to + ' in ' + label + '.');
            return;
        }
        rows.splice(from - 1, 1);
        rows.splice(to - 1, 0, tile);
        tile.parentElement.insertBefore(tile, rows[to] || null);
        renderPositions(rows, Math.min(from, to), Math.max(from, to));
        if (control && isShown(control)) control.focus();
        window.AppPanels.announce('Moved ' + (tile.dataset.playlistName || 'this playlist') + ' to position ' + to +
            ' in ' + label + '.');
        saveCurrentOrder();
    }

    // Move up and Move down: one place, for the row the control sits in.
    function moveBy(control, step) {
        const tile = control.closest('.card-tile');
        moveTile(tile, sectionTiles(tile.dataset.section).indexOf(tile) + 1 + step, control);
    }

    // Move to position reads the row's field as a number input does (valueAsNumber), taking
    // its whole part. An empty or unreadable entry (NaN) moves nothing: the field is marked
    // and focused, and the range is announced.
    function moveToPosition(control) {
        const tile = control.closest('.card-tile'), field = tile.querySelector('.library-position');
        const wanted = Math.trunc(field.valueAsNumber);
        if (!Number.isFinite(wanted)) {
            field.setAttribute('aria-invalid', 'true');
            window.AppPanels.announce('Enter a position from 1 to ' + sectionTiles(tile.dataset.section).length + '.');
            field.focus();
            return;
        }
        field.removeAttribute('aria-invalid');
        moveTile(tile, wanted, control);
    }

    /* --- HTML5 drag and drop, for pointers, in the sections that keep an order --- */
    function initDragAndDrop() {
        const grids = Object.keys(ORDERED_SECTIONS).map(section => document.getElementById(LIBRARY_GRIDS[section]))
            .filter(Boolean);

        let draggedTile = null;

        grids.forEach(grid => {
            grid.addEventListener('dragstart', (e) => {
                const tile = e.target.closest('.card-tile');
                if (!tile || !organizeMode) return;
                draggedTile = tile;
                tile.classList.add('sh-library-dragging');
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', tile.dataset.playlistId);
            });

            grid.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                const tile = e.target.closest('.card-tile');
                if (tile && tile !== draggedTile) {
                    tile.classList.add('drag-over');
                }
            });

            grid.addEventListener('dragleave', (e) => {
                const tile = e.target.closest('.card-tile');
                if (tile) {
                    tile.classList.remove('drag-over');
                }
            });

            // A row dropped on another row of its own list takes that row's place.
            grid.addEventListener('drop', (e) => {
                e.preventDefault();
                const targetTile = e.target.closest('.card-tile');
                if (!targetTile || !draggedTile || targetTile === draggedTile || !grid.contains(draggedTile)) return;
                targetTile.classList.remove('drag-over');
                // Focus inside the dragged row (a control used before the drag) stays there.
                const held = draggedTile.contains(document.activeElement) ? document.activeElement : null;
                moveTile(draggedTile, sectionTiles(draggedTile.dataset.section).indexOf(targetTile) + 1, held);
            });

            grid.addEventListener('dragend', () => {
                if (draggedTile) {
                    draggedTile.classList.remove('sh-library-dragging');
                    draggedTile = null;
                }
                // A drop refused in the other list leaves its highlight there too.
                grids.forEach(list => list.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over')));
            });
        });
    }

    // Event wiring: inline on* attributes cannot run under the CSP, so each control names
    // a key here and static/js/actions.js dispatches it.
    window.registerActions({
        'dash-001': function () { toggleOrganizeMode(); },
        'dash-002': function () { refreshPlaylists(); },
        'dash-004': function (el) { resetPreferences(el); },
        'dash-005': function () { toggleHiddenSection(); },

        // Row preference controls, shown in Organize mode.
        'card.favorite': function (el) { toggleFavorite(el.dataset.playlistId); },
        'card.hide': function (el) { hidePlaylist(el.dataset.playlistId); },
        'card.unhide': function (el) { unhidePlaylist(el.dataset.playlistId); },
        'card.move-up': function (el) { moveBy(el, -1); },
        'card.move-down': function (el) { moveBy(el, 1); },
        'card.move-to': function (el) { moveToPosition(el); },
        // Enter in the position field moves the row; arrows keep their native steps.
        'card.position-key': function (el, event) {
            if (event.key !== 'Enter') return undefined;
            moveToPosition(el);
            return false;
        },
        // A Spotify change, reviewed first (library-shuffle.js).
        'card.visibility': function (el) { togglePlaylistVisibility(el.dataset.playlistId, el); },
    });

    document.addEventListener('DOMContentLoaded', initDragAndDrop);
}());
