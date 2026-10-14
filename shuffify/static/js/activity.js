/* Filters the records already loaded for Activity; never fetches wider history. */
(function () {
    'use strict';

    const runFilter = document.getElementById('activity-run-filter');
    const operationFilter = document.getElementById('activity-operation-filter');
    const eventFilter = document.getElementById('activity-event-filter');
    if (!runFilter || !operationFilter || !eventFilter || !window.RoutineResults) return;

    const OPERATIONS = ['raid', 'shuffle', 'raid_and_shuffle', 'raid_and_drip', 'drip', 'rotate'];

    const runs = Array.from(document.querySelectorAll('.sh-activity-run'));
    const events = Array.from(document.querySelectorAll('.sh-activity-event'));
    const announcer = document.getElementById('activity-filter-status');

    function exactDetail(record) {
        const detail = record && record.result_detail;
        return detail && detail.detail_status === 'available' && detail.version === 1 &&
            Number.isSafeInteger(record.id) && detail.execution_id === record.id;
    }

    function runCategory(record) {
        if (!exactDetail(record)) return 'unavailable';
        if (record.result_detail.run_state === 'running') return 'running';
        return ['complete', 'partial', 'unknown'].includes(record.result_detail.outcome)
            ? record.result_detail.outcome : 'unavailable';
    }

    function runOperation(record) {
        const operation = exactDetail(record) ? record.result_detail.operation : record.job_type;
        return OPERATIONS.includes(operation) ? operation : 'unavailable';
    }

    function displayType(type) {
        return type.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
    }

    const eventTypes = [...new Set(events.map(row => row.dataset.activityType).filter(Boolean))].sort();
    eventTypes.forEach(function (type) {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = displayType(type);
        eventFilter.appendChild(option);
    });

    runs.forEach(function (row) {
        let record;
        try {
            record = JSON.parse(row.dataset.execution || '{}');
        } catch (_) {
            record = {};
        }
        row.dataset.resultCategory = runCategory(record);
        row.dataset.operation = runOperation(record);
        const slot = row.querySelector('[data-result-slot]');
        window.RoutineResults.renderExecution(slot, record, {density: 'full'});
    });

    function applyFilter(rows, filter, countId, emptyId, predicate, label, announce) {
        let shown = 0;
        rows.forEach(function (row) {
            const visible = predicate(row, filter.value);
            if (!visible && document.activeElement && row.contains && row.contains(document.activeElement)) {
                filter.focus();
            }
            row.hidden = !visible;
            if (visible) shown += 1;
        });
        const count = document.getElementById(countId);
        if (count) count.textContent = shown + ' of ' + rows.length + ' loaded ' + label + ' shown';
        const empty = document.getElementById(emptyId);
        if (empty) empty.hidden = shown !== 0 || rows.length === 0;
        if (announce && announcer) announcer.textContent = shown + ' loaded ' + label + ' shown.';
    }

    function filterRuns(announce) {
        applyFilter(runs, runFilter, 'activity-run-count', 'activity-run-no-match',
            (row, value) => (value === 'all' || row.dataset.resultCategory === value) &&
                (operationFilter.value === 'all' || row.dataset.operation === operationFilter.value),
            'runs', announce);
    }

    function filterEvents(announce) {
        applyFilter(events, eventFilter, 'activity-event-count', 'activity-event-no-match',
            (row, value) => value === 'all' || (value === 'manual'
                ? row.dataset.triggeredBy === 'manual' : row.dataset.activityType === value),
            'events', announce);
    }

    runFilter.addEventListener('change', function () { filterRuns(true); });
    operationFilter.addEventListener('change', function () { filterRuns(true); });
    eventFilter.addEventListener('change', function () { filterEvents(true); });
    filterRuns(false);
    filterEvents(false);
})();
