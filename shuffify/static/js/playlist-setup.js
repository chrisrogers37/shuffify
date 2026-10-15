/**
 * Shared playlist setup: the sources, collection and archive that
 * every routine of one playlist shares.
 *
 * Staging never writes. Selecting, typing, staging, Undo, Back, the section links and Refresh my
 * playlists change nothing outside this page; Refresh is a read. Only two requests change
 * anything: Save playlist settings (PUT settings) and Create playlist in Spotify (POST
 * spotify-playlists). Review changes asks for a read-only impact preview and its signed
 * acknowledgment.
 *
 * Save moves Idle, Reviewing, Saving, then Saved, Conflict, Busy, Error or Unknown. Unknown (no
 * readable reply, or any 5xx) is reconciled by reading the setup context: the state digest the
 * review expected means it saved; an unchanged revision is Not confirmed, never "not saved" (the
 * save may still be finishing: Check again stays offered, and saving again needs a new review);
 * anything else takes the Conflict path. A save is never sent again by the page.
 *
 * Creating is one request per deliberate press; Create is marked busy with aria-disabled (it
 * keeps focus) and a second press is refused. An Unknown creation blocks Create for that purpose
 * until a Refresh my playlists started after it (Refresh is refused while creating), which points
 * out a playlist with the requested name and then offers Create another playlist. A created
 * playlist is staged as a link or pair change: only Save uses it, and the page keeps known
 * created playlists in memory for this task only.
 *
 * Every outcome is announced once through AppPanels.announce; status lines are visible text.
 * Names from Spotify or the user are written as text (textContent and DOM builders), never markup.
 */
