/* A small, read-only renderer for the bounded JobExecution result DTO. */
(function () {
    'use strict';

    const OPERATIONS = {
        raid: 'Collect for review', shuffle: 'Reorder existing tracks',
        raid_and_shuffle: 'Collect then reorder', raid_and_drip: 'Collect then add to playlist',
        drip: 'Add collected tracks', rotate: 'Rotate with archive',
    };
    const STEPS = {raid: 'Collect', shuffle: 'Reorder', drip: 'Add to playlist', rotate: 'Rotate'};
    const LEGACY_STATUS = {
        success: 'Completed', failed: 'Run failed', running: 'In progress', skipped: 'Skipped',
        failed_rolled_back: 'Run failed; rollback reported', partial: 'Partly completed',
        unknown: 'Needs review — result could not be verified',
    };
    const STEP_STATUS = {
        complete: 'Completed', partial: 'Partly completed', unknown: 'Could not be verified',
        failed: 'Failed', restored: 'Restoration verified', not_started: 'Did not start',
        pending: 'Not started yet',
    };
    const FACT_STATUS = {
        complete: 'Verified', failed: 'Failed before a confirmed change',
        unknown: 'Changes could not be verified', restored: 'Restoration verified',
        not_required: 'No change needed',
    };
    const ROLES = {target: 'Target playlist', collection: 'Linked collection', archive: 'Archive'};
    const BOOKKEEPING = {
        pending_stage: 'Review inbox staging', pending_promotion: 'Review inbox promotion',
        pending_restore: 'Review inbox restoration', execution_record: 'Run history',
    };
    const BOOKKEEPING_STATUS = {
        complete: 'Verified', failed: 'Failed; no change confirmed',
        unknown: 'Change could not be verified', restored: 'Restoration verified',
        not_required: 'No change needed',
    };
    const RESTORE_STATUS = {
        complete: 'Restoration verified', failed: 'Restoration failed',
        unknown: 'Restoration could not be verified', restored: 'Restoration verified',
        not_required: 'No restoration needed',
    };
    const RECORD_STATUS = {
        complete: 'Saved', failed: 'Save failed', unknown: 'Save could not be verified',
        restored: 'Restoration verified', not_required: 'No save needed',
    };
    const COUNTS = {
        tracks_collected: ['collected for review', 'No new tracks collected'],
        tracks_moved_to_target: ['added to target', 'No tracks added to target'],
        tracks_removed_from_collection: ['removed from collection', 'No tracks removed from collection'],
        tracks_reordered: ['positions changed', 'Order unchanged'],
        tracks_archived: ['moved to archive', 'No tracks moved to archive'],
        tracks_returned: ['returned to target', 'No tracks returned to target'],
        archive_overlaps_removed: ['overlaps removed from archive', 'No archive overlaps removed'],
        tracks_removed_from_target: ['removed from target', 'No tracks removed from target'],
        tracks_removed_from_archive: ['removed from archive', 'No tracks removed from archive'],
    };

    function node(tag, className, value) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (value !== undefined) element.textContent = String(value);
        return element;
    }

    function appendText(parent, tag, className, value) {
        const child = node(tag, className, value);
        parent.appendChild(child);
        return child;
    }

    function available(execution) {
        const detail = execution && execution.result_detail;
        return detail && detail.detail_status === 'available' && detail.version === 1 &&
            Number.isSafeInteger(execution.id) && execution.id > 0 &&
            detail.execution_id === execution.id &&
            Object.prototype.hasOwnProperty.call(OPERATIONS, detail.operation) &&
            Array.isArray(detail.steps) && detail.steps.length > 0 && detail.steps.length <= 4;
    }

    function dateText(value) {
        if (typeof value !== 'string' || !value) return 'Time unavailable';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return 'Time unavailable';
        return parsed.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
    }

    function legacyStatus(status) {
        return Object.prototype.hasOwnProperty.call(LEGACY_STATUS, status)
            ? LEGACY_STATUS[status] : 'Run status unavailable';
    }

    function overall(detail) {
        if (detail.run_state === 'running') return 'In progress';
        if (detail.outcome === 'complete') return 'Completed';
        if (detail.outcome === 'partial') return 'Partly completed';
        return 'Needs review — result could not be verified';
    }

    function role(fact) {
        if (fact.phase === 'archive_purge') return 'Archive cleanup';
        return Object.prototype.hasOwnProperty.call(ROLES, fact.kind)
            ? ROLES[fact.kind] : 'Playlist resource';
    }

    function addCounts(container, counts) {
        if (!counts || typeof counts !== 'object') return;
        const list = node('ul', 'sh-result-counts');
        Object.keys(COUNTS).forEach(function (key) {
            const value = counts[key];
            if (!Number.isSafeInteger(value) || value < 0) return;
            const label = value === 0 ? COUNTS[key][1] : value + ' ' + COUNTS[key][0];
            appendText(list, 'li', 'sh-result-count', label);
        });
        if (list.childNodes.length) container.appendChild(list);
    }

    function addFacts(container, step) {
        const facts = Array.isArray(step.resources) ? step.resources.slice(0, 12) : [];
        if (!facts.length) return;
        const list = node('ul', 'sh-result-resources');
        facts.forEach(function (fact) {
            if (!fact || typeof fact !== 'object') return;
            const label = role(fact);
            const state = Object.prototype.hasOwnProperty.call(FACT_STATUS, fact.status)
                ? FACT_STATUS[fact.status] : 'Status unavailable';
            appendText(list, 'li', 'sh-result-resource', label + ': ' + state);
        });
        if (list.childNodes.length) container.appendChild(list);
    }

    function addBookkeeping(container, step) {
        const facts = Array.isArray(step.bookkeeping) ? step.bookkeeping.slice(0, 12) : [];
        if (!facts.length) return;
        const list = node('ul', 'sh-result-bookkeeping');
        facts.forEach(function (fact) {
            if (!fact || !Object.prototype.hasOwnProperty.call(BOOKKEEPING, fact.kind)) return;
            const statuses = fact.kind === 'pending_restore' ? RESTORE_STATUS
                : fact.kind === 'execution_record' ? RECORD_STATUS : BOOKKEEPING_STATUS;
            const state = Object.prototype.hasOwnProperty.call(statuses, fact.status)
                ? statuses[fact.status] : 'Status unavailable';
            appendText(list, 'li', 'sh-result-bookkeeping-fact', BOOKKEEPING[fact.kind] + ': ' + state);
        });
        if (list.childNodes.length) container.appendChild(list);
    }

    function renderExecution(container, execution, options) {
        if (!container || typeof container.replaceChildren !== 'function') return;
        const density = options && options.density === 'compact' ? 'compact' : 'full';
        const record = execution && typeof execution === 'object' ? execution : {};
        const detail = available(record) ? record.result_detail : null;
        const root = node('div', 'sh-result sh-result--' + density);
        const title = detail ? OPERATIONS[detail.operation] + ' — ' + overall(detail) : legacyStatus(record.status);
        appendText(root, 'p', 'sh-result-heading', title);
        appendText(root, 'time', 'sh-result-time', dateText(record.started_at));
        if (!detail) {
            appendText(root, 'p', 'sh-result-unavailable', 'Step details unavailable');
            container.replaceChildren(root);
            return;
        }
        const ordered = node('ol', 'sh-result-steps');
        detail.steps.slice(0, 4).forEach(function (step) {
            if (!step || !Object.prototype.hasOwnProperty.call(STEPS, step.step)) return;
            const item = node('li', 'sh-result-step');
            const status = Object.prototype.hasOwnProperty.call(STEP_STATUS, step.status)
                ? STEP_STATUS[step.status] : 'Status unavailable';
            appendText(item, 'p', 'sh-result-step-heading', STEPS[step.step] + ' — ' + status);
            if (step.status !== 'not_started' && step.status !== 'pending' && step.status !== 'failed') {
                addCounts(item, step.counts);
            }
            addFacts(item, step);
            addBookkeeping(item, step);
            ordered.appendChild(item);
        });
        root.appendChild(ordered);
        if (detail.execution_record === 'unknown') {
            appendText(root, 'p', 'sh-result-record-warning', 'Run history could not be verified.');
        }
        container.replaceChildren(root);
    }

    window.RoutineResults = Object.freeze({renderExecution: renderExecution});
})();
