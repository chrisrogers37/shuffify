/** Workshop target protocol, immutable review intent, and scoped source results. */
(function (global) {
    'use strict';
    var playlistId = null;
    var inFlight = false;
    var timeoutMs = 30000;

    function sameOrder(a, b) {
        return Array.isArray(a) && Array.isArray(b) && a.length === b.length &&
            a.every(function (uri, i) { return uri === b[i]; });
    }

    function uncertain(message) {
        return {success: false, target_status: 'unknown', error_code: 'write_unverified',
            requires_refresh: true, retryable: false, confirmed_batches: 0,
            total_batches: 0, message: message ||
                'Spotify may have changed. Check the playlist before trying again.'};
    }

    async function request(path, options, isWrite) {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, timeoutMs);
        try {
            var response = await global.fetch(path, Object.assign({}, options, {
                signal: controller.signal,
                headers: Object.assign({'X-Requested-With': 'XMLHttpRequest'}, options.headers),
            }));
            var data = await response.json();
            if (!data || typeof data !== 'object') throw new Error('Invalid response');
            data.http_status = response.status;
            data.http_ok = response.ok;
            if (!response.ok) data.success = false;
            return data;
        } catch (_) {
            return isWrite ? uncertain() : {
                success: false, target_status: 'not_written', error_code: 'upstream_read_failed',
                requires_refresh: true, retryable: false,
                message: 'Could not check Spotify. Your draft is still here. Check again when ready.',
            };
        } finally {
            clearTimeout(timer);
        }
    }


    function freeze(value) {
        if (value && typeof value === 'object') {
            Object.keys(value).forEach(function (key) { freeze(value[key]); });
            Object.freeze(value);
        }
        return value;
    }

    function summarize(baseline, desired) {
        var before = new Map(), after = new Map();
        baseline.forEach(function (uri) { before.set(uri, (before.get(uri) || 0) + 1); });
        desired.forEach(function (uri) { after.set(uri, (after.get(uri) || 0) + 1); });
        var added = 0, removed = 0;
        after.forEach(function (count, uri) { added += Math.max(0, count - (before.get(uri) || 0)); });
        before.forEach(function (count, uri) { removed += Math.max(0, count - (after.get(uri) || 0)); });
        // Compare the shared occurrences in sequence, retaining duplicate counts.
        function sharedOrder(uris, other) {
            var used = new Map();
            return uris.filter(function (uri) {
                var count = (used.get(uri) || 0) + 1; used.set(uri, count);
                return count <= (other.get(uri) || 0);
            });
        }
        return {added: added, removed: removed,
            orderChanged: !sameOrder(sharedOrder(baseline, after), sharedOrder(desired, before))};
    }

    async function prepare(revision) {
        // Capture before reading context: later edits invalidate, never alter, this review.
        var captured = JSON.parse(JSON.stringify(revision));
        var desired = new Set(captured.desired);
        function removalUris(task) {
            return task.kind === 'raids' ? task.removalUris :
                task.kind === 'restorations' ? task.body.track_uris : [];
        }
        var missingStagedTrack = captured.sources.some(function (task) {
            var uris = removalUris(task);
            return !Array.isArray(uris) || (task.kind === 'raids' && uris.length !== task.body.track_ids.length) ||
                uris.some(function (uri) { return typeof uri !== 'string' || !desired.has(uri); });
        });
        if (missingStagedTrack) return {success: false, error_code: 'staged_tracks_missing',
            message: 'Some staged tracks are missing from your draft. Add them back or discard draft staging, then review again.'};
        var raid = captured.sources.some(function (task) { return task.kind === 'raids'; });
        var archive = captured.sources.some(function (task) { return task.kind !== 'raids'; });
        var context = await request('/api/workshop/' + encodeURIComponent(playlistId) +
            '/source-context?raid=' + Number(raid) + '&archive=' + Number(archive),
            {method: 'GET', cache: 'no-store'}, false);
        if (context.success !== true || !context.target || context.target.id !== playlistId ||
            typeof context.target.name !== 'string' || (raid && context.raid === undefined) ||
            (archive && (!context.archive || !Array.isArray(context.archive.baseline_track_uris)))) {
            return {success: false, message: context.message ||
                'Could not resolve the playlists for this save. Your draft is still here. Review again.'};
        }
        var tasks = [];
        captured.sources.forEach(function (task) {
            var source = task.kind === 'raids' ? context.raid : context.archive;
            if (source && (typeof source.id !== 'string' || typeof source.name !== 'string')) {
                throw new Error('Invalid source identity');
            }
            var body = Object.assign({}, task.body, {
                source_playlist_id: source ? source.id : null,
                target_track_uris: captured.desired.slice(),
            });
            // A collection may also be the archive. An append's baseline must
            // reflect every earlier removal on that resource, with all copies
            // removed. The backend derives raid removal URIs from IDs.
            var precedingRemovals = task.kind === 'archive' ? tasks.filter(function (earlier) {
                return earlier.resource === source.id && earlier.removalUris.length > 0;
            }) : [];
            if (task.kind === 'archive') {
                var removed = new Set(precedingRemovals.flatMap(function (earlier) { return earlier.removalUris; }));
                body.baseline_track_uris = context.archive.baseline_track_uris.filter(function (uri) {
                    return !removed.has(uri);
                });
            }
            tasks.push({kind: task.kind, path: task.path, targetId: context.target.id,
                resource: source ? source.id : null,
                resourceName: source ? source.name : 'Review inbox for ' + context.target.name,
                removalUris: removalUris(task), body: body,
                dependsOn: precedingRemovals.map(function (earlier) { return earlier.kind; })});
        });
        return {success: true, review: freeze({baseline: captured.baseline, desired: captured.desired,
            staging: captured.sources, target: context.target, sources: tasks,
            summary: summarize(captured.baseline, captured.desired)})};
    }

    async function runSource(task, inspectOnly) {
        var result = await request('/api/playlist/' + encodeURIComponent(task.targetId) + task.path, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(Object.assign({}, task.body, inspectOnly ? {inspect_only: true} : {})),
        }, true);
        var valid = result.http_ok !== undefined && result.resource === task.resource &&
            ['complete', 'not_required', 'failed', 'unknown'].includes(result.status);
        var complete = valid && result.http_ok && result.success === true &&
            ['complete', 'not_required'].includes(result.status);
        if (complete) return result;
        if (!valid || ['complete', 'not_required'].includes(result.status)) return {
            success: false, resource: task.resource, status: 'unknown', retryable: false,
            error_code: 'cleanup_unverified',
            message: inspectOnly ? 'The check could not establish this step’s result.' :
                'This step may have changed Spotify. Check its contents before taking further action.',
        };
        var databaseOnly = task.kind === 'raids' && result.status === 'unknown' &&
            ['database_update_failed', 'database_update_pending'].includes(result.error_code) &&
            ['complete', 'not_required'].includes(result.source_status) &&
            ['failed', 'pending'].includes(result.database_status);
        result.retryable = result.retryable === true && (result.status === 'failed' || databaseOnly);
        return result;
    }

    global.WorkshopSave = {
        prepare: prepare,
        summarize: summarize,
        runSource: runSource,
        configure: function (id) { playlistId = id; },
        sameOrder: sameOrder,
        submit: async function (revision) {
            if (inFlight) return {success: false, target_status: 'not_written',
                error_code: 'operation_in_progress', requires_refresh: false, retryable: true,
                message: 'A save is already in progress.'};
            // Copy before the first await: later edits cannot alter the submitted revision.
            var baseline = Object.freeze(revision.baselineTrackUris.slice());
            var desired = Object.freeze(revision.trackUris.slice());
            inFlight = true;
            try {
                var result = await request('/api/workshop/' + encodeURIComponent(playlistId) + '/commit', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({baseline_track_uris: baseline, track_uris: desired}),
                }, true);
                if (result.success) {
                    if (!['saved', 'already_applied'].includes(result.target_status) ||
                        !sameOrder(result.saved_track_uris, desired)) return uncertain();
                } else if (!['not_written', 'partial', 'unknown'].includes(result.target_status)) {
                    return uncertain(result.http_status === 401 || result.http_status === 403
                        ? 'Your session or playlist access changed. Keep this page open and check your access before saving.' : null);
                }
                // A partial or uncertain request is never safe to replay automatically.
                if (result.target_status !== 'not_written') result.retryable = false;
                return result;
            } finally {
                inFlight = false;
            }
        },
        readCurrent: async function (id) {
            var result = await request('/api/workshop/' + encodeURIComponent(id || playlistId) + '/current-state',
                {method: 'GET', cache: 'no-store'}, false);
            if (result.success && (!Array.isArray(result.current_track_uris) || result.representable !== true)) {
                return {success: false, requires_refresh: true, retryable: false,
                    error_code: 'unsupported_playlist_items',
                    message: 'The current playlist could not be represented safely. Your draft is still here.'};
            }
            return result;
        },
    };
})(window);