(function () {
    'use strict';

    const DATA = typeof SETUP_PAGE === 'object' && SETUP_PAGE ? SETUP_PAGE : null;
    if (!DATA) return;
    const URLS = DATA.urls || {};
    const TARGET = DATA.target || {};
    // Each linked playlist's purpose: its link, replace and unlink changes, the staged setting
    // that belongs to its link (a subject), the other purpose, and its label.
    const ASSET = {
        collection: {link: 'link_collection', replace: 'replace_collection', unlink: 'unlink_collection',
            setting: 'drip', other: 'archive', label: 'Collection'},
        archive: {link: 'pair_archive', replace: 'replace_archive', unlink: 'unpair_archive',
            setting: 'auto-archive', other: 'collection', label: 'Archive'},
    };
    const PURPOSES = Object.keys(ASSET);
    const READ_TIMEOUT_MS = 30000;
    // Longer than the server's own Spotify timeouts, so the server's answer normally arrives first.
    const WRITE_TIMEOUT_MS = 120000;
    const MAX_CHANGES = 20;
    const SECTION_NAMES = {sources: 'Sources', collection: 'Collection', archive: 'Archive', routines: 'Routines'};
    const SOURCE_TYPES = {own: 'Your playlist', external: 'Playlist', search_query: 'Search words'};
    const TEXT = {
        saved: 'Settings saved. Existing routines now use these settings. No routine was started.',
        savedNone: 'Settings saved. No existing routines are affected. This does not schedule anything.',
        byReading: 'Confirmed by reading your saved settings.',
        ackAffected: 'I understand these existing routines will use these settings. ' +
            'Saving does not start or schedule any routine.',
        ackNone: 'No existing routines are affected. This does not schedule anything.',
        expired: 'This review expired. Review the changes again.',
        busy: 'A routine or save is running on this playlist. Try again when it finishes.',
        unknownSave: 'Could not confirm settings save.',
        notConfirmed: 'Your changes are not in your saved settings yet. A save may still be finishing. ' +
            'Check again, or review the changes again.',
        changed: 'Settings changed since your review',
        created: 'It stays there if you close setup. Save playlist settings to use it.',
        creationUnknown: 'We could not confirm whether the playlist was created. ' +
            'Check your Spotify playlists before creating another.',
        leaveSaving: "Saving can't be cancelled by leaving; wait for it to finish.",
        leaveCreating: "Creating a playlist can't be cancelled by leaving; wait for it to finish.",
        discardTitle: 'Discard unsaved settings changes?',
        maySaved: 'Your settings may already be saved.',
        pageOnly: 'These details are kept only on this page.',
    };

    const state = {
        context: DATA.context,
        changes: [],     // staged changes: {key, subject, change}, one per subject, in staging order
        stale: [],       // staged changes that no longer apply after a conflict: {key, label, reason}
        errors: {},      // server refusals by staged change key: {message, field}
        inputErrors: {}, // field id -> {message, value, count}: a value this page refused before
                         // staging. A refused count stands for a staged value and holds the review;
                         // an add, create or select error never does, and editing its field clears it.
        review: null,    // {revision, entries, reply}; cleared by any change to the staged list
        phase: 'idle',   // idle | reviewing | saving | unknown | checking (reading the saved settings:
                         // an unconfirmed save's check, the read after a reply, or Check again)
        unknown: null,   // the review an unconfirmed save was sent with, until a save is confirmed
                         // or settings that changed since are adopted (it may still be finishing)
        replacing: {collection: false, archive: false},
        playlists: null, // the owned and collaborative playlists (setup/playlists), once read
        playlistsState: 'idle', // idle | loading | failed | loaded
        playlistsVersion: 0,
        created: [],     // playlists created here, or found and used after an Unknown creation:
                         // {purpose, id, name, saved}
        creation: {collection: freshCreation(), archive: freshCreation()},
        status: '',
    };
    let changeSeq = 0, playlistToken = 0, unknownEpisodes = 0;

    function freshCreation() {
        // createdId: the playlist the status line reports as created here. episode numbers each
        // Unknown outcome, so only a Refresh started after it counts as its check.
        return {busy: false, unknown: false, refreshed: false, episode: 0, requested: '', found: null, status: '',
            createdId: null};
    }

    // ---- DOM helpers: text only ----
    const byId = id => document.getElementById(id);
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }
    function attrs(node, values) {
        Object.keys(values).forEach(name => {
            if (values[name] !== null && values[name] !== undefined) node.setAttribute(name, String(values[name]));
        });
        return node;
    }
    function button(text, values, className) {
        return attrs(el('button', className || 'sh-button-secondary', text), Object.assign({type: 'button'}, values));
    }
    // A control whose visible words are shared by many rows, named for its row.
    function namedButton(words, name, values) {
        const control = button(words, values);
        control.appendChild(el('span', 'sr-only', ': ' + name));
        return control;
    }
    function fill(list, items) {
        list.replaceChildren(...items);
        return list;
    }
    // Appends parts with a space between them, so their words never run together.
    function spaced(node, parts) {
        parts.forEach((part, index) => {
            if (index) node.append(' ');
            node.append(part);
        });
        return node;
    }
    // Keys are made by this page from letters, digits and dashes.
    function focusKey(key) {
        return document.querySelector('[data-setup-focus="' + key + '"]');
    }
    function shown(node) {
        return Boolean(node) && node.isConnected && node.getClientRects().length > 0 && !node.closest('[hidden]');
    }
    // Focus the first candidate shown, else the control used, else its section (its
    // [data-panel-return] region, found before the control was rebuilt).
    function focusFirst(candidates, trigger, region) {
        const target = candidates.find(shown);
        if (target) {
            target.focus();
            return;
        }
        if (!window.AppPanels) return;
        if (trigger && trigger.isConnected) window.AppPanels.focusNear(trigger);
        else if (region) window.AppPanels.focusNear(region);
    }
    function say(message) {
        if (message && window.AppPanels) window.AppPanels.announce(message);
    }
    function plural(count, one, many) {
        return count + ' ' + (count === 1 ? one : many);
    }
    function nameList(names) {
        if (names.length < 2) return names.join('');
        return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
    }

    // ---- Requests: AppRequest (the CSRF fetch wrapper) with this page's time limits ----
    // unknown: no readable reply, or any 5xx (the server could not say what happened); it is
    // never "failed". Reads are never served from a cache.
    function send(url, options) {
        const limits = options && options.method ? {timeoutMs: WRITE_TIMEOUT_MS} :
            {method: 'GET', cache: 'no-store', timeoutMs: READ_TIMEOUT_MS};
        return window.AppRequest.send(url, Object.assign(limits, options))
            .then(result => Object.assign(result, {unknown: !result.answered || result.status >= 500}));
    }
    function contextFrom(data) {
        const candidate = data && data.context && typeof data.context === 'object' ? data.context : data;
        return validContext(candidate) ? candidate : null;
    }
    function validContext(candidate) {
        return Boolean(candidate) && typeof candidate === 'object' &&
            typeof candidate.configuration_revision === 'string' &&
            Array.isArray(candidate.sources) && Array.isArray(candidate.routines);
    }
    function readContext() {
        return send(URLS.context).then(result => (result.ok ? contextFrom(result.data) : null));
    }
    // The settings a reply carried, else a read of them. Until that read settles the phase is
    // 'checking', never idle: the reply's review (still shown, still acknowledged) cannot be sent
    // again, and nothing is staged on settings about to be replaced. The caller then renders once.
    async function settingsAfter(carried) {
        if (carried) return carried;
        state.phase = 'checking';
        const fresh = await readContext();
        state.phase = 'idle';
        return fresh;
    }

    // ---- Saved and staged settings ----
    const limits = () => state.context.limits || {};
    const maxSources = () => Number(limits().max_sources) || 10;
    const range = (name, low, high) => {
        const pair = Array.isArray(limits()[name]) ? limits()[name] : [low, high];
        return {low: Number(pair[0]) || low, high: Number(pair[1]) || high};
    };
    function subjectOf(change) {
        switch (change.op) {
        case 'add_playlist_source': return 'source:' + change.playlist_id;
        case 'add_search_source': return 'search:' + change.search_query;
        case 'remove_source':
        case 'set_source_count': return 'source-id:' + change.source_id;
        case 'remove_routine_source': return 'routine-source:' + change.playlist_id;
        case 'set_drip': return ASSET.collection.setting;
        case 'set_auto_archive': return ASSET.archive.setting;
        // A link, replace or unlink: the purpose it is for.
        default: return PURPOSES.find(purpose => [ASSET[purpose].link, ASSET[purpose].replace,
            ASSET[purpose].unlink].includes(change.op));
        }
    }
    const staged = subject => state.changes.find(entry => entry.subject === subject) || null;
    const savedSource = id => state.context.sources.find(source => source.id === id) || null;
    function sourceName(source) {
        if (!source) return 'a source';
        return source.name || (source.search_query ? '“' + source.search_query + '”' : source.playlist_id);
    }
    function playlistName(id) {
        const known = (state.playlists || []).find(playlist => playlist.id === id) ||
            state.created.find(made => made.id === id);
        if (known && known.name) return known.name;
        for (const purpose of PURPOSES) {
            const saved = state.context[purpose];
            if (saved && saved.playlist_id === id && saved.name) return saved.name;
        }
        const source = state.context.sources.find(item => item.playlist_id === id);
        return source && source.name ? source.name : id;
    }
    const assetName = purpose => {
        const saved = state.context[purpose];
        return saved ? saved.name || saved.playlist_id : '';
    };
    // A source this page would add: its search words, or its playlist's name. A playlist added by
    // its link is named by the review, which reads it from Spotify; until then it is never shown
    // or read out as its 22-character ID.
    function addedSourceName(change) {
        if (change.op === 'add_search_source') return '“' + change.search_query + '” (search)';
        const name = playlistName(change.playlist_id);
        return name === change.playlist_id ? 'a playlist from a link (named when you review)' : name + ' (playlist)';
    }
    // The one wording of each change: the server's normalized summaries use the same words
    // (tests/fixtures/setup_change_summaries.json holds them for both sides).
    function describe(change) {
        switch (change.op) {
        case 'add_playlist_source':
        case 'add_search_source': return 'Add source: ' + addedSourceName(change);
        case 'remove_source': return 'Remove source: ' + sourceName(savedSource(change.source_id));
        case 'set_source_count': {
            const source = savedSource(change.source_id);
            return sourceName(source) + ': collect ' + change.raid_count + ' per run' +
                (source ? ' (was ' + source.raid_count + ')' : '');
        }
        case 'remove_routine_source': return 'Stop routines collecting from ' + change.playlist_id;
        case 'set_drip': {
            const parts = [];
            if ('drip_enabled' in change) {
                parts.push(change.drip_enabled ? 'add collected tracks automatically' :
                    'stop adding collected tracks automatically');
            }
            if ('drip_count' in change) parts.push('add ' + change.drip_count + ' per run');
            return 'Collection: ' + parts.join(', ');
        }
        case 'set_auto_archive': return change.value ? 'Archive: auto-archive removed tracks' :
            'Archive: stop auto-archiving removed tracks';
        default: {
            const purpose = subjectOf(change), asset = ASSET[purpose];
            if (change.op === asset.unlink) return asset.label + ': unlink ' + assetName(purpose);
            const name = playlistName(change.playlist_id);
            return asset.label + (change.op === asset.replace ? ': replace ' + assetName(purpose) + ' with ' + name :
                ': use ' + name);
        }
        }
    }
    // The collection or archive this page would save: {playlist_id, name, pending} or null.
    function effectiveAsset(purpose) {
        const saved = state.context[purpose], entry = staged(purpose);
        if (!entry) return saved ? {playlist_id: saved.playlist_id, name: saved.name, pending: null} : null;
        if (entry.change.op === ASSET[purpose].unlink) return null;
        return {playlist_id: entry.change.playlist_id, name: playlistName(entry.change.playlist_id),
            pending: entry.change.op};
    }
    function dripBase() {
        const saved = state.context.collection;
        return saved ? {drip_enabled: Boolean(saved.drip_enabled), drip_count: Number(saved.drip_count)} :
            {drip_enabled: false, drip_count: 3};
    }
    function dripEffective() {
        const entry = staged('drip'), values = dripBase();
        if (entry) {
            if ('drip_enabled' in entry.change) values.drip_enabled = entry.change.drip_enabled;
            if ('drip_count' in entry.change) values.drip_count = entry.change.drip_count;
        }
        return values;
    }
    function autoArchiveEffective() {
        const entry = staged('auto-archive'), saved = state.context.archive;
        if (entry) return entry.change.value;
        return saved ? Boolean(saved.auto_archive_on_remove) : true;
    }
    function sourceCount() {
        const removed = state.changes.filter(entry => entry.change.op === 'remove_source').length;
        const added = state.changes.filter(entry => /^add_/.test(entry.change.op)).length;
        return state.context.sources.length - removed + added;
    }

    // ---- What the page lets the person do now ----
    const anyCreating = () => PURPOSES.some(purpose => state.creation[purpose].busy);
    function editRefusal() {
        if (state.phase === 'reviewing') return 'Wait for the review to finish.';
        if (state.phase === 'saving') return 'Wait for the save to finish.';
        // An unconfirmed save: only reading the saved settings tells what it did.
        if (state.phase === 'unknown' || (state.phase === 'checking' && state.unknown)) {
            return 'Check your saved settings before changing anything.';
        }
        // The saved settings are being read after a reply (or Check again) and replace what is shown.
        if (state.phase === 'checking') return 'Wait for your saved settings to load.';
        return '';
    }
    function refuse(message) {
        setStatus(message);
        say(message);
        renderFooter();
        return false;
    }
    // A staging handler, refused with its reason while what is staged cannot change
    // (editRefusal). revert: the control already shows the refused value (a typed count, a
    // toggled box), so the page renders again to put it back. A count can be committed with focus
    // still in its field (Enter, the spinner), and render keeps a focused field's text: the field
    // gets back the count the page holds first.
    function guarded(handler, options) {
        return function (control) {
            const reason = editRefusal();
            if (!reason) return handler(control);
            refuse(reason);
            if (options && options.revert) {
                if (control.type === 'number') control.value = heldCount(control);
                render();
            }
            return false;
        };
    }
    function saveRefusal() {
        const blocked = editRefusal();
        if (blocked) return blocked;
        if (anyCreating()) return 'Wait for the playlist to be created.';
        if (!state.changes.length) return 'There are no pending changes to save.';
        if (!state.review) return 'Review the changes first.';
        if (!byId('setup-ack').checked) return 'Check the acknowledgment in the review first.';
        return '';
    }

    // ---- Staging (page memory only) ----
    function stage(change) {
        const subject = subjectOf(change), index = state.changes.findIndex(entry => entry.subject === subject);
        const entry = {key: 'c' + (++changeSeq), subject: subject, change: change};
        if (index >= 0) {
            delete state.errors[state.changes[index].key];
            state.changes[index] = entry;
        } else {
            state.changes.push(entry);
        }
        stagedListChanged();
        return entry;
    }
    function unstage(subject) {
        const entry = staged(subject);
        if (!entry) return;
        delete state.errors[entry.key];
        state.changes = state.changes.filter(item => item !== entry);
        stagedListChanged();
    }
    // A different staged list needs a new review (so a review always matches the list it was
    // made from); an old outcome no longer describes the page.
    function stagedListChanged() {
        clearReview();
        clearOutcome();
        state.status = '';
    }
    // count: the refused value is a count (a source's or Drip's), which holds the review.
    function setInputError(id, message, value, count) {
        if (message) state.inputErrors[id] = {message: message, value: value, count: Boolean(count)};
        else delete state.inputErrors[id];
    }
    const inputError = id => (state.inputErrors[id] ? state.inputErrors[id].message : '');
    // Editing a field whose add, create or choice was refused clears that refusal. A refused
    // count stays until a valid count is committed.
    function edited(field) {
        const error = state.inputErrors[field.id];
        if (!error || error.count) return;
        setInputError(field.id, '');
        render();
    }
    // A playlist is staged for purpose: what its chooser refused before no longer applies.
    function chosen(purpose) {
        setInputError('setup-' + purpose + '-select', '');
        setInputError('setup-' + purpose + '-create-name', '');
    }

    function parsePlaylistId(text) {
        const value = text.trim();
        if (/^[a-zA-Z0-9]{22}$/.test(value)) return value;
        const patterns = [
            /^(?:https?:\/\/)?open\.spotify\.com(?:\/intl-[a-z]{2}(?:-[a-zA-Z]{2})?)?\/playlist\/([a-zA-Z0-9]{22})\/?(?:\?.*)?$/,
            /^(?:https?:\/\/)?open\.spotify\.com\/embed\/playlist\/([a-zA-Z0-9]{22})\/?(?:\?.*)?$/,
            /^spotify:playlist:([a-zA-Z0-9]{22})$/,
        ];
        for (const pattern of patterns) {
            const match = pattern.exec(value);
            if (match) return match[1];
        }
        return null;
    }
    function sourceLimitMessage() {
        return 'A playlist can have at most ' + maxSources() + ' sources';
    }
    // Stages change from a staging field, or marks the field with the reason it cannot be
    // staged and keeps focus there.
    function stageFromField(field, message, change) {
        setInputError(field.id, message);
        if (!message) {
            const entry = stage(change);
            field.value = '';
            say(describe(entry.change) + ' is pending.');
        }
        render();
        if (message) field.focus();
        return false;
    }
    // The whole number a count field holds within bounds, or null after marking the field (which
    // keeps what was typed beside its error).
    function wholeNumber(field, bounds) {
        const text = field.value.trim(), count = Number(text);
        const valid = /^\d+$/.test(text) && count >= bounds.low && count <= bounds.high;
        setInputError(field.id, valid ? '' : 'Enter a whole number from ' + bounds.low + ' to ' + bounds.high + '.',
            field.value, true);
        return valid ? count : null;
    }
    // The text a count field (a source's or the shared Drip count) shows for what the page holds:
    // a refused value beside its error, else the staged or saved count.
    function heldCount(field) {
        const refused = state.inputErrors[field.id];
        if (refused) return refused.value;
        if (field.id === 'setup-drip-count') return String(dripEffective().drip_count);
        const id = Number(field.getAttribute('data-source-id')), entry = staged('source-id:' + id);
        if (entry && entry.change.op === 'set_source_count') return String(entry.change.raid_count);
        const source = savedSource(id);
        return source ? String(source.raid_count) : '';
    }
    function addPlaylistSource() {
        const field = byId('setup-add-playlist-input'), id = parsePlaylistId(field.value);
        let message = '';
        if (!field.value.trim()) message = 'Enter a Spotify playlist link or ID.';
        else if (!id) message = 'Enter a Spotify playlist link, URI or ID.';
        else if (id === TARGET.id) message = 'This is the playlist being set up';
        else if (state.context.sources.some(source => source.playlist_id === id) ||
            staged('source:' + id)) message = 'Already a source';
        else if (sourceCount() >= maxSources()) message = sourceLimitMessage();
        return stageFromField(field, message, {op: 'add_playlist_source', playlist_id: id});
    }
    function addSearchSource() {
        const field = byId('setup-add-search-input');
        const query = field.value.trim();
        let message = '';
        if (!query) message = 'Enter search words.';
        else if (query.length > 500) message = 'Use at most 500 characters.';
        else if (state.context.sources.some(source => source.search_query === query) ||
            staged('search:' + query)) message = 'Already a source';
        else if (sourceCount() >= maxSources()) message = sourceLimitMessage();
        return stageFromField(field, message, {op: 'add_search_source', search_query: query});
    }
    function removeSource(control) {
        const id = Number(control.getAttribute('data-source-id'));
        if (!savedSource(id)) return false;
        setInputError(countFieldId(id), '');
        stage({op: 'remove_source', source_id: id});
        render({keys: ['source-undo-' + id], trigger: control});
        return false;
    }
    const countFieldId = id => 'setup-source-count-' + id;
    function setSourceCount(field) {
        const id = Number(field.getAttribute('data-source-id')), source = savedSource(id);
        if (!source) return;
        const count = wholeNumber(field, range('raid_count', 1, 100));
        if (count !== null) {
            if (count === Number(source.raid_count)) unstage('source-id:' + id);
            else stage({op: 'set_source_count', source_id: id, raid_count: count});
        }
        // The row is rebuilt: wait until the browser has moved focus (Tab fires change first),
        // then render puts focus back on the same control in the rebuilt row.
        setTimeout(function () { render(); }, 0);
    }
    function removeRoutineSource(control) {
        const id = control.getAttribute('data-playlist-id');
        stage({op: 'remove_routine_source', playlist_id: id});
        render({keys: [control.getAttribute('data-setup-focus').replace(/-remove$/, '-undo')], trigger: control});
        return false;
    }
    function undo(control) {
        const key = control.getAttribute('data-setup-undo');
        const entry = state.changes.find(item => item.key === key);
        if (!entry) return false;
        const list = control.closest('#setup-pending-list');
        const keys = [control.getAttribute('data-setup-counterpart')]
            .concat(list ? neighborUndos(list, control, '[data-setup-undo]') : []);
        unstage(entry.subject);
        // Drip and auto-archive belong to a link: undoing a new one leaves them nothing to apply to.
        if (PURPOSES.includes(entry.subject) && !effectiveAsset(entry.subject)) {
            unstage(ASSET[entry.subject].setting);
        }
        render({keys: keys.filter(Boolean), trigger: control});
        return false;
    }
    // In a list of Undo controls (pending changes, changes that no longer apply), focus moves to
    // the next one still shown, else the previous one: their keys, in that order.
    function neighborUndos(list, control, selector) {
        const items = Array.from(list.querySelectorAll(selector)), index = items.indexOf(control);
        return items.slice(index + 1).concat(items.slice(0, index).reverse())
            .map(item => item.getAttribute('data-setup-focus'));
    }
    function dismissStale(control) {
        const key = control.getAttribute('data-setup-stale');
        const keys = neighborUndos(byId('setup-stale-list'), control, '[data-setup-stale]');
        state.stale = state.stale.filter(item => item.key !== key);
        render({keys: keys, trigger: control});
        return false;
    }

    // Replacing or unlinking keeps the saved link until Save; nothing is replaced silently.
    function toggleReplace(control) {
        const purpose = control.getAttribute('data-purpose');
        state.replacing[purpose] = !state.replacing[purpose];
        if (state.replacing[purpose]) ensurePlaylists();
        render({keys: [purpose + '-replace'], trigger: control});
        return false;
    }
    function unlink(control) {
        const purpose = control.getAttribute('data-purpose');
        if (!state.context[purpose]) return false;
        state.replacing[purpose] = false;
        stage({op: ASSET[purpose].unlink});
        // Drip and auto-archive belong to the link being removed.
        unstage(ASSET[purpose].setting);
        render({keys: [purpose + '-undo'], trigger: control});
        return false;
    }
    function useExisting(control) {
        const purpose = control.getAttribute('data-purpose');
        const select = byId('setup-' + purpose + '-select');
        return stageAsset(purpose, select.value, select, control);
    }
    function useFound(control) {
        const purpose = control.getAttribute('data-purpose'), found = state.creation[purpose].found;
        if (!found) return false;
        return stageAsset(purpose, found.id, null, control);
    }
    function stageAsset(purpose, id, select, control) {
        if (state.creation[purpose].busy) return refuse('Wait for the playlist to be created.');
        const other = effectiveAsset(ASSET[purpose].other);
        let message = '';
        if (!id) message = 'Choose a playlist first.';
        else if (id === TARGET.id) message = 'This is the playlist being set up';
        else if (other && other.playlist_id === id) message = 'The collection and archive must be different playlists';
        if (message) {
            if (select) setInputError(select.id, message, id);
            render();
            if (select) select.focus();
            else refuse(message);
            return false;
        }
        stage({op: state.context[purpose] ? ASSET[purpose].replace : ASSET[purpose].link, playlist_id: id});
        chosen(purpose);
        state.replacing[purpose] = false;
        const found = state.creation[purpose].found;
        if (found && found.id === id) {
            // Most likely the playlist the Unknown creation made: kept like one made here, so
            // leaving still names it (also once this link is undone).
            remember(purpose, found);
            state.creation[purpose] = freshCreation();
        }
        // The chooser closes: focus goes to its section (AppPanels.focusNear).
        render({trigger: control});
        return false;
    }
    function setDrip(field) {
        const base = dripBase(), entry = staged('drip');
        const values = entry ? Object.assign({}, entry.change) : {};
        if (field.id === 'setup-drip-enabled') {
            values.drip_enabled = field.checked;
        } else {
            const count = wholeNumber(field, range('drip_count', 1, 50));
            if (count === null) {
                render();
                return;
            }
            values.drip_count = count;
        }
        const change = {op: 'set_drip'};
        if ('drip_enabled' in values && values.drip_enabled !== base.drip_enabled) change.drip_enabled = values.drip_enabled;
        if ('drip_count' in values && values.drip_count !== base.drip_count) change.drip_count = values.drip_count;
        if (Object.keys(change).length > 1) stage(change);
        else unstage('drip');
        render();
    }
    function setAutoArchive(field) {
        const saved = state.context.archive;
        const base = saved ? Boolean(saved.auto_archive_on_remove) : true;
        if (field.checked === base) unstage('auto-archive');
        else stage({op: 'set_auto_archive', value: field.checked});
        render();
    }

    // ---- Owned playlists (setup/playlists): Use existing playlist and reconciliation ----
    // The first chooser shown reads the list once; after a failure only Refresh reads again.
    function ensurePlaylists() {
        if (state.playlistsState === 'idle') loadPlaylists(false);
    }
    // Reads the owned playlists: {list}, the list or null when the read failed, or {superseded:
    // true} when a later read replaced this one (only the latest read's outcome is shown or said).
    async function loadPlaylists(refresh) {
        const token = ++playlistToken;
        state.playlistsState = 'loading';
        renderPickers();
        const separator = String(URLS.playlists).indexOf('?') >= 0 ? '&' : '?';
        const result = await send(URLS.playlists + (refresh ? separator + 'refresh=1' : ''));
        if (token !== playlistToken) return {superseded: true};
        if (!result.ok || !Array.isArray(result.data.playlists)) {
            state.playlistsState = 'failed';
            renderPickers();
            return {list: null};
        }
        state.playlists = result.data.playlists.filter(item => item && typeof item.id === 'string');
        state.playlistsState = 'loaded';
        state.playlistsVersion += 1;
        renderPickers();
        return {list: ownedPlaylists()};
    }
    // The person's playlists as last read, topped by any created here that Spotify's list does
    // not show yet (newest first); null until read.
    function ownedPlaylists() {
        if (!state.playlists) return null;
        const listed = state.playlists;
        return state.created.filter(made => !listed.some(item => item.id === made.id)).reverse()
            .map(made => ({id: made.id, name: made.name, track_count: 0, owned: true})).concat(listed);
    }
    async function refreshPlaylists(control) {
        const purpose = control.getAttribute('data-purpose');
        // A list read while this purpose's playlist is being created could predate it, so it is
        // refused; and only a read started after an Unknown creation is that creation's check.
        if (state.creation[purpose].busy) return refuse('Wait for the playlist to be created.');
        const episode = state.creation[purpose].unknown ? state.creation[purpose].episode : 0;
        const read = await loadPlaylists(true);
        if (read.superseded) return;
        const creation = state.creation[purpose], list = read.list;
        if (!list) {
            say('Could not load your playlists. Try Refresh my playlists again.');
            render();
            return;
        }
        if (creation.unknown && creation.episode === episode) {
            creation.refreshed = true;
            creation.found = list.find(item => item.id !== TARGET.id && item.name === creation.requested) || null;
            say(creation.found ? 'Found ' + creation.found.name + ' — use it?' :
                'No playlist named ' + creation.requested + ' was found. You can create another playlist.');
        } else {
            say('Your playlists were refreshed.');
        }
        render();
    }

    // ---- Create playlist in Spotify ----
    async function createPlaylist(control, another) {
        const purpose = control.getAttribute('data-purpose'), creation = state.creation[purpose];
        const trigger = another ? control : byId('setup-' + purpose + '-create');
        // A second press while creating is refused: one request per deliberate press.
        if (creation.busy) return false;
        if (editRefusal()) return refuse(editRefusal());
        if (state.context[purpose] || staged(purpose)) return false;
        if (creation.unknown && !creation.refreshed) return createRefusal(purpose, TEXT.creationUnknown);
        if (creation.unknown && !another) {
            return createRefusal(purpose, 'Press Create another playlist to create one more.');
        }
        const field = byId('setup-' + purpose + '-create-name'), name = field.value.trim();
        const message = !name ? 'Enter a name for the new playlist.' :
            name.length > 100 ? 'Use at most 100 characters.' : '';
        setInputError(field.id, message, field.value);
        if (message) {
            render();
            field.focus();
            return false;
        }
        Object.assign(creation, {busy: true, requested: name, found: null, createdId: null,
            status: 'Creating ' + name + ' in your Spotify library…'});
        render();
        const result = await send(URLS.create, {method: 'POST', queued: true, newIntent: another === true, json: {purpose: purpose, name: name}});
        creation.busy = false;
        const data = result.data;
        creation.operationId = result.operationId || null;
        if ((result.ok || result.answered) && data.outcome === 'created' && data.playlist &&
                typeof data.playlist.id === 'string') {
            return created(purpose, data, name, trigger);
        }
        if (result.unknown) return creationUnknown(purpose, name);
        if (result.status < 300) return creationUnknown(purpose, name);
        // A refusal is read from its body: another layer's 400 or 429 (the CSRF check, the
        // app's rate limit) is neither a name error nor Spotify being busy.
        if (data.outcome === 'rate_limited') {
            const seconds = result.retryAfter;
            return createRefusal(purpose, 'Spotify is busy. Try again in ' +
                (seconds ? plural(seconds, 'second', 'seconds') : 'a moment') + '.');
        }
        if (data.error_code === 'already_linked' || data.error_code === 'already_paired') {
            // The refusal means another page changed these settings. Keep edits and the old
            // review blocked until the saved context has been read and adopted (or the read fails).
            state.phase = 'checking';
            creation.status = 'This playlist already has a ' + purpose + ' playlist. Loading your saved settings…';
            render();
            const fresh = await readContext();
            state.phase = 'idle';
            if (fresh) {
                adoptContext(fresh);
                createRefusal(purpose, data.message || 'This playlist already has one. Its saved settings are shown.');
            } else {
                // The 409 proves the old review is stale even when the replacement read fails.
                clearReview();
                createRefusal(purpose, 'This playlist already has a ' + purpose + ' playlist. ' +
                    'Could not load your saved settings. Reload the page to show them.');
            }
            render({trigger: trigger});
            return false;
        }
        if (data.error_code === 'invalid_request') {
            creation.status = '';
            setInputError(field.id, data.message || 'Check the name and try again.', field.value);
            render();
            field.focus();
            return false;
        }
        const reason = typeof data.reason === 'string' && data.reason ? data.reason : '';
        return createRefusal(purpose, data.message || reason || 'Spotify did not create the playlist.');
    }
    function createRefusal(purpose, message) {
        state.creation[purpose].status = message;
        say(message);
        render();
        return false;
    }
    function creationUnknown(purpose, name) {
        Object.assign(state.creation[purpose], {unknown: true, refreshed: false, episode: ++unknownEpisodes,
            requested: name, found: null, status: TEXT.creationUnknown + (state.creation[purpose].operationId ?
                ' Accepted operation: ' + state.creation[purpose].operationId + '.' : '')});
        say(state.creation[purpose].status);
        render();
        return false;
    }
    // Keeps a playlist made here (or found after an Unknown creation) in page memory, once: leaving
    // names it until it is saved as the collection or archive.
    function remember(purpose, playlist) {
        if (state.created.some(made => made.id === playlist.id)) return;
        state.created.push({purpose: purpose, id: playlist.id, name: playlist.name, saved: false});
    }
    function created(purpose, data, requested, trigger) {
        const playlist = {id: data.playlist.id, name: data.playlist.name || requested};
        remember(purpose, playlist);
        // The choosers list it from now on.
        state.playlistsVersion += 1;
        state.creation[purpose] = freshCreation();
        byId('setup-' + purpose + '-create-name').value = '';
        stage({op: ASSET[purpose].link, playlist_id: playlist.id});
        chosen(purpose);
        let message = 'Created ' + playlist.name + ' in your Spotify library. ' + TEXT.created;
        if (data.privacy_confirmed === false) message += ' Spotify did not confirm that it is private.';
        Object.assign(state.creation[purpose], {status: message, createdId: playlist.id});
        say(message);
        // Create is gone (the playlist is now the staged link): focus goes to its section.
        render({trigger: trigger});
        return false;
    }

    // ---- Review changes (read-only impact preview) ----
    function clearReview() {
        state.review = null;
        const ack = byId('setup-ack');
        if (ack) ack.checked = false;
    }
    // The section digests of the settings this page shows: from them the server
    // names a conflict's changed sections. Left out when the settings carry no full set.
    function sectionDigests() {
        const digests = state.context.section_digests;
        return digests && Object.keys(SECTION_NAMES).every(name => typeof digests[name] === 'string') ?
            digests : undefined;
    }
    // The first refused count shown: it stands for a staged value, so the review waits for it. An
    // add, create or select error is about a value never staged and does not hold the review.
    function firstCountError() {
        const id = Object.keys(state.inputErrors).find(key => state.inputErrors[key].count && shown(byId(key)));
        return id ? byId(id) : null;
    }
    async function reviewChanges(control) {
        if (state.phase === 'reviewing') return false;
        if (editRefusal()) return refuse(editRefusal());
        if (anyCreating()) return refuse('Wait for the playlist to be created.');
        if (!state.changes.length) return refuse('There are no pending changes to review.');
        const invalid = firstCountError();
        if (invalid) {
            invalid.focus();
            return refuse('Fix the marked field first.');
        }
        if (state.changes.length > MAX_CHANGES) return refuse('Review at most ' + MAX_CHANGES + ' changes at a time.');
        const entries = state.changes.slice();
        const body = {configuration_revision: state.context.configuration_revision,
            changes: entries.map(entry => entry.change), section_digests: sectionDigests()};
        state.phase = 'reviewing';
        clearOutcome();
        setStatus('Reviewing…');
        render();
        const result = await send(URLS.impact, {method: 'POST', json: body});
        state.phase = 'idle';
        const data = result.data;
        if (result.ok && data.acknowledgment && typeof data.acknowledgment === 'object') {
            state.review = {revision: body.configuration_revision, entries: entries, reply: data};
            state.errors = {};
            showReview();
            const affected = (data.affected_routines || []).length;
            const message = 'Review ready. ' + (affected ? plural(affected, 'existing routine is', 'existing routines are') +
                ' affected.' : TEXT.ackNone);
            setStatus('Review ready.');
            say(message);
            render();
            byId('setup-review-heading').focus();
            return false;
        }
        if (result.status === 409 && data.error_code === 'configuration_changed') {
            return conflict(await settingsAfter(contextFrom(data)), data.changed_sections);
        }
        if (result.status === 400 && data.error_code === 'invalid_changes') return refusedChanges(data.errors, entries);
        const message = !result.unknown && data.message ? data.message :
            'The review could not be loaded. Nothing was saved. Try Review changes again.';
        return outcome('Could not review the changes', message, 'Could not review the changes.');
    }
    function showReview() {
        const reply = state.review.reply;
        const items = (reply.changes || []).map((change, index) => el('li', 'sh-detail',
            change && change.summary ? change.summary : describe(state.review.entries[index].change)));
        fill(byId('setup-review-changes'), items);
        const affected = (reply.affected_routines || []).map(routine => el('li', 'sh-detail',
            routineLine(routine) + ((routine.effects || []).length ? ' — ' + routine.effects.join('; ') : '')));
        const unaffected = (reply.unaffected_routines || []).map(routine => el('li', 'sh-detail',
            routine.operation + ' (#' + routine.id + ') — ' + (routine.reason || 'not affected')));
        const ackText = affected.length ? TEXT.ackAffected : TEXT.ackNone;
        const notes = (reply.notes || []).filter(note => note && note !== ackText).map(note => el('li', 'sh-detail', note));
        [['setup-review-affected', affected], ['setup-review-unaffected', unaffected]].forEach(pair => {
            fill(byId(pair[0]), pair[1]).hidden = !pair[1].length;
            byId(pair[0] + '-heading').hidden = !pair[1].length;
        });
        fill(byId('setup-review-notes'), notes).hidden = !notes.length;
        byId('setup-ack-text').textContent = ackText;
        byId('setup-ack').checked = false;
    }
    function routineLine(routine) {
        return routine.operation + ' · ' + routine.timing_text + ' (#' + routine.id + ') — ' +
            (routine.is_enabled ? 'Active' : 'Paused');
    }

    // ---- Save playlist settings ----
    async function saveSettings(control) {
        // A second press while saving is refused: the page sends one save per press.
        if (state.phase === 'saving') return false;
        const reason = saveRefusal();
        if (reason) return refuse(reason);
        const review = state.review;
        state.phase = 'saving';
        clearOutcome();
        setStatus('Saving…');
        render();
        const result = await send(URLS.settings, {method: 'PUT', json: {
            configuration_revision: review.revision,
            changes: review.entries.map(entry => entry.change),
            acknowledgment: review.reply.acknowledgment,
            section_digests: sectionDigests()}});
        state.phase = 'idle';
        const data = result.data;
        if (result.unknown) return saveUnknown(review);
        if (result.ok && data.saved === true) {
            const affected = Array.isArray(data.affected_schedule_ids) ? data.affected_schedule_ids.length :
                (review.reply.affected_routines || []).length;
            return saved(await settingsAfter(contextFrom(data.context)), affected, false);
        }
        if (result.status < 300) return saveUnknown(review);
        if (result.status === 409 && data.error_code === 'configuration_changed') {
            return conflict(await settingsAfter(contextFrom(data)), data.changed_sections);
        }
        if (result.status === 409 && data.error_code === 'acknowledgment_invalid') {
            clearReview();
            return outcome('Review expired', TEXT.expired, 'This review expired.');
        }
        if (result.status === 409 && data.error_code === 'playlist_busy') {
            return outcome('Not saved yet', TEXT.busy, TEXT.busy);
        }
        if (result.status === 400 && data.error_code === 'invalid_changes') {
            return refusedChanges(data.errors, review.entries);
        }
        return outcome('Not saved', data.message || 'The settings were not saved.', 'Not saved.');
    }
    // Shows an outcome and the status line, announces it once and renders. options: list (the
    // lines under it), checkAgain (offers Check again) and spoken (other words to announce).
    function outcome(heading, message, status, options) {
        options = options || {};
        showOutcome(heading, message, options);
        setStatus(status);
        say(options.spoken || message);
        render();
        return false;
    }
    // Focus goes to the outcome heading after Saved, and after each outcome of an unconfirmed
    // save's check or of Check again, which can hide the Check again that was used.
    function focusOutcome() {
        byId('setup-outcome-heading').focus();
        return false;
    }
    function saved(fresh, affected, byReading) {
        // A confirmed save settles an earlier unconfirmed one too: that one held the lock first.
        state.unknown = null;
        if (fresh) adoptSaved(fresh);
        else {
            state.changes = [];
            state.stale = [];
            state.errors = {};
            clearReview();
        }
        let message = affected ? TEXT.saved : TEXT.savedNone;
        if (byReading) message += ' ' + TEXT.byReading;
        if (!fresh) message += ' This page could not load the saved settings. Check again to show them.';
        outcome('Settings saved', message, 'Settings saved.', {checkAgain: !fresh});
        return focusOutcome();
    }
    function adoptSaved(fresh) {
        state.context = fresh;
        state.changes = [];
        state.stale = [];
        state.errors = {};
        state.inputErrors = {};
        state.replacing = {collection: false, archive: false};
        clearReview();
        markSavedPlaylists();
    }
    function markSavedPlaylists() {
        state.created.forEach(made => {
            const asset = state.context[made.purpose];
            if (asset && asset.playlist_id === made.id) {
                made.saved = true;
                if (state.creation[made.purpose].createdId === made.id) state.creation[made.purpose].status = '';
            }
        });
    }
    function saveUnknown(review) {
        state.phase = 'unknown';
        state.unknown = review;
        outcome('Could not confirm settings save', TEXT.unknownSave + ' Checking your saved settings…',
            TEXT.unknownSave, {spoken: TEXT.unknownSave});
        return reconcile();
    }
    // Reads the saved settings to learn what an unconfirmed save did. It never saves again. Each
    // outcome focuses the outcome heading (focusOutcome).
    async function reconcile() {
        const review = state.unknown;
        if (!review) return false;
        state.phase = 'checking';
        render();
        const fresh = await readContext();
        if (!fresh) {
            state.phase = 'unknown';
            const message = TEXT.unknownSave + ' Your saved settings could not be read. Check again before changing anything.';
            outcome('Could not confirm settings save', message, TEXT.unknownSave, {checkAgain: true});
            return focusOutcome();
        }
        state.phase = 'idle';
        if (fresh.state_digest && fresh.state_digest === review.reply.expected_state_digest) {
            return saved(fresh, (review.reply.affected_routines || []).length, true);
        }
        if (fresh.configuration_revision === review.revision) {
            // Not in the saved settings yet, but never "not saved": the save holds the playlist's
            // lock through Spotify's checks, and this read does not wait for it, so it may still be
            // finishing. It stays unconfirmed (state.unknown, so Check again and leaving still
            // speak of it), the staged changes stay, and saving them needs a new acknowledgment.
            state.context = fresh;
            clearReview();
            outcome('Not confirmed', TEXT.notConfirmed, TEXT.unknownSave, {checkAgain: true});
            return focusOutcome();
        }
        conflict(fresh, null);
        return focusOutcome();
    }
    async function checkAgain() {
        // A second press while the settings are read is refused: one read per press.
        if (state.phase === 'checking') return false;
        if (state.unknown) return reconcile();
        state.phase = 'checking';
        render();
        const fresh = await readContext();
        state.phase = 'idle';
        // The read after a save may fail too: the button its message names stays offered.
        if (!fresh) {
            outcome('Settings saved', 'Your saved settings could not be read. Check again.', 'Settings saved.',
                {checkAgain: true});
        } else {
            adoptSaved(fresh);
            outcome('Settings saved', 'Showing your saved settings.', 'Settings saved.');
        }
        return focusOutcome();
    }
    // The saved settings changed under the page: show them, keep the staged changes that still
    // apply, mark the rest, and ask for a new review.
    // sections: the server's changed sections when it named them; else the page names those whose
    // digests differ in the settings it adopts (a reconciled save has no server answer to use).
    function conflict(fresh, sections) {
        let result = {sections: [], stale: []};
        // A new review is needed either way: adopting the fresh settings clears this one.
        if (fresh) result = adoptContext(fresh);
        else clearReview();
        const names = (Array.isArray(sections) && sections.length ? sections : result.sections)
            .map(name => SECTION_NAMES[name] || name);
        let message = TEXT.changed + (names.length ? ': ' + names.join(', ') : '') + '.';
        if (result.stale.length) {
            message += ' ' + plural(result.stale.length, 'change no longer applies', 'changes no longer apply') + '.';
        }
        message += ' Review the changes again.';
        return outcome('Settings changed', message, TEXT.changed + '.');
    }
    // Adopts fresh saved settings under the staged list. A change whose subject is gone moves to
    // "No longer applies", worded as it was staged. Any review is cleared: it was made from the
    // list and settings this replaces. The settings changed since an unconfirmed save was sent,
    // so they show what it did: it is settled.
    function adoptContext(fresh) {
        const previous = state.context;
        // Each change is described against the settings it was staged on, before they go.
        const labels = new Map(state.changes.map(entry => [entry, describe(entry.change)]));
        state.context = fresh;
        state.unknown = null;
        clearReview();
        const kept = [], stale = [];
        const dependent = entry => entry.change.op === 'set_drip' || entry.change.op === 'set_auto_archive';
        state.changes.forEach(entry => {
            const reason = dependent(entry) ? '' : staleReason(entry.change, []);
            if (reason) stale.push({key: entry.key, label: labels.get(entry), reason: reason});
            else kept.push(entry);
        });
        // Drip and auto-archive apply only while a collection or archive remains after the list.
        const dangling = kept.filter(entry => dependent(entry) && staleReason(entry.change, kept));
        dangling.forEach(entry => {
            stale.push({key: entry.key, label: labels.get(entry), reason: staleReason(entry.change, kept)});
        });
        state.changes = kept.filter(entry => dangling.indexOf(entry) < 0);
        state.stale = state.stale.concat(stale);
        PURPOSES.forEach(purpose => { if (!state.context[purpose]) state.replacing[purpose] = false; });
        markSavedPlaylists();
        const before = previous.section_digests || {}, after = fresh.section_digests || {};
        const sections = Object.keys(Object.assign({}, before, after)).filter(name => before[name] !== after[name]);
        return {sections: sections, stale: stale};
    }
    function staleReason(change, kept) {
        const context = state.context;
        const hasStaged = op => kept.some(entry => entry.change.op === op);
        switch (change.op) {
        case 'add_playlist_source':
            return context.sources.some(source => source.playlist_id === change.playlist_id) ? 'Already a source' : '';
        case 'add_search_source':
            return context.sources.some(source => source.search_query === change.search_query) ? 'Already a source' : '';
        case 'remove_source':
        case 'set_source_count':
            return context.sources.some(source => source.id === change.source_id) ? '' : 'This source no longer exists.';
        case 'remove_routine_source':
            return (context.routine_only_sources || []).some(item => item.playlist_id === change.playlist_id) ? '' :
                'No routine uses it now.';
        case 'set_drip':
            return context.collection || hasStaged(ASSET.collection.link) ? '' : 'No collection playlist is linked now.';
        case 'set_auto_archive':
            return context.archive || hasStaged(ASSET.archive.link) ? '' : 'No archive playlist is linked now.';
        default: {
            // A link needs nothing linked there yet; a replace or unlink needs its link.
            const purpose = subjectOf(change);
            if (change.op === ASSET[purpose].link) return context[purpose] ? 'A playlist is already linked here now.' : '';
            return context[purpose] ? '' : 'Nothing is linked here now.';
        }
        }
    }
    // Field refusals: each beside the control that staged it; focus moves to the first.
    function refusedChanges(errors, entries) {
        state.errors = {};
        clearReview();
        const general = [], items = [];
        (Array.isArray(errors) ? errors : []).forEach(error => {
            const entry = entries[error && error.index];
            const current = entry && state.changes.find(item => item.key === entry.key);
            const message = (error && error.message) || 'This change cannot be saved.';
            if (current) {
                state.errors[current.key] = {message: message, field: error.field || ''};
                items.push(describe(current.change) + ': ' + message);
            } else {
                general.push(message);
            }
        });
        const message = (items.length ? plural(items.length, 'change needs', 'changes need') + ' attention. ' : '') +
            general.join(' ') + (items.length ? ' Fix or undo them, then review the changes again.' : '');
        outcome('Some changes need attention', message.trim(), 'Some changes need attention.', {list: items});
        const first = state.changes.find(entry => state.errors[entry.key]);
        const target = first ? errorControl(first) : null;
        if (target) target.focus();
        else byId('setup-outcome-heading').focus();
        return false;
    }
    // The control a staged change's refusal is shown beside.
    function errorControl(entry) {
        const op = entry.change.op;
        if (op === 'set_source_count') return byId(countFieldId(entry.change.source_id));
        if (op === 'set_drip') {
            const error = state.errors[entry.key];
            return byId(error && error.field === 'drip_enabled' ? 'setup-drip-enabled' : 'setup-drip-count');
        }
        if (op === 'set_auto_archive') return byId('setup-auto-archive');
        return focusKey(undoKey(entry));
    }
    function undoKey(entry) {
        const op = entry.change.op, subject = subjectOf(entry.change);
        if (op === 'remove_source') return 'source-undo-' + entry.change.source_id;
        if (PURPOSES.includes(subject)) return subject + '-undo';
        if (op === 'remove_routine_source') {
            const index = (state.context.routine_only_sources || [])
                .findIndex(item => item.playlist_id === entry.change.playlist_id);
            return 'routine-source-' + index + '-undo';
        }
        return 'entry-undo-' + entry.key;
    }

    // ---- Rendering ----
    function setStatus(message) {
        state.status = message || '';
    }
    function showOutcome(heading, message, options) {
        options = options || {};
        byId('setup-outcome').hidden = false;
        byId('setup-outcome-heading').textContent = heading;
        byId('setup-outcome-text').textContent = message;
        const list = fill(byId('setup-outcome-list'), (options.list || []).map(item => el('li', 'sh-detail', item)));
        list.hidden = !(options.list || []).length;
        byId('setup-check-again').hidden = !options.checkAgain;
    }
    function clearOutcome() {
        if (state.phase === 'unknown' || state.phase === 'checking') return;
        byId('setup-outcome').hidden = true;
        byId('setup-check-again').hidden = true;
    }
    function fieldState(field, message, errorId) {
        const error = byId(errorId);
        if (message) field.setAttribute('aria-invalid', 'true');
        else field.removeAttribute('aria-invalid');
        if (error) {
            error.textContent = message || '';
            error.hidden = !message;
        }
    }
    function busyControl(control, busy, busyText, idleText) {
        if (busy) control.setAttribute('aria-disabled', 'true');
        else control.removeAttribute('aria-disabled');
        control.textContent = busy ? busyText : idleText;
    }
    // Rebuilds what changed state shows. A rebuilt control that held focus gets it back
    // (with any digits typed into it); plan.keys names where focus goes when the control that
    // was used is gone, else AppPanels.focusNear finds its section.
    function render(plan) {
        const region = plan && plan.trigger && plan.trigger.closest ? plan.trigger.closest('[data-panel-return]') : null;
        const active = document.activeElement;
        const key = active && active.getAttribute ? active.getAttribute('data-setup-focus') : null;
        const typed = key && active.localName === 'input' ? active.value : null;
        renderSources();
        PURPOSES.forEach(renderAsset);
        renderPickers();
        renderSummary();
        renderFooter();
        if (plan) {
            focusFirst((plan.keys || []).map(focusKey).filter(Boolean), plan.trigger, region);
        } else if (key && document.activeElement !== active) {
            const again = focusKey(key);
            if (shown(again)) {
                again.focus();
                if (typed !== null) again.value = typed;
            }
        }
    }

    function renderSources() {
        const count = sourceCount(), max = maxSources();
        byId('setup-source-limit').textContent = count + ' of ' + max + ' sources';
        const rows = state.context.sources.map(sourceRow);
        state.changes.filter(entry => /^add_/.test(entry.change.op)).forEach(entry => {
            rows.push(undoControl(row([el('span', 'sh-setup-name', addedSourceName(entry.change)),
                pendingMark('Pending: add')]), entry, describe(entry.change), 'entry-undo-' + entry.key));
        });
        fill(byId('setup-source-list'), rows);
        byId('setup-source-empty').hidden = rows.length > 0;
        const routineOnly = (state.context.routine_only_sources || []).map(routineOnlyRow);
        fill(byId('setup-routine-only-list'), routineOnly);
        byId('setup-routine-only').hidden = !routineOnly.length;
        fieldState(byId('setup-add-playlist-input'), inputError('setup-add-playlist-input'), 'setup-add-playlist-error');
        fieldState(byId('setup-add-search-input'), inputError('setup-add-search-input'), 'setup-add-search-error');
    }
    // One row of the page's lists: its words (each part an element, a space between them) and
    // its controls. Returns {row, text, controls}; options.tag is li unless given (a section's
    // current link is a div), options.attributes go on the row.
    function row(parts, options) {
        options = options || {};
        const node = attrs(el(options.tag || 'li', 'sh-setup-row'), options.attributes || {});
        const text = spaced(el('div', 'sh-setup-row-text'), parts);
        const controls = el('div', 'sh-setup-row-actions');
        spaced(node, [text, controls]);
        return {row: node, text: text, controls: controls};
    }
    // What is pending for a row: after its words.
    function pendingMark(words) {
        return el('span', 'sh-detail sh-text-warning sh-setup-pending-mark', words);
    }
    // The Undo of one staged change, in its built row: named for what it undoes, focus keyed by
    // focus, and returning focus to counterpart (the control that staged it) when that shows
    // again. The server's refusal of the change shows under the row and describes the control.
    // Returns the row.
    function undoControl(built, entry, name, focus, counterpart) {
        const control = namedButton('Undo', name, {'data-action-click': 'setup.undo', 'data-setup-undo': entry.key,
            'data-setup-focus': focus, 'data-setup-counterpart': counterpart || null});
        built.controls.appendChild(control);
        const error = state.errors[entry.key];
        if (error) {
            const id = 'setup-' + focus + '-error';
            built.row.appendChild(attrs(el('p', 'sh-field-error', error.message), {id: id}));
            control.setAttribute('aria-describedby', id);
        }
        return built.row;
    }
    function sourceRow(source) {
        const entry = staged('source-id:' + source.id), name = sourceName(source);
        const built = row([el('span', 'sh-setup-name', name),
            el('span', 'sh-detail sh-text-muted', SOURCE_TYPES[source.type] || 'Source')],
        {attributes: {'data-source-id': source.id}});
        if (entry && entry.change.op === 'remove_source') {
            built.text.append(' ', pendingMark('Removal pending'));
            return undoControl(built, entry, 'remove ' + name, 'source-undo-' + source.id, 'source-remove-' + source.id);
        }
        const fieldId = countFieldId(source.id), errorId = fieldId + '-error';
        const countEntry = entry && entry.change.op === 'set_source_count' ? entry : null;
        const bounds = range('raid_count', 1, 100);
        const label = attrs(el('label', 'sh-setup-count'), {for: fieldId});
        label.append(el('span', null, 'Collects'), ' ');
        const input = attrs(el('input', 'sh-input sh-setup-number'), {id: fieldId, type: 'number',
            inputmode: 'numeric', min: bounds.low, max: bounds.high, 'data-source-id': source.id,
            'data-action-change': 'setup.count', 'data-setup-focus': 'source-count-' + source.id,
            'aria-describedby': errorId});
        // A refused value stays in its field beside its error.
        input.value = heldCount(input);
        label.append(input, ' ', el('span', null, 'per run'), el('span', 'sr-only', ' from ' + name));
        spaced(built.controls, [label, namedButton('Remove', name, {'data-action-click': 'setup.remove-source',
            'data-source-id': source.id, 'data-setup-focus': 'source-remove-' + source.id})]);
        if (countEntry) built.text.append(' ', pendingMark('Change pending'));
        const serverError = countEntry && state.errors[countEntry.key] ? state.errors[countEntry.key].message : '';
        const message = inputError(fieldId) || serverError;
        const error = attrs(el('p', 'sh-field-error', message || ''), {id: errorId});
        error.hidden = !message;
        built.row.appendChild(error);
        if (message) input.setAttribute('aria-invalid', 'true');
        return built.row;
    }
    function routineOnlyRow(item, index) {
        const entry = staged('routine-source:' + item.playlist_id), base = 'routine-source-' + index;
        const ids = (item.routine_ids || []).map(id => '#' + id);
        const built = row([el('span', 'sh-setup-name', item.playlist_id), el('span', 'sh-detail sh-text-muted',
            'Used only by ' + (ids.length === 1 ? 'routine ' : 'routines ') + nameList(ids) + ': not a saved source')]);
        if (!entry) {
            built.controls.appendChild(namedButton('Remove', item.playlist_id, {
                'data-action-click': 'setup.remove-routine-source', 'data-playlist-id': item.playlist_id,
                'data-setup-focus': base + '-remove'}));
            return built.row;
        }
        built.text.append(' ', pendingMark('Removal pending'));
        return undoControl(built, entry, 'remove ' + item.playlist_id, base + '-undo', base + '-remove');
    }

    function renderAsset(purpose) {
        const saved = state.context[purpose], entry = staged(purpose), creation = state.creation[purpose];
        const asset = ASSET[purpose];
        let current = null;
        if (saved) {
            const built = row([el('span', 'sh-setup-name', saved.name || saved.playlist_id),
                el('span', 'sh-detail sh-text-muted', asset.label + ' playlist')], {tag: 'div'});
            if (entry) {
                built.text.append(' ', pendingMark(entry.change.op === asset.replace ? 'Pending: replace with ' +
                    playlistName(entry.change.playlist_id) : 'Pending: unlink'));
                current = assetUndo(built, purpose, entry);
            } else {
                spaced(built.controls, [
                    namedButton('Replace', 'the ' + purpose + ' playlist', {'data-action-click': 'setup.replace',
                        'data-purpose': purpose, 'data-setup-focus': purpose + '-replace',
                        'aria-expanded': String(state.replacing[purpose]), 'aria-controls': 'setup-' + purpose + '-choose'}),
                    namedButton('Unlink', 'the ' + purpose + ' playlist', {'data-action-click': 'setup.unlink',
                        'data-purpose': purpose, 'data-setup-focus': purpose + '-unlink'})]);
                current = built.row;
            }
        } else if (entry) {
            current = assetUndo(row([el('span', 'sh-setup-name', playlistName(entry.change.playlist_id)),
                pendingMark('Pending: use as the ' + purpose + ' playlist')], {tag: 'div'}), purpose, entry);
        } else {
            current = el('p', 'sh-detail sh-text-muted', 'No ' + purpose + ' playlist yet.');
        }
        fill(byId('setup-' + purpose + '-current'), [current]);

        const choosing = !entry && (!saved || state.replacing[purpose]);
        byId('setup-' + purpose + '-choose').hidden = !choosing;
        if (choosing) ensurePlaylists();
        const form = byId('setup-' + purpose + '-create-form');
        form.hidden = Boolean(saved);
        const create = byId('setup-' + purpose + '-create'), another = byId('setup-' + purpose + '-create-another');
        const offerAnother = creation.unknown && creation.refreshed;
        create.hidden = offerAnother;
        another.hidden = !offerAnother;
        busyControl(create, creation.busy && !offerAnother, 'Creating…', 'Create playlist in Spotify');
        busyControl(another, creation.busy && offerAnother, 'Creating…', 'Create another playlist');
        // Blocked until the person checks their playlists: marked, never disabled.
        if (creation.unknown && !creation.refreshed) create.setAttribute('aria-disabled', 'true');
        const nameField = byId('setup-' + purpose + '-create-name');
        fieldState(nameField, inputError(nameField.id), 'setup-' + purpose + '-create-error');
        fieldState(byId('setup-' + purpose + '-select'), inputError('setup-' + purpose + '-select'),
            'setup-' + purpose + '-select-error');
        const found = byId('setup-' + purpose + '-found');
        found.hidden = !(creation.found && choosing);
        byId('setup-' + purpose + '-found-text').textContent = creation.found ?
            'Found ' + creation.found.name + ' — use it?' : '';
        const status = byId('setup-' + purpose + '-create-status');
        status.textContent = creation.status;
        status.hidden = !creation.status;
        const other = state.context[asset.other];
        byId('setup-' + purpose + '-shared-note').hidden = !(saved && other && other.playlist_id === saved.playlist_id);
        if (purpose === 'collection') renderDrip();
        else renderAutoArchive();
    }
    // A staged link, replace or unlink: Undo returns focus to the control that staged it (Unlink
    // or Replace) when that shows again.
    function assetUndo(built, purpose, entry) {
        const op = entry.change.op;
        const counterpart = op === ASSET[purpose].unlink ? purpose + '-unlink' :
            op === ASSET[purpose].replace ? purpose + '-replace' : null;
        return undoControl(built, entry, describe(entry.change), purpose + '-undo', counterpart);
    }
    function renderDrip() {
        const group = byId('setup-drip');
        group.hidden = !effectiveAsset('collection');
        const values = dripEffective(), entry = staged('drip');
        const error = entry && state.errors[entry.key];
        const enabled = byId('setup-drip-enabled'), count = byId('setup-drip-count');
        const bounds = range('drip_count', 1, 50);
        enabled.checked = values.drip_enabled;
        count.setAttribute('min', bounds.low);
        count.setAttribute('max', bounds.high);
        // A refused or half-typed count stays as typed.
        if (document.activeElement !== count && !state.inputErrors[count.id]) count.value = String(values.drip_count);
        fieldState(enabled, error && error.field === 'drip_enabled' ? error.message : '', 'setup-drip-enabled-error');
        fieldState(count, inputError(count.id) || (error && error.field !== 'drip_enabled' ? error.message : ''),
            'setup-drip-count-error');
    }
    function renderAutoArchive() {
        byId('setup-auto-archive-group').hidden = !effectiveAsset('archive');
        const field = byId('setup-auto-archive'), entry = staged('auto-archive');
        field.checked = autoArchiveEffective();
        fieldState(field, entry && state.errors[entry.key] ? state.errors[entry.key].message : '', 'setup-auto-archive-error');
    }

    function renderPickers() {
        const owned = ownedPlaylists();
        PURPOSES.forEach(purpose => {
            const select = byId('setup-' + purpose + '-select');
            const status = byId('setup-' + purpose + '-list-status');
            const saved = state.context[purpose];
            const exclude = state.replacing[purpose] && saved ? saved.playlist_id : null;
            // Never this playlist, nor (when replacing) the playlist being replaced.
            const offered = item => item.id !== TARGET.id && item.id !== exclude;
            const version = state.playlistsVersion + ':' + (exclude || '') + ':' + TARGET.id;
            if (owned && select.getAttribute('data-version') !== version) {
                const chosen = select.value;
                const options = [attrs(el('option', null, 'Choose a playlist'), {value: ''})];
                owned.filter(offered).forEach(item => {
                    const tracks = Number(item.track_count);
                    const label = (item.name || item.id) + (Number.isFinite(tracks) ? ' (' +
                        plural(tracks, 'track', 'tracks') + ')' : '') + (item.owned === false ? ' · shared with you' : '');
                    options.push(attrs(el('option', null, label), {value: item.id}));
                });
                fill(select, options);
                select.value = options.some(option => option.value === chosen) ? chosen : '';
                select.setAttribute('data-version', version);
            }
            status.textContent = state.playlistsState === 'loading' ? 'Loading your playlists…' :
                state.playlistsState === 'failed' ? 'Could not load your playlists. Refresh my playlists to try again.' :
                    state.playlistsState === 'loaded' && !owned.some(offered) ? 'No other playlists found.' : '';
        });
    }

    function renderSummary() {
        const context = state.context;
        const saved = byId('setup-saved');
        const sourceNames = context.sources.map(sourceName);
        const collection = context.collection, archive = context.archive;
        const rows = [
            ['Sources', sourceNames.length ? plural(sourceNames.length, 'source', 'sources') + ': ' + sourceNames.join(', ') :
                'None'],
            ['Collection', collection ? (collection.name || collection.playlist_id) + ' · ' +
                (collection.drip_enabled ? 'adds ' + collection.drip_count + ' per run' : 'automatic adding off') : 'None'],
            ['Archive', archive ? (archive.name || archive.playlist_id) + ' · auto-archive ' +
                (archive.auto_archive_on_remove ? 'on' : 'off') : 'None'],
        ];
        const items = [];
        rows.forEach(pair => {
            const group = el('div', 'sh-setup-saved-item');
            spaced(group, [el('dt', 'sh-detail sh-text-muted', pair[0]), el('dd', 'sh-detail', pair[1])]);
            items.push(group);
        });
        fill(saved, items);
        const routines = (context.routines || []).map(routine => el('li', 'sh-detail', routineLine(routine)));
        fill(byId('setup-routines'), routines);
        byId('setup-routines-empty').hidden = routines.length > 0;

        const pending = state.changes.map(entry => {
            const label = describe(entry.change);
            return undoControl(row([el('span', 'sh-detail', label)]), entry, label, 'pending-undo-' + entry.key);
        });
        fill(byId('setup-pending-list'), pending);
        byId('setup-pending-empty').hidden = pending.length > 0;
        const stale = state.stale.map(item => {
            const built = row([el('span', 'sh-detail', item.label), el('span', 'sh-detail sh-text-muted', item.reason)]);
            built.controls.appendChild(namedButton('Undo', item.label, {'data-action-click': 'setup.dismiss-stale',
                'data-setup-stale': item.key, 'data-setup-focus': 'stale-undo-' + item.key}));
            return built.row;
        });
        fill(byId('setup-stale-list'), stale);
        byId('setup-stale').hidden = !stale.length;
        byId('setup-review').hidden = !state.review;
    }

    function renderFooter() {
        const count = state.changes.length;
        byId('setup-pending-count').textContent = count ? plural(count, 'pending change', 'pending changes') :
            'No pending changes';
        const status = byId('setup-status');
        status.textContent = state.status;
        status.hidden = !state.status;
        const reviewButton = byId('setup-review-btn'), save = byId('setup-save');
        busyControl(reviewButton, state.phase === 'reviewing', 'Reviewing…', 'Review changes');
        if (state.phase !== 'reviewing' && (!count || editRefusal() || anyCreating())) {
            reviewButton.setAttribute('aria-disabled', 'true');
        }
        const reason = saveRefusal();
        busyControl(save, state.phase === 'saving', 'Saving…', 'Save playlist settings');
        if (reason) save.setAttribute('aria-disabled', 'true');
        byId('setup-save-reason').textContent = state.phase === 'saving' ? '' : reason;
        const page = byId('setup-page');
        if (state.phase === 'saving' || state.phase === 'reviewing' || state.phase === 'checking' || anyCreating()) {
            page.setAttribute('aria-busy', 'true');
        } else {
            page.removeAttribute('aria-busy');
        }
    }

    // ---- Leaving: the shared navigation guard and the browser's own warning ----
    function leaveDetails() {
        const details = [];
        // Only an unconfirmed save may be saved already; a read after a definite reply is not one.
        if (state.unknown) details.push(TEXT.maySaved);
        const names = state.created.filter(made => !made.saved).map(made => made.name);
        if (names.length) details.push(nameList(names) + (names.length === 1 ? ' stays' : ' stay') + ' in your Spotify library.');
        PURPOSES.forEach(purpose => {
            const creation = state.creation[purpose];
            if (creation.unknown) details.push(creation.requested + ' may already be in your Spotify library.');
        });
        if (details.length) details.push(TEXT.pageOnly);
        return details;
    }
    function navigationGuard(detail) {
        if (state.phase === 'saving' || anyCreating()) {
            return refuse(state.phase === 'saving' ? TEXT.leaveSaving : TEXT.leaveCreating);
        }
        const pending = state.changes.length > 0, details = leaveDetails();
        if (!pending && !details.length) return true;
        const message = (pending ? ['Your pending changes are not saved. Leaving discards them.'] : []).concat(details);
        return window.AppConfirm.ask({
            title: pending ? TEXT.discardTitle : 'Leave setup?',
            message: message.join(' '),
            confirmLabel: pending ? 'Discard and leave' : 'Leave',
            cancelLabel: 'Stay',
            tone: 'danger',
            trigger: detail && detail.element,
        });
    }
    // The browser's own warning (AppNavigation's one unload listener) covers what the guard
    // asks about. A navigation the guard just approved warns only while saving or creating,
    // which leaving cannot stop.
    function unloadCheck(approved) {
        const busy = state.phase === 'saving' || anyCreating();
        return busy || (!approved && (state.changes.length > 0 || leaveDetails().length > 0));
    }

    window.registerActions({
        // Staging: refused, with its reason, while a review, save or unknown save is unresolved.
        'setup.add-playlist': guarded(addPlaylistSource),
        'setup.add-search': guarded(addSearchSource),
        'setup.remove-source': guarded(removeSource),
        'setup.count': guarded(setSourceCount, {revert: true}),
        'setup.remove-routine-source': guarded(removeRoutineSource),
        'setup.undo': guarded(undo),
        'setup.replace': guarded(toggleReplace),
        'setup.unlink': guarded(unlink),
        'setup.use': guarded(useExisting),
        'setup.use-found': guarded(useFound),
        'setup.drip': guarded(setDrip, {revert: true}),
        'setup.auto-archive': guarded(setAutoArchive, {revert: true}),
        'setup.dismiss-stale': dismissStale,
        // Typing or choosing again stages nothing; it only clears the field's earlier refusal.
        'setup.edit': edited,
        'setup.refresh': function (control) { refreshPlaylists(control); },
        'setup.create': function (form) {
            createPlaylist(form, false);
            return false;
        },
        'setup.create-another': function (control) { createPlaylist(control, true); },
        'setup.ack': function () { renderFooter(); },
        'setup.review': function (control) { reviewChanges(control); },
        'setup.save': function (control) { saveSettings(control); },
        'setup.check-again': function () { checkAgain(); },
    });
    if (window.AppNavigation) {
        window.AppNavigation.addGuard(navigationGuard);
        window.AppNavigation.addUnloadCheck(unloadCheck);
    }

    // Read-only, for the cases the page shares with the server's own tests.
    window.PlaylistSetup = Object.freeze({describe: describe, parsePlaylistId: parsePlaylistId});
    render();
    // Below the workspace width the action footer sticks to the viewport; its measured height
    // keeps focused and scrolled-to controls clear of it (components/action-footer.css).
    if (window.AppPanels) window.AppPanels.reserveFooter(byId('setup-footer'), document.documentElement);
    // ?section= names the section to start at (the route allows only the three sections).
    const start = byId('setup-' + DATA.section + '-heading');
    if (start) start.focus();
}());
