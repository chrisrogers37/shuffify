/**
 * Reviewed live actions and the outcomes retained on this page.
 *
 * prepare() reads server context and opens a review, resolving a single-use
 * handle only on its final Run; run() dispatches that handle once; reconcile()
 * records read-only observations for one retained operation. A conflicting page
 * draft is resolved through the page's own common review first, and the action is
 * then reviewed again before its separate Run.
 */
(function (global) {
    'use strict';
    // Executors may resolve multiple sources sequentially before writing and verifying.
    // Five minutes bounds the page wait; server/proxy limits can still end it earlier.
    var defaultTimeoutMs = 300000;
    var defaultReadTimeoutMs = 30000;
    var config = {}, records = [], busy = false, timeoutMs = defaultTimeoutMs;
    var readTimeoutMs = defaultReadTimeoutMs;
    var successStates = ['complete', 'not_required'];
    // Restored verifies compensation, not completion of the requested action.
    var effectStates = successStates.concat(['failed', 'unknown', 'restored']);
    // Current-page identities only; never database receipts or idempotency tokens.
    var nextOperationId = 0, preparation = null, handles = new WeakMap(), checking = new Set();
    // Records whose "Update this view" is running; a second activation waits for it.
    var refreshing = new Set();
    var guardInstalled = false, controlsBound = false, leaveResolve = null;
    var digest = /^[0-9a-f]{64}$/, playlistId = /^[A-Za-z0-9]+$/, trackUri = /^spotify:track:[A-Za-z0-9]{22}$/;
    // The review request allowlist; tests pin it to the server's shuffle form fields.
    var formFields = ['algorithm', 'keep_first', 'section_count', 'shuffle_percentage', 'shuffle_location',
        'min_spacing', 'shuffle_within_albums', 'jitter'];
    // Each review binding the server returned travels back under its request field.
    var bindingFields = {scope: 'reviewed_scope', before_order: 'reviewed_before_order', history: 'reviewed_history'};
    // Each reviewed kind: what identifies it (a target playlist, routine or snapshot); its
    // family (which context shape and review copy apply, and whether the named target must
    // itself verify); the options its body takes; the steps a direct executor kind runs;
    // what it is known to write before its context loads (a routine's writes come from the
    // server); and its fixed mutation route and, for GET reviews, its context route.
    var reviewedKinds = {
        shuffle: {subject: 'target', family: 'library', body: 'shuffle', writes: ['tracks', 'history'],
            encoding: 'form', url: function (id) { return '/api/shuffle/' + id; }},
        undo: {subject: 'target', family: 'library', body: 'form', writes: ['tracks', 'history'],
            encoding: 'form', url: function (id) { return '/api/undo/' + id; }},
        privacy: {subject: 'target', family: 'library', body: 'visibility', writes: ['metadata'],
            encoding: 'json', url: function (id) { return '/api/playlist/' + id + '/toggle-visibility'; }},
        raid: {subject: 'target', family: 'executor', body: 'sources', steps: ['raid'], writes: ['inbox'],
            encoding: 'json', url: function (id) { return '/api/playlist/' + id + '/raid-now'; }},
        drip: {subject: 'target', family: 'executor', body: 'none', steps: ['drip'], writes: ['tracks', 'inbox'],
            encoding: 'json', url: function (id) { return '/api/playlist/' + id + '/drip-now'; }},
        raid_and_drip: {subject: 'target', family: 'executor', body: 'sources', steps: ['raid', 'drip'],
            writes: ['tracks', 'inbox'], encoding: 'json',
            url: function (id) { return '/api/playlist/' + id + '/raid-and-drip'; }},
        schedule: {subject: 'schedule', family: 'executor', body: 'none', writes: [], encoding: 'json',
            url: function (id) { return '/api/schedules/' + id + '/run'; },
            context: function (id) { return '/api/schedules/' + id + '/run-context'; }},
        snapshot_restore: {subject: 'snapshot', family: 'snapshot', body: 'none', writes: ['tracks', 'history'],
            encoding: 'json', url: function (id) { return '/api/snapshots/' + id + '/restore'; },
            context: function (id) { return '/api/snapshots/' + id + '/restore-context'; }},
    };
    var changedReview = ['review_changed', 'history_changed', 'resource_changed'];
    var busyNotice = 'This action is still running. Closing this view cannot cancel a dispatched change.';

    function copy(value) { return JSON.parse(JSON.stringify(value)); }
    function freeze(value) {
        if (value && typeof value === 'object' && !Object.isFrozen(value)) {
            Object.keys(value).forEach(function (key) { freeze(value[key]); });
            Object.freeze(value);
        }
        return value;
    }
    function isObject(value) { return Boolean(value) && typeof value === 'object' && !Array.isArray(value); }
    function count(value) { return Number.isInteger(value) && value >= 0; }
    function text(parent, tag, value) {
        var element = document.createElement(tag); element.textContent = value;
        parent.appendChild(element); return element;
    }
    function byId(id) { return global.document ? document.getElementById(id) : null; }
    function hasAttention() {
        return records.some(function (record) { return record.attention; });
    }

    // Claims are strings: `tracks:<spotify id>`, `metadata:<id>`, `inbox:<target>`
    // and `history:*` (session history is shared by every playlist on the page).
    // A written resource stays claimed while its outcome is unknown or missing.
    // A verified restoration is a known outcome: the playlist is back at its baseline.
    var knownStates = ['complete', 'not_required', 'failed', 'restored'];
    function claimId(claim) { return claim.slice(claim.indexOf(':') + 1); }
    function writesOf(context) {
        var writes = [];
        context.resources.forEach(function (resource) {
            if (resource.access !== 'write') return;
            writes.push(resource.domain === 'inbox' ? 'inbox:' + resource.resource
                : (resource.mutation_type === 'metadata' ? 'metadata:' : 'tracks:') + resource.resource);
        });
        if (context.required_bookkeeping.includes('session_history')) writes.push('history:*');
        return writes.filter(function (claim, index) { return writes.indexOf(claim) === index; });
    }
    function unknownBookkeeping(op, kinds) {
        return kinds.some(function (kind) {
            var entry = op && op.bookkeeping.find(function (item) { return item.kind === kind; });
            return !entry || entry.status === 'unknown';
        });
    }
    // A resource's latest finished check decides: a match resolves only that resource's
    // Spotify contents, never the bookkeeping or other resources of the same run.
    function observedMatch(record, id) {
        if (checking.has(record.operationId)) return false;
        for (var index = record.observations.length - 1; index >= 0; index--) {
            var entry = record.observations[index].resources.find(function (item) { return item.id === id; });
            if (entry) return entry.status === 'match';
        }
        return false;
    }
    function claimsOf(record) {
        if (record.state === 'busy' || record.complete || record.noEffect) return [];
        var intent = record.intent, op = record.operation;
        if (intent.kind === 'privacy') return observedMatch(record, intent.targetId) ? [] : ['metadata:' + intent.targetId];
        var pendingKinds = intent.requiredBookkeeping.filter(function (kind) { return kind.indexOf('pending_') === 0; });
        return intent.writes.filter(function (claim) {
            if (claim === 'history:*') return unknownBookkeeping(op, ['session_history']);
            if (claim.indexOf('inbox:') === 0) {
                return unknownBookkeeping(op, pendingKinds) || Boolean(op && op.bookkeeping.some(function (entry) {
                    return entry.kind.indexOf('pending_') === 0 && entry.status === 'unknown';
                }));
            }
            var id = claimId(claim);
            if (observedMatch(record, id)) return false;
            var entries = op ? op.resources.filter(function (resource) { return resource.resource === id; }) : [];
            return !entries.length || entries.some(function (resource) { return !knownStates.includes(resource.status); });
        });
    }
    // Facts no check can resolve: required bookkeeping missing, any bookkeeping not
    // verified, or a written playlist that neither verified nor matched a later check.
    function openFacts(record) {
        var op = record.operation;
        return unknownBookkeeping(op, record.intent.requiredBookkeeping) || Boolean(op) && (
            op.bookkeeping.some(function (entry) { return !successStates.includes(entry.status); }) ||
            op.resources.some(function (resource) {
                return !successStates.includes(resource.status) && !observedMatch(record, resource.resource);
            }));
    }
    // A finished run that did not fully complete stays in Attention until a check settles
    // it: nothing claimed and nothing open.
    function derive(record) {
        record.claims = claimsOf(record);
        record.blocked = record.claims.length > 0;
        var finished = record.state !== 'busy' && !record.complete && !record.noEffect;
        record.settled = finished && record.observations.length > 0 && !record.blocked && !openFacts(record);
        record.attention = record.state === 'busy' || record.blocked || Boolean(record.displayAttention) ||
            (finished && !record.settled);
    }
    // Every unresolved claim on this page, and the keys of the records holding them.
    function unresolved() {
        var claims = new Set(), keys = new Set();
        records.forEach(function (record) {
            var own = claimsOf(record);
            own.forEach(function (claim) { claims.add(claim); });
            if (own.length) keys.add(record.intent.key);
        });
        return {claims: claims, keys: keys};
    }
    function hasUnresolved(claim) { return unresolved().claims.has(claim); }
    function recordConflict(writes) {
        var claims = unresolved().claims;
        return writes.some(function (claim) { return claims.has(claim); });
    }
    // What a kind is known to write before its context loads; routine resources come from the server.
    function anticipatedWrites(kind, targetId) {
        if (!targetId) return [];
        return reviewedKinds[kind].writes.map(function (domain) {
            return domain === 'history' ? 'history:*' : domain + ':' + targetId;
        });
    }
    function keyBlocked(state, key) {
        var separator = String(key).indexOf(':');
        if (separator <= 0) return false;
        var kind = key.slice(0, separator), subject = key.slice(separator + 1);
        if (!Object.prototype.hasOwnProperty.call(reviewedKinds, kind)) return false;
        var targetId = reviewedKinds[kind].subject === 'target' ? subject : null;
        return state.keys.has(key) || anticipatedWrites(kind, targetId).some(function (claim) {
            return state.claims.has(claim);
        });
    }
    function isBlocked(key) { return keyBlocked(unresolved(), key); }
    function syncControls(state) {
        if (!global.document) return;
        state = state || unresolved();
        document.querySelectorAll('[data-live-operation-key]').forEach(function (button) {
            var blocked = busy || keyBlocked(state, button.dataset.liveOperationKey);
            if (blocked && !button.hasAttribute('data-live-disabled')) {
                button.setAttribute('data-live-disabled', String(button.disabled));
            }
            if (blocked) button.disabled = true;
            else if (button.hasAttribute('data-live-disabled')) {
                button.disabled = button.getAttribute('data-live-disabled') === 'true';
                button.removeAttribute('data-live-disabled');
            }
        });
    }
    // A page sets a keyed control's own state here; the next syncControls re-applies any
    // block the controller holds, and lifting that block restores this state.
    function setControlDisabled(button, disabled) {
        button.removeAttribute('data-live-disabled');
        button.disabled = disabled;
    }
    function stepName(kind) {
        return {raid: 'Collect for review', drip: 'Add collected tracks', shuffle: 'Shuffle playlist',
            undo: 'Restore previous saved version', snapshot_restore: 'Restore saved snapshot',
            privacy: 'Change playlist visibility',
            rotate: 'Rotate tracks', raid_and_drip: 'Collect and add tracks',
            raid_and_shuffle: 'Collect and shuffle'}[kind] || 'Action step';
    }
    function identity(parent, data) {
        if (data.execution_id != null) {
            text(parent, 'p', 'Execution ' + data.execution_id +
                (data.started_at ? ' · ' + data.started_at : ''));
        } else if (data.execution_state === 'not_started') text(parent, 'p', 'Execution was not started.');
    }
    function stateLine(record) {
        if (record.state === 'busy') return 'Running — closing this view cannot cancel a dispatched action.';
        if (record.complete) {
            return record.displayAttention ? 'Attention — verified, but this view could not update.'
                : 'Complete — all required effects verified.';
        }
        if (record.noEffect) return 'Not applied — this attempt made no playlist changes.';
        if (record.settled) return 'Checked — the earlier result is kept below.';
        return record.aggregate === 'unknown' ? 'Attention — some effects could not be established.'
            : 'Attention — this action did not fully complete.';
    }
    var checkCopy = {
        privacy: {match: 'Current visibility matches the request. This verifies the current state; ' +
            'it does not show which request caused it.',
            mismatch: 'Current visibility does not match the request.',
            failed: 'Current visibility could not be checked. Earlier details are unchanged.'},
        tracks: {match: 'Current tracks match the retained order.',
            mismatch: 'Current tracks do not match the retained order.',
            unavailable: 'Current tracks cannot be compared: the intended order was lost with the reply.',
            failed: 'Current tracks could not be checked. Earlier details are unchanged.'},
    };
    // spoken: the same words without the time, for the announcer.
    function observationLabel(record, observation, spoken) {
        var time = observation.checkedAt ? observation.checkedAt.replace('T', ' ').slice(0, 19) + ' UTC' : '';
        var prefix = spoken ? '' : 'Checked ' + time + ': ';
        if (record.intent.kind === 'privacy') {
            return prefix + (checkCopy.privacy[observation.status] || checkCopy.privacy.failed);
        }
        var lines = observation.resources.map(function (entry) {
            var line = checkCopy.tracks[entry.status] || checkCopy.tracks.failed;
            // A check covering several playlists names each one.
            return observation.resources.length > 1 ? (record.intent.names[entry.id] || entry.id) + ': ' + line : line;
        });
        var anyMatch = observation.resources.some(function (entry) { return entry.status === 'match'; });
        return prefix + lines.join(' ') + (!anyMatch ? '' : observation.settled
            ? ' This verifies current contents only; it does not show which request caused it.'
            : ' This verifies current contents only; the earlier action’s other effects stay unresolved, ' +
                'so it still needs attention.');
    }
    function actionButton(parent, label, attribute, operationId) {
        var button = text(parent, 'button', label);
        button.type = 'button'; button.className = 'sh-button-secondary';
        button.setAttribute(attribute, String(operationId));
        return button;
    }
    function renderReviewedExtras(row, details, record) {
        record.observations.forEach(function (observation, index) {
            var latestCheck = index === record.observations.length - 1;
            text(latestCheck ? row : details, 'p', observationLabel(record, observation));
        });
        if (record.state === 'busy') return;
        // Only an unresolved playlist or visibility can be checked; bookkeeping cannot.
        if (record.claims.some(function (claim) { return /^(tracks|metadata):/.test(claim); })) {
            var inFlight = checking.has(record.operationId);
            var checkLabel = record.intent.kind === 'privacy' ? 'Check current visibility' : 'Check current playlist';
            var check = actionButton(row, inFlight ? 'Checking…' : checkLabel, 'data-live-check', record.operationId);
            check.setAttribute('aria-label', (inFlight ? 'Checking' : checkLabel) + ' for ' + rowName(record));
            // aria-disabled keeps keyboard focus on the control while its check runs.
            if (inFlight) check.setAttribute('aria-disabled', 'true');
        }
        if (record.displayAttention) {
            var updating = refreshing.has(record.operationId);
            var update = actionButton(row, updating ? 'Updating…' : 'Update this view', 'data-live-refresh',
                record.operationId);
            update.setAttribute('aria-label', (updating ? 'Updating' : 'Update this view') + ' for ' + rowName(record));
            if (updating) update.setAttribute('aria-disabled', 'true');
        }
    }
    function rowName(record) { return record.intent.label + ', ' + record.intent.targetName; }
    // The selector of the control (or result row) holding focus, to restore it after a rebuild.
    // A control that disappears (a resolved Check) falls back to its row.
    function focusTarget(list) {
        var focused = global.document.activeElement;
        if (!focused || !focused.dataset || !list.contains || !list.contains(focused)) return null;
        var row = focused.closest && focused.closest('li');
        var rowSelector = row && row.id ? '#' + row.id : null;
        if (focused.dataset.liveCheck) {
            return {selector: '[data-live-check="' + focused.dataset.liveCheck + '"]', fallback: rowSelector};
        }
        if (focused.dataset.liveRefresh) {
            return {selector: '[data-live-refresh="' + focused.dataset.liveRefresh + '"]', fallback: rowSelector};
        }
        return rowSelector ? {selector: rowSelector, fallback: null} : null;
    }
    // Results needing attention, linked to their rows above the list. It is rebuilt only
    // when that list changes, and a link holding focus keeps it: the same link, else its
    // row, else the heading. Review modes hide it (renderPanel).
    var summarySignature = null, summaryShown = false;
    function renderSummary() {
        var summary = byId('live-operation-summary');
        if (!summary) return;
        var needing = records.filter(function (record) { return record.attention && record.state !== 'busy'; });
        var signature = needing.map(function (record) { return record.operationId + ':' + rowName(record); }).join('|');
        summaryShown = needing.length > 0;
        if (signature === summarySignature) return;
        summarySignature = signature;
        var focused = global.document.activeElement;
        var focusedRow = focused && focused.dataset && summary.contains && summary.contains(focused)
            ? focused.dataset.liveRow : null;
        summary.replaceChildren();
        if (needing.length) {
            text(summary, 'p', 'Needs attention:');
            var items = text(summary, 'ul', '');
            needing.slice().reverse().forEach(function (record) {
                var link = text(text(items, 'li', ''), 'a', rowName(record));
                link.href = '#live-operation-' + record.operationId;
                link.dataset.liveRow = String(record.operationId);
            });
        }
        if (focusedRow) {
            var again = summary.querySelector('[data-live-row="' + focusedRow + '"]') ||
                byId('live-operation-' + focusedRow);
            if (again && again.focus) again.focus();
            if (!again || global.document.activeElement !== again) focusPanelHeading();
        }
    }
    var claimSignature = '';
    function render() {
        records.forEach(derive);
        var state = unresolved();
        syncControls(state);
        // A page whose own controls depend on unresolved claims (Workshop Save) hears each change.
        var signature = Array.from(state.claims).sort().join('|');
        if (signature !== claimSignature) {
            claimSignature = signature;
            if (config.onClaimsChanged) {
                try { config.onClaimsChanged(); } catch (_) { /* the page keeps its earlier state */ }
            }
        }
        if (!global.document) return;
        var list = document.getElementById('live-operation-results');
        if (!list) return;
        var restore = focusTarget(list);
        list.replaceChildren();
        var names = {complete: 'Complete — verified', not_required: 'No change needed — verified',
            failed: 'Failed — this attempt applied no change', unknown: 'Unknown — effect not established',
            restored: 'Restored — baseline verified'};
        records.slice().reverse().forEach(function (record) {
            var row = text(list, 'li', ''); row.id = 'live-operation-' + record.operationId;
            row.className = 'sh-callout p-3 space-y-2'; row.tabIndex = -1;
            text(row, 'h3', record.intent.label + ' · ' + record.intent.targetName);
            text(row, 'p', stateLine(record));
            var data = record.response || {};
            var details = document.createElement('details'); details.className = 'sh-detail';
            var detailsLabel = text(details, 'summary', 'Run details');
            detailsLabel.className = 'sh-button-secondary';
            text(detailsLabel, 'span', ': ' + rowName(record)).className = 'sr-only';
            identity(details, data.result || data);
            if (data.message) text(row, 'p', data.message);
            var operation = record.operation;
            if (operation) {
                operation.resources.forEach(function (resource) {
                    var detail = text(row, 'div', '');
                    var name = resource.resource_name || record.intent.names[resource.resource] || resource.resource ||
                        'Unresolved resource';
                    text(detail, 'h4', name + ' · ' + stepName(resource.step || operation.kind));
                    var notStarted = resource.started === false;
                    text(detail, 'p', (notStarted ? 'Did not run — nothing was sent'
                        : names[resource.status] || 'Unknown — effect not established') +
                        (resource.affected_count != null && resource.verification !== 'metadata' && !notStarted
                            ? ' · Affected tracks: ' + resource.affected_count : ''));
                    if (successStates.concat(['restored']).includes(resource.status)) {
                        var verification = {ordered: 'The complete playlist order was checked.',
                            occurrences: 'Track occurrence counts were checked.', metadata: 'Playlist settings were checked.'};
                        if (verification[resource.verification]) text(details, 'p', name + ': ' + verification[resource.verification]);
                    }
                    if (typeof resource.resource === 'string' && /^[a-zA-Z0-9]+$/.test(resource.resource)) {
                        var link = text(successStates.includes(resource.status) ? details : detail, 'a',
                            'View ' + name + ' on Spotify (new tab)');
                        link.href = 'https://open.spotify.com/playlist/' + encodeURIComponent(resource.resource);
                        link.target = '_blank'; link.rel = 'noopener noreferrer'; link.className = 'sh-button-secondary';
                    }
                });
                operation.bookkeeping.forEach(function (entry) {
                    if (entry.kind === 'source_resolution' && entry.status === 'failed') {
                        text(row, 'p', 'Some discovery sources could not be read. Any completed collection and inbox changes are shown separately.');
                        (Array.isArray(entry.failed_sources) ? entry.failed_sources : []).forEach(function (source) {
                            if (!source || typeof source !== 'object') return;
                            var id = typeof source.source_playlist_id === 'string' ? source.source_playlist_id.trim() : '';
                            if (id && /^[a-zA-Z0-9]+$/.test(id)) {
                                var link = text(row, 'a', 'View discovery playlist ' + id + ' on Spotify (new tab)');
                                link.href = 'https://open.spotify.com/playlist/' + encodeURIComponent(id);
                                link.target = '_blank'; link.rel = 'noopener noreferrer'; link.className = 'sh-button-secondary';
                            } else if (id) text(row, 'p', 'Discovery playlist ID: ' + id);
                            else if (typeof source.source_id === 'number' ||
                                (typeof source.source_id === 'string' && source.source_id.trim())) {
                                text(row, 'p', 'Discovery source ID: ' + source.source_id);
                            }
                        });
                        return;
                    }
                    var labels = {pending_stage: 'Review inbox', pending_staging: 'Review inbox',
                        pending_promotion: 'Review inbox status', session_history: 'Saved playlist history',
                        execution_record: 'Run history', execution_status: 'Run history'};
                    var states = {complete: 'saved', not_required: 'already up to date',
                        failed: 'not saved by this attempt', unknown: 'could not be verified',
                        restored: 'restored previous app state — verified'};
                    text(successStates.includes(entry.status) ? details : row, 'p',
                        (labels[entry.kind] || 'Saved app state') + ': ' + states[entry.status]);
                });
                (operation.steps || []).forEach(function (step) {
                    var states = {success: 'completed', complete: 'completed', failed: 'needs attention',
                        skipped: 'did not run', not_started: 'did not run', unknown: 'outcome unknown',
                        restored: 'restored — verified', partial: 'partly completed'};
                    text(['success', 'complete'].includes(step.status) ? details : row, 'h4',
                        stepName(step.step) + ' · ' + (states[step.status] || 'needs attention'));
                    identity(details, step);
                });
            }
            if (record.message) text(row, 'p', record.message);
            if (record.refreshMessage) text(row, 'p', record.refreshMessage);
            renderReviewedExtras(row, details, record);
            row.appendChild(details);
        });
        var again = restore && list.querySelector ? list.querySelector(restore.selector) ||
            (restore.fallback && list.querySelector(restore.fallback)) : null;
        if (again && again.focus) again.focus();
        renderSummary();
        var open = document.getElementById('live-operation-open');
        if (open) { open.hidden = !records.length; open.textContent = hasAttention() ? 'View live action attention' : 'View live action results'; }
        renderPanel();
    }

    // Stable panel hooks: AppPanels keeps the first open's callbacks while active.
    var panelOptions = {
        modal: false,
        beforeClose: function () { return true; },
        onClose: function () {
            var current = preparation;
            // While a draft resolves, its own review replaces this view; that is not a cancel.
            if (!current || current.phase === 'accepted' || current.phase === 'resolving') return;
            finish(current, null);
            var trigger = current.trigger;
            if (trigger && trigger.isConnected && trigger.focus) trigger.focus();
        },
    };
    function openResults(trigger) {
        render();
        if (!global.AppPanels || !global.document) return Promise.resolve(false);
        return global.AppPanels.open('live-operation-panel', Object.assign({trigger: trigger}, panelOptions));
    }
    function focusPanelHeading() {
        var heading = byId('live-operation-heading');
        if (heading && heading.focus) heading.focus();
    }
    function statusText() {
        return busy ? 'Busy — waiting for this attempt.' :
            hasAttention() ? 'Attention — keep this page open to retain these details.' :
                records.length ? 'Verified results' : '';
    }
    // Review copy per preparation phase; results mode restores the page's own labels.
    function reviewMode(current) {
        if (!current || current.phase === 'accepted') return null;
        var label = current.context ? current.context.label : '';
        var named = current.context && current.context.target ? label + ', ' + current.context.target.name : label;
        // spoken: what the announcer says once on entering that state; loading is heard as
        // the focused heading, and a resolving draft as the review that replaces this view.
        return {
            loading: {heading: 'Loading review…', status: 'Loading the current playlist. Nothing has been sent.', run: ''},
            error: {heading: 'Review unavailable', status: 'The review could not be loaded. Nothing was sent.',
                run: 'Load review again', spoken: 'Review unavailable: ' +
                    (current.error || 'the review could not be loaded.') + ' Nothing was sent.'},
            review: {heading: 'Review: ' + label, status: 'Review ready. Nothing is sent until you run it.', run: label,
                spoken: 'Review ready: ' + named + '. Nothing is sent until you run it.'},
            conflict: {heading: 'Save or discard your draft first',
                status: 'Your draft overlaps this action. Nothing has been sent.', run: 'Review & save draft',
                discard: true, spoken: 'Save or discard your draft first: it overlaps this action. Nothing has been sent.'},
            resolving: {heading: 'Finishing your draft…',
                status: 'Your draft is open for review. This action has not been sent.', run: ''},
        }[current.phase];
    }
    // Results-mode text comes from the page's data-results-label; review text from reviewMode.
    function applyLabel(element, mode, reviewText) {
        if (!element) return;
        if (mode) element.textContent = reviewText;
        else if (element.dataset.resultsLabel) element.textContent = element.dataset.resultsLabel;
    }
    function renderPanel() {
        var review = byId('live-operation-review'), status = byId('live-operation-status');
        if (!review) return;
        var mode = reviewMode(preparation), runButton = byId('live-operation-run');
        var list = byId('live-operation-results'), note = byId('live-operation-note');
        var summary = byId('live-operation-summary');
        review.hidden = !mode;
        if (list) list.hidden = Boolean(mode);
        if (note) note.hidden = Boolean(mode);
        // Its links lead to result rows, which a review hides.
        if (summary) summary.hidden = Boolean(mode) || !summaryShown;
        // The status line is visible text; the announcer reads each review state once.
        // Every state change is noted, so a second failure after Load review again is heard.
        if (mode && preparation.spokenPhase !== preparation.phase) {
            preparation.spokenPhase = preparation.phase;
            if (mode.spoken) announce(mode.spoken);
        }
        applyLabel(byId('live-operation-close'), mode, 'Cancel');
        applyLabel(byId('live-operation-back'), mode, 'Cancel');
        var footerStatus = byId('live-operation-footer-status');
        if (footerStatus) {
            var footerText = mode ? '' : busy ? 'Running — closing this view cannot cancel a change already sent.'
                : hasAttention() ? 'Some results need attention; they stay on this page until you leave.' : '';
            footerStatus.textContent = footerText;
            footerStatus.hidden = !footerText;
        }
        applyLabel(byId('live-operation-heading'), mode, mode && mode.heading);
        if (runButton) {
            runButton.hidden = !(mode && mode.run);
            runButton.textContent = mode ? mode.run : '';
        }
        var discard = byId('live-operation-discard');
        if (discard) discard.hidden = !(mode && mode.discard);
        if (status) status.textContent = mode ? mode.status : statusText();
        if (mode) renderReview(review, preparation);
        else if (review.dataset.notice) {
            review.hidden = false; review.replaceChildren(); text(review, 'p', review.dataset.notice);
        }
    }
    // Option names come from the reviewed form's own labels; fall back to the field name.
    function paramLabel(current, name) {
        var form = current.trigger && current.trigger.form;
        var control = form && form.elements && form.elements.namedItem(name);
        var label = control && control.labels && control.labels[0];
        var named = label && label.textContent.trim();
        return named || name.replace(/_/g, ' ');
    }
    function timeLabel(value) {
        return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) ? value.replace('T', ' ').slice(0, 16) + ' UTC' : value;
    }
    function changeSummary(summary) {
        var parts = [];
        if (summary.added_count) parts.push('adds ' + summary.added_count + ' track' + (summary.added_count === 1 ? '' : 's'));
        if (summary.removed_count) parts.push('removes ' + summary.removed_count + ' track' + (summary.removed_count === 1 ? '' : 's'));
        parts.push(summary.order_changed ? 'changes the order' : 'keeps the order of the remaining tracks');
        return 'Restoring ' + parts.join(', ') + '.';
    }
    function renderReview(container, current) {
        container.replaceChildren();
        if (current.phase === 'loading') {
            text(container, 'p', 'Reading the current playlist from Spotify. Nothing is sent until you choose Run.');
            return;
        }
        if (current.phase === 'error') {
            text(container, 'p', current.error || 'The review could not be loaded.');
            text(container, 'p', 'Nothing was sent. Your options are still here.');
            return;
        }
        var context = current.context, action = context.action, now = context.current;
        if (current.phase === 'conflict') {
            text(container, 'p', 'Your unsaved draft changes ' + resourceNames(current.conflict) +
                ', which this action would also change.');
            text(container, 'p', 'Review & save opens the full review of your draft; nothing is saved until you choose ' +
                'Save there. Discard keeps only what is already on Spotify. Either way, this action is reviewed again ' +
                'before it can run.');
            return;
        }
        text(container, 'p', 'Playlist: ' + context.target.name);
        if (reviewedKinds[context.kind].family === 'executor') executorReview(container, context);
        else if (context.kind === 'shuffle') {
            text(container, 'p', 'Method: ' + action.algorithm.name);
            var names = Object.keys(action.params);
            if (names.length) {
                var options = text(container, 'ul', '');
                names.forEach(function (name) {
                    text(options, 'li', paramLabel(current, name) + ': ' + String(action.params[name]));
                });
            }
            text(container, 'p', 'Current tracks: ' + now.track_count + '.');
            text(container, 'p', 'Running shuffles the playlist’s current tracks on Spotify. ' +
                'The new order is chosen when it runs, so it is not shown here.');
        } else if (context.kind === 'undo' || context.kind === 'snapshot_restore') {
            text(container, 'p', context.kind === 'undo' ? 'This restores the previous version saved in this session. ' +
                'It can include saved Workshop changes, not only shuffles.'
                : 'This restores the saved snapshot' + (context.snapshot && context.snapshot.created_at
                    ? ' from ' + timeLabel(context.snapshot.created_at) : '') +
                    ', compared with the playlist on Spotify now.');
            text(container, 'p', 'Current tracks: ' + now.track_count + '. After restoring: ' +
                action.desired_track_uris.length + '.');
            text(container, 'p', changeSummary(action.summary));
            text(container, 'p', context.kind === 'undo' ? 'Saved versions last only for this session. If the ' +
                'playlist or its saved history changes before you run, nothing is sent and you can review again.'
                : 'If the playlist changes before you run, nothing is sent and you can review again.');
        } else {
            var state = action.public ? 'public' : 'private';
            text(container, 'p', 'Current visibility: ' + (now.public ? 'public' : 'private') + '.');
            text(container, 'p', now.public === action.public
                ? 'The playlist is already ' + state + '. Running checks it again without changing it.'
                : 'Running makes the playlist ' + state + ' on Spotify.');
        }
        text(container, 'p', 'Run applies this to Spotify now. Cancel sends nothing.');
    }
    function resourceNames(claims) {
        var names = [];
        (claims || []).forEach(function (claim) {
            var id = claimId(claim);
            var resource = preparation && preparation.context && preparation.context.resources.find(function (item) {
                return item.resource === id;
            });
            var name = claim.indexOf('inbox:') === 0 ? 'the review inbox' : resource ? resource.resource_name : 'a playlist';
            if (!names.includes(name)) names.push(name);
        });
        return names.length ? 'touch ' + names.join(' and ') : 'overlap this action';
    }
    // Executor kinds describe their effects; random collection and shuffle results are never previewed.
    function executorReview(container, context) {
        var action = context.action;
        var collection = context.resources.find(function (resource) { return resource.role === 'collection'; });
        var steps = context.steps;
        if (context.kind === 'schedule') {
            text(container, 'p', 'Runs this routine once now: ' + steps.map(stepName).join(', then ') + '. A paused routine stays paused and its schedule does not change.');
        }
        if (steps.includes('raid')) {
            // Saved source names and this run's chosen playlists; a routine names neither.
            var sources = (Array.isArray(action.sources) ? action.sources : []).map(function (source) {
                return isObject(source) && typeof source.label === 'string' ? source.label : '';
            }).filter(Boolean);
            var extras = Array.isArray(action.extras) ? action.extras.length : 0;
            if (extras) sources.push(extras === 1 ? '1 playlist chosen for this run' : extras + ' playlists chosen for this run');
            text(container, 'p', 'Collects new tracks from ' + (sources.length ? sources.join(', ')
                : 'the saved discovery sources') + ' into ' + (collection ? collection.resource_name +
                ' and the review inbox' : 'the review inbox') + (steps.includes('drip') ? '.' : '. Collecting adds ' +
                'nothing to ' + context.target.name + '; you choose what to promote from the inbox.'));
        }
        if (steps.includes('shuffle') && isObject(action.algorithm)) {
            text(container, 'p', 'Shuffles the playlist’s current tracks with ' + action.algorithm.name +
                '. The new order is chosen when it runs.');
        }
        if (steps.includes('drip')) {
            var drip = isObject(action.drip) ? action.drip : action;
            text(container, 'p', drip.gate === 'closed' ? 'Adding collected tracks is turned off for this routine, ' +
                'so that step will not run.' : 'Adds up to ' + (count(drip.count) ? drip.count : 'the saved number of') +
                ' collected tracks from ' + (collection ? collection.resource_name : 'the collection') + ' to the top of ' +
                context.target.name + ' and marks them added in the inbox.' + (context.kind === 'drip' ||
                context.kind === 'raid_and_drip' ? ' This direct action runs even when automatic adding is paused.' : ''));
            if (steps[0] === 'raid') text(container, 'p', 'Adding does not run unless collecting completes.');
        }
        if (steps.includes('rotate') && isObject(action.rotation)) {
            var rotation = action.rotation;
            var archive = context.resources.find(function (resource) { return resource.role === 'archive'; });
            text(container, 'p', 'Swaps up to ' + rotation.count + ' tracks between ' + context.target.name + ' and ' +
                (archive ? archive.resource_name : 'its archive') + ', keeping the first ' + rotation.protect +
                ' tracks and at most ' + rotation.cap + ' tracks in the playlist. Tracks are chosen when it runs.');
        }
    }
    // One polite announcement per run start and finish, and per refusal; re-renders stay quiet.
    function announce(message) {
        if (!message) return;
        if (global.AppPanels && typeof global.AppPanels.announce === 'function') {
            global.AppPanels.announce(message);
            return;
        }
        var region = byId('live-operation-announcer');
        if (region) region.textContent = region.textContent === message ? message + '\u00a0' : message;
    }
    var outcomeWords = {complete: 'verified', not_required: 'no change needed', failed: 'not changed',
        unknown: 'could not be established', restored: 'restored to its earlier state'};
    var outcomeRank = ['unknown', 'restored', 'failed', 'complete', 'not_required'];
    // The app facts named after the playlists, each in one short phrase.
    var factPhrases = [
        {name: 'review inbox', done: 'up to date', has: function (kind) { return kind.indexOf('pending_') === 0; }},
        {name: 'saved version history', done: 'saved', has: function (kind) { return kind === 'session_history'; }},
        {name: 'run history', done: 'saved',
            has: function (kind) { return kind === 'execution_record' || kind === 'execution_status'; }},
        {name: 'playlist lock', done: 'released', has: function (kind) { return kind === 'coordination'; }},
    ];
    // A required fact the reply left out could not be verified, like an unknown one.
    function factPhrase(fact, op, required) {
        var entries = op.bookkeeping.filter(function (entry) { return fact.has(entry.kind); });
        var missing = required.some(function (kind) {
            return fact.has(kind) && !entries.some(function (entry) { return entry.kind === kind; });
        });
        if (!entries.length && !missing) return null;
        var status = function (state) { return function (entry) { return entry.status === state; }; };
        var word = missing || entries.some(status('unknown')) ? 'could not be verified'
            : entries.every(function (entry) { return successStates.includes(entry.status); }) ? fact.done
                : entries.some(status('failed')) ? 'not saved' : 'restored to its earlier state';
        return fact.name + ': ' + word;
    }
    // The target first, then each other written playlist, then the inbox, saved version
    // and run history facts. A verified run whose view did not update says so.
    function outcomeAnnouncement(record) {
        var intent = record.intent, op = record.operation;
        if (!op) return 'Attention: ' + rowName(record) + '. The outcome could not be established; do not repeat it.';
        // Provably nothing changed: say what its row says, never "could not be verified".
        if (record.noEffect) return 'Not applied: ' + rowName(record) + '. ' + (record.message || 'Nothing was applied.');
        var byResource = {}, order = [];
        op.resources.forEach(function (resource) {
            var current = byResource[resource.resource];
            if (!current) { order.push(resource.resource); byResource[resource.resource] = resource; return; }
            if (outcomeRank.indexOf(resource.status) < outcomeRank.indexOf(current.status)) byResource[resource.resource] = resource;
        });
        // Short of complete, a written playlist the reply left out could not be established
        // either (a complete executor run may plan no write for a playlist).
        if (!record.complete) {
            intent.writes.forEach(function (claim) {
                var id = /^(tracks|metadata):/.test(claim) ? claimId(claim) : null;
                if (id && !byResource[id]) { order.push(id); byResource[id] = {resource: id, status: 'unknown'}; }
            });
        }
        order.sort(function (a, b) { return (a === intent.targetId ? 0 : 1) - (b === intent.targetId ? 0 : 1); });
        var parts = order.map(function (id) {
            var resource = byResource[id];
            var name = resource.resource_name || intent.names[id] || (id === intent.targetId ? intent.targetName : id);
            return name + ': ' + (resource.started === false
                ? 'did not run' : outcomeWords[resource.status] || 'could not be established');
        });
        factPhrases.forEach(function (fact) {
            var phrase = factPhrase(fact, op, intent.requiredBookkeeping || []);
            if (phrase) parts.push(phrase);
        });
        if (op.bookkeeping.some(function (entry) { return entry.kind === 'source_resolution' && entry.status === 'failed'; })) {
            parts.push('some discovery sources could not be read');
        }
        var lead = record.complete && !record.displayAttention ? 'Complete: ' : 'Attention: ';
        var facts = parts.length ? parts.join('; ') + '.' : record.complete ? 'All required changes verified.'
            : 'The outcome could not be established.';
        return lead + rowName(record) + '. ' + facts + (record.unknown ? ' Do not repeat it.' : '') +
            (record.displayAttention ? ' This view could not update; check Spotify before another run.' : '');
    }
    function notice(message) {
        var review = byId('live-operation-review');
        if (!review) return;
        if (message) review.dataset.notice = message;
        else delete review.dataset.notice;
    }

    function classify(record, data, ok) {
        record.response = freeze(copy(data));
        var op = data.operation;
        var valid = op && ['complete', 'partial', 'unknown'].includes(op.aggregate_status) &&
            Array.isArray(op.resources) && Array.isArray(op.bookkeeping) &&
            op.resources.every(function (entry) { return entry && effectStates.includes(entry.status); }) &&
            op.bookkeeping.every(function (entry) { return entry && typeof entry.kind === 'string' &&
                effectStates.includes(entry.status); });
        if (!valid) throw new Error('Outcome unavailable');
        record.operation = record.response.operation;
        var entries = op.resources.concat(op.bookkeeping);
        record.complete = ok && data.success === true && op.aggregate_status === 'complete' &&
            entries.every(function (entry) { return successStates.includes(entry.status); }) &&
            requiredFactsPresent(record);
        record.aggregate = record.complete ? 'complete' : op.aggregate_status === 'complete' ? 'unknown' : op.aggregate_status;
        if (record.complete) return;
        // A verified run-history receipt is not a playlist/inbox effect. Keep
        // unknown receipts and every other app-state outcome in the no-effect proof.
        var effects = op.resources.concat(op.bookkeeping.filter(function (entry) {
            return entry.kind !== 'execution_record' || !successStates.includes(entry.status);
        }));
        record.noEffect = record.aggregate === 'partial' && effects.every(function (entry) {
            return ['failed', 'not_required'].includes(entry.status);
        });
        // Warn against repeating unless every fact the client requires is known: no derived unknown,
        // no unknown entry, no required bookkeeping missing, and an entry for every written playlist.
        var unknown = record.aggregate === 'unknown' || entries.some(function (entry) { return entry.status === 'unknown'; }) ||
            unknownBookkeeping(op, record.intent.requiredBookkeeping) || record.intent.writes.some(function (claim) {
                return /^(tracks|metadata):/.test(claim) && !op.resources.some(function (resource) {
                    return resource.resource === claimId(claim);
                });
            });
        record.unknown = !record.noEffect && unknown;
        if (!record.noEffect) {
            record.message = unknown
                ? 'Do not repeat this action to recover it. Check the named playlists and retain these details.'
                : 'This action did not fully complete. The results below show each verified change; another run ' +
                    'starts from a fresh review.';
        } else if (changedReview.includes(op.error_code)) {
            record.message = 'Nothing was sent because the playlist, its saved history or the reviewed action changed. ' +
                'Review it again to continue.';
        } else {
            record.message = ((data.result || data).execution_state === 'not_started' ? 'This attempt did not run. '
                : 'This attempt applied no playlist changes. ') + 'A new review will check current conditions again.';
        }
    }
    // Complete requires every bookkeeping fact the review required and, for direct
    // actions, the named target; an empty list cannot vacuously prove either. Executor
    // runs are complete through their aggregate and bookkeeping (a step may plan no write).
    function requiredFactsPresent(record) {
        var op = record.operation, intent = record.intent;
        var target = reviewedKinds[intent.kind].family === 'executor' || op.resources.some(function (resource) {
            return resource.resource === intent.targetId && successStates.includes(resource.status);
        });
        return target && intent.requiredBookkeeping.every(function (kind) {
            return op.bookkeeping.some(function (entry) {
                return entry.kind === kind && successStates.includes(entry.status);
            });
        });
    }
    async function send(spec, limitMs) {
        var controller = new AbortController(), timer;
        try {
            return await Promise.race([
                (async function () {
                    var options = {method: spec.method || 'POST', signal: controller.signal,
                        headers: {'X-Requested-With': 'XMLHttpRequest'}};
                    if (options.method === 'GET') options.cache = 'no-store';
                    else if (spec.encoding === 'form') {
                        options.body = new FormData();
                        Object.entries(spec.body).forEach(function (field) { options.body.append(field[0], field[1]); });
                    } else {
                        options.headers['Content-Type'] = 'application/json';
                        options.body = JSON.stringify(spec.body);
                    }
                    var response = await global.fetch(spec.url, options);
                    return {data: await response.json(), ok: response.ok};
                })(),
                new Promise(function (_, reject) {
                    timer = setTimeout(function () { controller.abort(); reject(new Error('Timed out')); }, limitMs);
                }),
            ]);
        } finally { clearTimeout(timer); }
    }
    function lost(record) {
        record.aggregate = 'unknown';
        record.message = 'The outcome or response could not be established. Spotify may have changed. ' +
            'Do not repeat this action. Retain these details and check the playlist before taking another action.';
    }
    // A frozen view for callers: intent, response and observations are already deep-frozen.
    function snapshot(record) {
        return Object.freeze(Object.assign({}, record, {
            claims: freeze(copy(record.claims)),
            observations: Object.freeze(record.observations.slice()),
        }));
    }
    // ---- Reviewed actions ----
    function inputRevision(kind, subject) {
        return config.getInputRevision ? config.getInputRevision(kind, subject) : 0;
    }
    function pageContext() {
        var context = null;
        // An unreadable page state fails closed: nothing new starts.
        try { context = config.getContext ? config.getContext() : null; }
        catch (_) { return {busy: true, draft: [], attention: []}; }
        return {busy: Boolean(context && context.busy),
            draft: context && Array.isArray(context.draft) ? context.draft : [],
            attention: context && Array.isArray(context.attention) ? context.attention : []};
    }
    // Busy here means page writes in flight; a page read never holds navigation.
    function pageNavigation() {
        var state = null;
        // An unreadable page state asks before leaving.
        try { state = config.navigationState ? config.navigationState() : null; }
        catch (_) { return {draft: false, attention: true, busy: false}; }
        return {draft: Boolean(state && state.draft), attention: Boolean(state && state.attention),
            busy: Boolean(state && state.busy)};
    }
    function overlap(writes, claims) {
        return claims.filter(function (claim) { return writes.includes(claim); });
    }
    function scalarBody(kind, body) {
        body = body === undefined ? {} : body;
        if (Object.prototype.toString.call(body) !== '[object Object]') throw new Error('Reviewed actions require copied fields');
        var keys = Object.keys(body), shape = reviewedKinds[kind].body;
        if (shape === 'visibility') {
            if (keys.length !== 1 || typeof body.public !== 'boolean') {
                throw new Error('Visibility review requires an explicit true or false');
            }
            return {public: body.public};
        }
        if (shape === 'sources') {
            if (keys.some(function (key) { return key !== 'source_playlist_ids'; })) throw new Error('Unsupported collect options');
            if (!keys.length) return {};
            var ids = body.source_playlist_ids;
            if (!Array.isArray(ids) || !ids.length || ids.some(function (id) { return typeof id !== 'string' || !id; })) {
                throw new Error('Choose at least one discovery source');
            }
            return {source_playlist_ids: ids.slice()};
        }
        if (shape === 'none') {
            if (keys.length) throw new Error('This action takes no options');
            return {};
        }
        // Form kinds keep their copied scalar fields, including the form's CSRF token.
        var values = {};
        keys.forEach(function (name) {
            if (typeof body[name] !== 'string') throw new Error('Form actions require copied scalar fields');
            if (Object.values(bindingFields).includes(name)) throw new Error('Review bindings come from the server');
            values[name] = body[name];
        });
        if (shape === 'shuffle' && !values.algorithm) throw new Error('Choose a shuffle method before review');
        return values;
    }
    function contextSpec(current) {
        var definition = reviewedKinds[current.kind];
        if (definition.context) {
            return {method: 'GET', url: definition.context(encodeURIComponent(current.subject))};
        }
        var body = {kind: current.kind};
        if (definition.body === 'visibility') body.public = current.body.public;
        else if (definition.body === 'shuffle') {
            body.form = {};
            formFields.forEach(function (name) {
                if (Object.prototype.hasOwnProperty.call(current.body, name)) body.form[name] = current.body[name];
            });
        } else if (current.body.source_playlist_ids) body.source_playlist_ids = current.body.source_playlist_ids.slice();
        return {url: '/api/playlist/' + encodeURIComponent(current.subject) + '/live-context', encoding: 'json', body: body};
    }
    function uriList(value) {
        return Array.isArray(value) && value.every(function (uri) { return typeof uri === 'string' && trackUri.test(uri); });
    }
    var roles = ['target', 'collection', 'archive', 'inbox'];
    var writeTypes = ['track_order', 'track_membership', 'inbox_state', 'metadata'];
    function validResource(resource, targetId) {
        if (!isObject(resource) || typeof resource.resource !== 'string' || !resource.resource ||
            typeof resource.resource_name !== 'string' || !roles.includes(resource.role)) return false;
        var inbox = resource.role === 'inbox';
        if (resource.domain !== (inbox ? 'inbox' : 'spotify')) return false;
        if (inbox && (resource.resource !== targetId || resource.mutation_type !== 'inbox_state')) return false;
        if (!inbox && !playlistId.test(resource.resource)) return false;
        if (resource.role === 'target' && resource.resource !== targetId) return false;
        if (resource.access === 'read') return resource.mutation_type === null && resource.field === null;
        return resource.access === 'write' && writeTypes.includes(resource.mutation_type) &&
            resource.field === (resource.mutation_type === 'metadata' ? 'public' : null);
    }
    // A restore shows the fresh current order and the exact order it will write.
    function validRestore(now, action) {
        var summary = action.summary;
        return count(now.track_count) && uriList(now.track_uris) && now.track_uris.length === now.track_count &&
            uriList(action.desired_track_uris) && isObject(summary) && count(summary.added_count) &&
            count(summary.removed_count) && typeof summary.order_changed === 'boolean';
    }
    // Library kinds (shuffle, undo, privacy) must match their exact shapes.
    function validLibraryContext(context, current) {
        if (typeof context.current.loaded_at !== 'string' || context.resources.length !== 1) return false;
        var resource = context.resources[0];
        var mutations = {shuffle: ['track_order'], undo: ['track_order', 'track_membership'], privacy: ['metadata']};
        if (resource.role !== 'target' || resource.access !== 'write' ||
            !mutations[current.kind].includes(resource.mutation_type)) return false;
        var bookkeeping = current.kind === 'privacy' ? [] : ['session_history'];
        if (JSON.stringify(context.required_bookkeeping) !== JSON.stringify(bookkeeping)) return false;
        var binding = context.review_binding, undo = current.kind === 'undo';
        if (undo ? !(digest.test(binding.before_order) && digest.test(binding.history))
            : binding.before_order !== null || binding.history !== null) return false;
        var now = context.current, action = context.action;
        if (current.kind === 'shuffle') {
            return count(now.track_count) && isObject(action.algorithm) && action.algorithm.id === current.body.algorithm &&
                typeof action.algorithm.name === 'string' && isObject(action.params) &&
                Object.values(action.params).every(function (value) {
                    return ['string', 'number', 'boolean'].includes(typeof value);
                });
        }
        if (undo) {
            return validRestore(now, action) && Number.isInteger(action.current_index) && action.current_index >= 1 &&
                action.selected_index === action.current_index - 1;
        }
        return typeof now.public === 'boolean' && action.public === current.body.public;
    }
    function validExecutorContext(context, current) {
        var binding = context.review_binding;
        if (binding.before_order !== null || binding.history !== null) return false;
        if (!Array.isArray(context.steps) || !context.steps.length ||
            context.steps.some(function (step) { return typeof step !== 'string'; })) return false;
        if (current.kind === 'schedule') {
            return isObject(context.schedule) && context.schedule.id === current.subject &&
                typeof context.schedule.job_type === 'string' && context.required_bookkeeping.includes('execution_record');
        }
        return JSON.stringify(context.steps) === JSON.stringify(reviewedKinds[current.kind].steps) &&
            context.required_bookkeeping.includes('execution_record') &&
            context.resources.some(function (resource) { return resource.role === 'inbox' && resource.access === 'write'; });
    }
    function validSnapshotContext(context, current) {
        var binding = context.review_binding;
        return digest.test(binding.before_order) && binding.history === null &&
            isObject(context.snapshot) && context.snapshot.id === current.subject &&
            JSON.stringify(context.required_bookkeeping) === JSON.stringify(['session_history']) &&
            context.resources.some(function (resource) {
                return resource.role === 'target' && resource.access === 'write' &&
                    ['track_order', 'track_membership'].includes(resource.mutation_type);
            }) && validRestore(context.current, context.action);
    }
    var validators = {library: validLibraryContext, snapshot: validSnapshotContext, executor: validExecutorContext};
    function validContext(context, current) {
        if (!isObject(context) || context.version !== 1 || context.kind !== current.kind) return null;
        var target = context.target;
        if (!isObject(target) || typeof target.id !== 'string' || !playlistId.test(target.id) ||
            typeof target.name !== 'string' || !target.name) return null;
        // A routine or snapshot names its own target; a descriptor's target must agree.
        if (current.targetId && target.id !== current.targetId) return null;
        if (typeof context.label !== 'string' || !context.label) return null;
        if (!Array.isArray(context.resources) || !context.resources.length ||
            !context.resources.every(function (resource) { return validResource(resource, target.id); })) return null;
        if (!Array.isArray(context.required_bookkeeping) || context.required_bookkeeping.some(function (kind) {
            return typeof kind !== 'string';
        })) return null;
        var binding = context.review_binding;
        if (!isObject(binding) || binding.version !== 1 || !digest.test(binding.scope)) return null;
        if (!isObject(context.current) || !isObject(context.action)) return null;
        var valid = validators[reviewedKinds[current.kind].family](context, current);
        // The parsed reply belongs to this page alone, so it is frozen in place.
        return valid ? freeze(context) : null;
    }
    function isCurrent(current) { return preparation === current; }
    function finish(current, value, message) {
        if (!isCurrent(current)) return;
        preparation = null;
        notice(message || '');
        current.resolve(value);
        render();
        // A refusal is heard and keeps focus in the view that explains it.
        if (message) { announce(message); focusPanelHeading(); }
    }
    function cancelPreparation(message) {
        if (preparation) finish(preparation, null, message);
    }
    var attentionNotice = 'Another result on this page needs attention before this action can run.';
    var busyRefusal = 'Another request on this page is still running. Nothing was sent; review again when it finishes.';
    var draftRefusal = 'Your draft changed after this review. Nothing was sent. Review again.';
    async function openPageAttention() {
        if (config.openAttention) {
            try { await config.openAttention(); } catch (_) { /* recovery view unavailable; facts are retained */ }
        }
    }
    async function loadContext(current) {
        current.phase = 'loading'; current.error = null; current.context = null;
        renderPanel(); focusPanelHeading();
        var reply = null;
        try { reply = await send(contextSpec(current), readTimeoutMs); } catch (_) { reply = null; }
        if (!isCurrent(current)) return;
        if (inputRevision(current.kind, current.subject) !== current.revision) {
            finish(current, null, 'The options changed while the review loaded. Nothing was sent.');
            return;
        }
        var data = reply && isObject(reply.data) ? reply.data : null;
        var context = reply && reply.ok && data && data.success === true ? validContext(data.context, current) : null;
        if (!context) {
            current.phase = 'error';
            current.error = data && typeof data.message === 'string' && data.message ? data.message
                : 'The current playlist could not be read for review.';
            renderPanel(); focusPanelHeading();
            return;
        }
        var writes = writesOf(context), page = pageContext();
        if (busy || page.busy) { finish(current, null, busyRefusal); return; }
        if (recordConflict(writes)) { finish(current, null, attentionNotice); return; }
        if (overlap(writes, page.attention).length) {
            finish(current, null, attentionNotice);
            await openPageAttention();
            return;
        }
        current.context = context; current.targetId = context.target.id;
        current.conflict = overlap(writes, page.draft);
        current.phase = current.conflict.length && config.resolveConflict ? 'conflict' : 'review';
        if (current.conflict.length && !config.resolveConflict) {
            finish(current, null, 'Save or discard your draft changes before this action can run.');
            return;
        }
        renderPanel(); focusPanelHeading();
    }
    function descriptorSubject(kind, descriptor) {
        var subject = reviewedKinds[kind].subject;
        if (subject === 'target') {
            if (typeof descriptor.targetId !== 'string' || !playlistId.test(descriptor.targetId)) return null;
            return descriptor.targetId;
        }
        var id = subject === 'schedule' ? descriptor.scheduleId : descriptor.snapshotId;
        if (!Number.isInteger(id) || id < 1) return null;
        if (descriptor.targetId !== undefined &&
            (typeof descriptor.targetId !== 'string' || !playlistId.test(descriptor.targetId))) return null;
        return id;
    }
    async function prepare(descriptor) {
        descriptor = descriptor || {};
        var kind = descriptor.kind;
        var subject = Object.prototype.hasOwnProperty.call(reviewedKinds, kind) ? descriptorSubject(kind, descriptor) : null;
        if (subject === null) throw new Error('Unsupported reviewed action');
        var body = scalarBody(kind, descriptor.body);
        var targetId = typeof descriptor.targetId === 'string' ? descriptor.targetId : null;
        if (busy || leaveResolve) { await openResults(descriptor.trigger); return null; }
        var page = pageContext();
        if (page.busy) { notify('Wait for the current playlist update to finish.'); return null; }
        cancelPreparation();
        var anticipated = anticipatedWrites(kind, targetId);
        if (recordConflict(anticipated)) { await openResults(descriptor.trigger); return null; }
        if (overlap(anticipated, page.attention).length) { await openPageAttention(); return null; }
        var current = {kind: kind, subject: subject, targetId: targetId, body: freeze(body),
            revision: inputRevision(kind, subject), trigger: descriptor.trigger || null, phase: 'loading',
            context: null, conflict: []};
        var settled = new Promise(function (resolve) { current.resolve = resolve; });
        preparation = current;
        notice('');
        renderPanel();
        if (!await showReview(current)) return settled;
        if (isCurrent(current)) loadContext(current);
        return settled;
    }
    async function showReview(current) {
        if (!global.AppPanels || !global.document) return true;
        try {
            var opened = await global.AppPanels.open('live-operation-panel',
                Object.assign({trigger: current.trigger}, panelOptions));
            if (!opened) { finish(current, null); return false; }
        } catch (_) { finish(current, null); return false; }
        return true;
    }
    // A conflicting draft is resolved through the page's own common review; the action
    // is then reviewed again from fresh facts and still needs its separate Run.
    async function resolveDraft(choice) {
        var current = preparation;
        if (!current || current.phase !== 'conflict' || !config.resolveConflict) return;
        current.phase = 'resolving';
        renderPanel();
        var resolved = false;
        try { resolved = await config.resolveConflict({type: 'draft', choice: choice}); } catch (_) { resolved = false; }
        if (!isCurrent(current)) return;
        if (resolved !== true) {
            // Cancelled, superseded, or saved with work still needing attention: the action waits.
            var waiting = 'Your draft is still open or needs attention, so this action was not reviewed. Nothing was sent.';
            finish(current, null, waiting);
            // Announced by finish; the toast is for eyes only, since this view may be closed.
            notify(waiting, false);
            // Return focus to the action only when no panel took over (an open review or results keeps it).
            var openPanel = global.document && document.querySelector('[data-app-panel]:not([hidden])');
            if (!openPanel && current.trigger && current.trigger.isConnected && current.trigger.focus) {
                current.trigger.focus();
            }
            return;
        }
        current.revision = inputRevision(current.kind, current.subject);
        current.phase = 'loading';
        if (!await showReview(current) || !isCurrent(current)) return;
        loadContext(current);
    }
    function acceptReview() {
        var current = preparation;
        if (!current) return;
        if (current.phase === 'error') { loadContext(current); return; }
        if (current.phase === 'conflict') { resolveDraft('save'); return; }
        if (current.phase !== 'review') return;
        if (inputRevision(current.kind, current.subject) !== current.revision) {
            finish(current, null, 'The options changed after this review. Nothing was sent. Review the current options again.');
            return;
        }
        var writes = writesOf(current.context), page = pageContext();
        if (busy || page.busy) { finish(current, null, busyRefusal); return; }
        if (recordConflict(writes) || overlap(writes, page.attention).length) {
            finish(current, null, attentionNotice);
            return;
        }
        if (overlap(writes, page.draft).length) { finish(current, null, draftRefusal); return; }
        current.phase = 'accepted';
        var handle = Object.freeze({kind: current.kind, subject: current.subject});
        handles.set(handle, current);
        current.resolve(handle);
    }
    // Validation fixed which bindings each kind carries; every one returned goes back.
    function bindingBody(current) {
        var binding = current.context.review_binding, body = Object.assign({}, current.body);
        Object.keys(bindingFields).forEach(function (name) {
            if (binding[name] !== null) body[bindingFields[name]] = binding[name];
        });
        return body;
    }
    async function run(handle) {
        var current = handle && typeof handle === 'object' ? handles.get(handle) : undefined;
        if (!current) return null;
        // Consume before any await: a second activation of this handle is inert.
        handles.delete(handle);
        if (!isCurrent(current) || current.phase !== 'accepted') return null;
        preparation = null;
        notice('');
        if (busy || leaveResolve) { render(); return null; }
        if (inputRevision(current.kind, current.subject) !== current.revision) {
            var changed = 'The options changed after this review. Nothing was sent. Review the current options again.';
            notice(changed);
            render();
            announce(changed); focusPanelHeading();
            return null;
        }
        var context = current.context, kind = current.kind, writes = writesOf(context);
        if (recordConflict(writes)) { await openResults(current.trigger); return null; }
        var spec = {url: reviewedKinds[kind].url(encodeURIComponent(current.subject)),
            encoding: reviewedKinds[kind].encoding, body: bindingBody(current)};
        // Reviewed facts only: the private form copy and CSRF token stay out of records.
        var names = {};
        context.resources.forEach(function (resource) { names[resource.resource] = resource.resource_name; });
        var intent = freeze({key: kind + ':' + current.subject, kind: kind, subject: current.subject, label: context.label,
            targetId: context.target.id, targetName: context.target.name, action: context.action, writes: writes,
            names: names, steps: context.steps || [], requiredBookkeeping: context.required_bookkeeping});
        busy = true;
        var record = {operationId: nextOperationId++, intent: intent, state: 'busy', complete: false,
            observations: [], noEffect: false};
        records.push(record);
        try {
            render();
            announce('Running: ' + rowName(record) + '.');
            if (config.onBusy) config.onBusy(true, snapshot(record));
            await openResults(current.trigger);
            // The final Run control is replaced by results; keep focus on the panel.
            focusPanelHeading();
            var list = byId('live-operation-results');
            if (list && list.parentElement) list.parentElement.scrollTop = 0;
            try {
                var reply = await send(spec, timeoutMs);
                classify(record, reply.data, reply.ok);
            } catch (_) { lost(record); }
            record.state = 'finished';
            derive(record);
            if (record.complete && config.afterComplete) {
                try { await config.afterComplete(snapshot(record)); }
                catch (_) {
                    record.refreshMessage = 'The action is verified, but this view could not refresh. ' +
                        'Keep your current work and check Spotify before another run.';
                    // Remember which display update failed so "Update this view" retries that one.
                    record.displayAttention = 'complete';
                }
            }
            derive(record);
            announce(outcomeAnnouncement(record));
            return snapshot(record);
        } finally {
            busy = false;
            if (config.onBusy) config.onBusy(false, snapshot(record));
            render();
        }
    }
    // Each written playlist is checked against its own retained order (the last step's
    // intent); a retained review order is the fallback for restores; otherwise unavailable.
    function trackChecks(record) {
        var op = record.operation, intent = record.intent;
        // Only playlists still unresolved are read again; a verified one cannot change the record.
        return claimsOf(record).filter(function (claim) { return claim.indexOf('tracks:') === 0; }).map(function (claim) {
            var id = claimId(claim);
            var entries = op ? op.resources.filter(function (resource) {
                return resource.resource === id && uriList(resource.desired_track_uris);
            }) : [];
            var desired = entries.length ? entries[entries.length - 1].desired_track_uris.slice()
                : id === intent.targetId && uriList(intent.action.desired_track_uris)
                    ? intent.action.desired_track_uris.slice() : null;
            return {id: id, desired: desired};
        });
    }
    function observe(record, data, desired, resourceId) {
        var privacy = record.intent.kind === 'privacy';
        var op = isObject(data) && data.inspection_only === true ? data.operation : null;
        if (!isObject(op) || op.kind !== (privacy ? 'privacy' : 'track_inspection') ||
            !Array.isArray(op.resources) || op.resources.length > 1) return 'failed';
        var resource = op.resources[0];
        if (resource && (!isObject(resource) || resource.resource !== resourceId)) return 'failed';
        if (data.success === true && op.aggregate_status === 'complete' && resource && resource.status === 'not_required') {
            if (privacy) {
                var current = resource.current_metadata;
                return isObject(current) && current.public === desired && data.public === desired ? 'match' : 'failed';
            }
            return Array.isArray(resource.current_track_uris) &&
                JSON.stringify(resource.current_track_uris) === JSON.stringify(desired) ? 'match' : 'failed';
        }
        if (op.error_code === 'intent_unavailable') return 'unavailable';
        if (op.error_code === 'intent_not_observed' || op.error_code === 'verification_failed') return 'mismatch';
        return 'failed';
    }
    async function inspect(record, resourceId, body, desired) {
        try {
            var reply = await send({url: '/api/playlist/' + encodeURIComponent(resourceId) + '/inspect-live',
                encoding: 'json', body: body}, readTimeoutMs);
            var status = observe(record, reply.data, desired, resourceId);
            return {id: resourceId, status: status === 'match' && !reply.ok ? 'failed' : status};
        } catch (_) { return {id: resourceId, status: 'failed'}; }
    }
    var checkOrder = ['failed', 'mismatch', 'unavailable', 'match'];
    async function reconcile(operationId) {
        var record = records.find(function (item) { return item.operationId === operationId; });
        if (!record || record.state === 'busy' || checking.has(operationId)) return null;
        var privacy = record.intent.kind === 'privacy', desiredPublic = record.intent.action.public;
        var checks = privacy ? [{id: record.intent.targetId}] : trackChecks(record);
        if (!checks.length) return null;
        checking.add(operationId);
        render();
        // Each playlist is read separately; the reads are independent, so they run together.
        var results = await Promise.all(checks.map(function (check) {
            return privacy ? inspect(record, check.id, {kind: 'privacy', desired_public: desiredPublic}, desiredPublic)
                : inspect(record, check.id, check.desired ? {kind: 'tracks', desired_track_uris: check.desired}
                    : {kind: 'tracks'}, check.desired);
        }));
        var status = checkOrder.find(function (state) {
            return results.some(function (result) { return result.status === state; });
        }) || 'failed';
        var observation = {status: status, checkedAt: new Date().toISOString(), resources: results};
        record.observations.push(observation);
        checking.delete(operationId);
        derive(record);
        // Whether this check left nothing of the run unresolved.
        observation.settled = record.settled;
        freeze(observation);
        // A visibility match shows the requested state, which the check just observed.
        if (status === 'match' && privacy && config.afterObservation) {
            try { await config.afterObservation(snapshot(record)); }
            catch (_) {
                record.displayAttention = 'observation';
                record.refreshMessage = 'The current visibility is verified, but this view could not update.';
            }
        }
        render();
        // The status line is visible text only, so each finished check is announced.
        announce('Checked ' + rowName(record) + ': ' + observationLabel(record, observation, true) +
            (record.displayAttention ? ' ' + record.refreshMessage : '') +
            (hasAttention() ? '' : ' No results need attention.'));
        return snapshot(record);
    }
    // Retry only the display update that failed; it never re-sends anything to Spotify.
    async function refreshDisplay(operationId) {
        var record = records.find(function (item) { return item.operationId === operationId; });
        if (!record || !record.displayAttention || refreshing.has(operationId)) return;
        var observation = record.observations[record.observations.length - 1], updated = false;
        refreshing.add(operationId);
        render();
        try {
            if (record.displayAttention === 'observation') {
                if (!config.afterObservation || !observation || observation.status !== 'match') {
                    throw new Error('No verified observation to show');
                }
                await config.afterObservation(snapshot(record));
            } else {
                if (!config.afterComplete) throw new Error('No display update');
                await config.afterComplete(snapshot(record));
            }
            record.displayAttention = false; record.refreshMessage = null;
            updated = true;
        } catch (_) { /* keep the verified result and its display attention */ }
        refreshing.delete(operationId);
        render();
        announce(updated ? 'View updated: ' + rowName(record) + '.' + (hasAttention() ? '' : ' No results need attention.')
            : 'This view still could not update: ' + rowName(record) + '.');
    }

    // ---- Navigation for reviewed pages ----
    // spoken false: the announcer already said it.
    function notify(message, spoken) {
        if (typeof global.showNotification === 'function') {
            global.showNotification(message, 'info', spoken === false ? {spoken: false} : undefined);
        }
    }
    // AppPanels passes each close reason to onClose. 'leave', 'discard', 'save' and
    // 'review' are explicit choices; Stay, Escape and any other close keep the page.
    function settleLeave(reason) {
        if (!leaveResolve) return;
        var resolve = leaveResolve;
        leaveResolve = null;
        if (reason === 'leave') {
            cancelPreparation(); resolve(true);
            return;
        }
        if (reason === 'discard') {
            cancelPreparation();
            Promise.resolve(config.resolveConflict ? config.resolveConflict({type: 'draft', choice: 'discard'}) : false)
                .then(function (done) { resolve(done === true); }, function () { resolve(false); });
            return;
        }
        resolve(false);
        if (reason === 'review') {
            cancelPreparation();
            (hasAttention() || !config.openAttention ? openResults(leaveTrigger) : openPageAttention())
                .catch(function () {});
        } else if (reason === 'save' && config.resolveConflict) {
            cancelPreparation();
            Promise.resolve(config.resolveConflict({type: 'draft', choice: 'save'})).catch(function () {});
        }
    }
    var leaveTrigger = null;
    var leaveCopy = {
        attention: 'Some results on this page still need attention. Leaving or reloading loses these details from ' +
            'this page. It does not cancel or undo anything already sent to Spotify.',
        draft: 'This page has draft changes that are not saved to Spotify. Review and save them, or discard them ' +
            'before you leave.',
    };
    function leaveVariant(attention, draft) {
        var textElement = byId('live-operation-leave-text');
        if (textElement) {
            textElement.textContent = attention ? leaveCopy.attention + (draft ? ' Unsaved draft changes would also ' +
                'be lost.' : '') : leaveCopy.draft;
        }
        [['live-operation-leave-review', attention], ['live-operation-leave-save', !attention && draft],
            ['live-operation-leave-discard', !attention && draft], ['live-operation-leave-confirm', attention]]
            .forEach(function (entry) {
                var button = byId(entry[0]);
                if (button) button.hidden = !entry[1];
            });
    }
    function askLeave(detail, attention, draft) {
        if (!global.AppPanels || !byId('live-operation-leave')) return false;
        leaveVariant(attention, draft);
        leaveTrigger = (detail && detail.element) || document.activeElement;
        var decision = new Promise(function (resolve) { leaveResolve = resolve; });
        global.AppPanels.open('live-operation-leave', {modal: true,
            trigger: (detail && detail.element) || document.activeElement, onClose: settleLeave})
            .then(function (opened) { if (!opened) settleLeave('stay'); },
                function () { settleLeave('stay'); });
        return decision;
    }
    function chooseLeave(choice) {
        if (!leaveResolve) return;
        global.AppPanels.requestClose(choice).then(function (closed) {
            if (!closed) settleLeave(choice);
        }, function () { settleLeave(choice); });
    }
    function navigationGuard(detail) {
        if (leaveResolve) return false;
        var page = pageNavigation(), attention = hasAttention() || page.attention;
        if (busy || page.busy) { notify(busyNotice); return false; }
        if (!attention && !page.draft) { cancelPreparation(); return true; }
        return askLeave(detail, attention, page.draft);
    }
    function bindReviewedControls() {
        if (controlsBound || !global.document) return;
        controlsBound = true;
        var runButton = byId('live-operation-run');
        if (runButton) runButton.addEventListener('click', acceptReview);
        var discardButton = byId('live-operation-discard');
        if (discardButton) discardButton.addEventListener('click', function () { resolveDraft('discard'); });
        var list = byId('live-operation-results');
        if (list) list.addEventListener('click', function (event) {
            var target = event.target && event.target.closest ? event.target.closest('button') : null;
            if (!target || !target.dataset) return;
            if (target.dataset.liveCheck) reconcile(Number(target.dataset.liveCheck));
            else if (target.dataset.liveRefresh) refreshDisplay(Number(target.dataset.liveRefresh));
        });
        var summary = byId('live-operation-summary');
        if (summary) summary.addEventListener('click', function (event) {
            var link = event.target && event.target.closest ? event.target.closest('a[data-live-row]') : null;
            if (!link) return;
            event.preventDefault();
            var row = byId('live-operation-' + link.dataset.liveRow);
            if (row && row.focus) row.focus();
        });
        var openButton = byId('live-operation-open');
        if (openButton) openButton.addEventListener('click', function () { openResults(openButton); });
        [['live-operation-leave-review', 'review'], ['live-operation-leave-save', 'save'],
            ['live-operation-leave-stay', 'stay'], ['live-operation-leave-discard', 'discard'],
            ['live-operation-leave-confirm', 'leave']].forEach(function (entry) {
            var button = byId(entry[0]);
            if (button) button.addEventListener('click', function () { chooseLeave(entry[1]); });
        });
    }
    function configure(options) {
        config = options || {}; timeoutMs = config.timeoutMs || defaultTimeoutMs;
        readTimeoutMs = config.readTimeoutMs || defaultReadTimeoutMs;
        bindReviewedControls();
        if (!guardInstalled && global.AppNavigation) {
            guardInstalled = true;
            global.AppNavigation.addGuard(navigationGuard);
            global.AppNavigation.addUnloadCheck(unloadCheck);
        }
    }
    // The browser's own warning (AppNavigation's one unload listener) covers what the guard
    // asks about. A navigation the guard just approved warns only while a run is in flight,
    // which leaving cannot stop.
    function unloadCheck(approved) {
        var page = pageNavigation();
        return busy || (!approved && (page.busy || hasAttention() || page.draft || page.attention));
    }
    global.PlaylistOperations = {configure: configure, run: run, prepare: prepare, reconcile: reconcile,
        openResults: openResults, hasAttention: hasAttention, isBlocked: isBlocked,
        hasUnresolved: hasUnresolved, setControlDisabled: setControlDisabled,
        // Every unresolved claim on the page at once, for pages checking several.
        unresolvedClaims: function () { return Array.from(unresolved().claims); },
        isBusy: function () { return busy; },
        isPreparing: function () { return Boolean(preparation); },
        getRecords: function () { return records.map(snapshot); },
        syncControls: syncControls};
})(window);
