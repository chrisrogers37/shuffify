/* Guided routine task. The routine context, review and activation flow is the only definition write path. */
(function () {
    'use strict';
    const PAGE = typeof ROUTINE_PAGE === 'object' ? ROUTINE_PAGE : null;
    if (!PAGE) return;
    const KEY = 'shuffify:routine-continuation:v1';
    const OPERATIONS = {
        raid: 'Collect', raid_and_shuffle: 'Collect and shuffle', raid_and_drip: 'Collect and add',
        shuffle: 'Shuffle', rotate: 'Rotate', drip: 'Add collected tracks',
    };
    const GOALS = {
        collect: ['raid', 'raid_and_shuffle', 'raid_and_drip'], order: ['shuffle', 'rotate'],
        fresh: ['rotate', 'drip'], all: Object.keys(OPERATIONS),
    };
    const COLLECTS = ['raid', 'raid_and_shuffle', 'raid_and_drip'];
    const SHUFFLES = ['shuffle', 'raid_and_shuffle'];
    const DRIPS = ['drip', 'raid_and_drip'];
    const byId = id => document.getElementById(id);
    const text = (id, value) => { byId(id).textContent = String(value == null ? '' : value); };
    const element = (tag, words, className) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (words != null) node.textContent = String(words);
        return node;
    };
    const replace = (id, children) => byId(id).replaceChildren(...children);
    const integer = value => /^\d+$/.test(String(value)) ? Number(value) : null;
    const enteredNumber = value => value === '' ? null : integer(value) === null ? value : integer(value);
    const enteredFloat = value => value === '' ? null : Number.isFinite(Number(value)) ? Number(value) : value;
    const displayNumber = (value, fallback) => value === undefined ? fallback : value === null ? '' : value;
    const clone = value => JSON.parse(JSON.stringify(value));
    const state = {
        context: null, step: PAGE.requestedScheduleId ? 'connections' : 'goal', goal: 'all',
        operation: null, scheduleId: PAGE.requestedScheduleId, revision: null, createAnother: false,
        choiceMade: Boolean(PAGE.requestedScheduleId), row: null, algorithm: null, params: {},
        timing: {kind: 'interval', interval: 'daily', timezone: 'UTC'}, timingDirty: false,
        review: null, reviewedRequest: null, activationRequest: null, accepted: null,
        phase: 'loading', dirty: false, error: '', handoff: null, continuation: null,
        origin: PAGE.origin,
    };

    function announce(message) { text('routine-status', message); }
    function setBusy(phase, message) { state.phase = phase; announce(message); render(); }
    function invalidate() { state.review = null; state.reviewedRequest = null; state.dirty = true; }
    function clearError() {
        document.querySelectorAll('.sh-field-error').forEach(node => {
            node.textContent = ''; node.hidden = true;
        });
        document.querySelectorAll('[aria-invalid]').forEach(node => node.removeAttribute('aria-invalid'));
    }
    function fieldError(path, message) {
        const name = String(path || '').replace(/^definition\./, '');
        const map = {
            job_type: 'routine-operation', algorithm_name: 'routine-algorithm',
            timing: 'routine-timing-kind', 'timing.kind': 'routine-timing-kind',
            'timing.interval': 'routine-interval', 'timing.time': 'routine-clock',
            'timing.weekday': 'routine-weekday', 'timing.expression': 'routine-expression',
            'timing.timezone': 'routine-timezone',
            'algorithm_params.drip_count': 'routine-drip-count',
            'algorithm_params.rotation_mode': 'routine-rotation-mode',
            'algorithm_params.rotation_count': 'routine-rotation-count',
            'algorithm_params.target_size': 'routine-target-size',
            'algorithm_params.protect_count': 'routine-protect-count',
        };
        const id = map[name] || (name.startsWith('algorithm_params.') ?
            'routine-param-' + name.slice('algorithm_params.'.length) : 'routine-operation');
        const control = byId(id), error = byId(name === 'timing' ? 'routine-timing-error' : id + '-error');
        if (control) control.setAttribute('aria-invalid', 'true');
        if (error) { error.textContent = message; error.hidden = false; }
        return control;
    }
    function showErrors(data) {
        clearError();
        const errors = Array.isArray(data.field_errors) ? data.field_errors : [];
        const path = String(errors[0] && errors[0].path || '');
        state.step = state.scheduleId ? 'connections' : path.includes('timing') ? 'timing' : 'connections';
        render(true);
        let first = null;
        errors.forEach(item => { first = first || fieldError(item.path, item.message); });
        announce(data.message || 'Review the highlighted fields.');
        if (first) first.focus();
    }
    function readable(result) { return result && result.answered && result.data && typeof result.data === 'object'; }
    function acceptedShape(data, requestBody) {
        const revision = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
        if (!data || data.success !== true || data.accepted !== true ||
                !Number.isInteger(data.schedule_id) || data.schedule_id < 1 ||
                (requestBody.schedule_id && data.schedule_id !== requestBody.schedule_id) ||
                typeof data.replayed !== 'boolean' || typeof data.accepted_at !== 'string' ||
                !data.accepted_at || !revision(data.dependency_revision)) return false;
        if (data.state === 'Deleted') return data.schedule === null &&
            data.schedule_revision === undefined && data.registration === undefined;
        const registration = data.registration;
        return revision(data.schedule_revision) && data.schedule && typeof data.schedule === 'object' &&
            data.schedule.id === data.schedule_id && data.schedule.target_playlist_id === PAGE.targetId &&
            typeof data.schedule.is_enabled === 'boolean' &&
            typeof data.state === 'string' && data.state.length > 0 && registration &&
            ['registered', 'paused', 'needs_attention', 'unavailable'].includes(registration.status) &&
            (registration.next_run_at === null || typeof registration.next_run_at === 'string');
    }
    function send(url, options) {
        return window.AppRequest.send(url, Object.assign({timeoutMs: options && options.method ? 120000 : 30000,
            cache: 'no-store'}, options || {}));
    }
    function storage() {
        try { return window.sessionStorage || null; } catch (_) { return null; }
    }
    function discardContinuation() {
        try { const store = storage(); if (store) store.removeItem(KEY); } catch (_) { /* Storage is optional. */ }
    }
    function validContinuation(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
        const keys = ['v', 'ownerId', 'targetId', 'operation', 'options', 'scheduleId', 'scheduleRevision',
            'createAnother', 'origin', 'expiresAt'];
        if (Object.keys(value).sort().join('|') !== keys.sort().join('|')) return false;
        if (value.v !== 1 || value.ownerId !== PAGE.ownerId || value.targetId !== PAGE.targetId ||
                !Object.hasOwn(OPERATIONS, value.operation) || typeof value.createAnother !== 'boolean' ||
                !['playlist', 'routines'].includes(value.origin) || !Number.isFinite(value.expiresAt) ||
                value.expiresAt <= Date.now() || value.expiresAt > Date.now() + 1800000) return false;
        if (value.scheduleId !== null && (!Number.isInteger(value.scheduleId) || value.scheduleId < 1 ||
                typeof value.scheduleRevision !== 'string' || value.scheduleRevision.length !== 64)) return false;
        if (value.scheduleId === null && value.scheduleRevision !== null) return false;
        const options = value.options;
        if (!options || typeof options !== 'object' || Array.isArray(options) ||
                Object.keys(options).sort().join('|') !== ['algorithm', 'params', 'timing', 'timingDirty'].sort().join('|') ||
                !(options.algorithm === null || typeof options.algorithm === 'string') ||
                !options.params || typeof options.params !== 'object' || Array.isArray(options.params) ||
                !options.timing || typeof options.timing !== 'object' || Array.isArray(options.timing) ||
                typeof options.timingDirty !== 'boolean') return false;
        if (Object.keys(options.params).length > 20 || !Object.keys(options.params).every(name =>
            /^[a-z_]{1,40}$/.test(name) && (options.params[name] === null ||
                ['string', 'number', 'boolean'].includes(typeof options.params[name]))) ||
                !Object.keys(options.timing).every(name =>
                    ['kind', 'interval', 'time', 'weekday', 'expression', 'timezone'].includes(name)) ||
                typeof options.timing.kind !== 'string' ||
                !['interval', 'daily', 'weekly', 'advanced'].includes(options.timing.kind)) return false;
        return true;
    }
    function readContinuation() {
        try {
            const store = storage(); if (!store) return null;
            const raw = store.getItem(KEY);
            if (!raw) return null;
            if (raw.length > 4096) { discardContinuation(); return null; }
            const value = JSON.parse(raw);
            if (!validContinuation(value)) { discardContinuation(); return null; }
            return value;
        } catch (_) { discardContinuation(); return null; }
    }
    function persistContinuation() {
        captureFields();
        const store = storage();
        if (!store) return false;
        const value = {
            v: 1, ownerId: PAGE.ownerId, targetId: PAGE.targetId, operation: state.operation,
            options: {algorithm: state.algorithm, params: state.params, timing: state.timing,
                timingDirty: state.timingDirty},
            scheduleId: state.scheduleId, scheduleRevision: state.scheduleId ? state.revision : null,
            createAnother: state.createAnother, origin: state.origin, expiresAt: Date.now() + 1800000,
        };
        try { store.setItem(KEY, JSON.stringify(value)); return true; } catch (_) { return false; }
    }
    function restoreContinuation(value) {
        state.operation = value.operation;
        state.scheduleId = value.scheduleId;
        state.revision = value.scheduleRevision;
        state.createAnother = value.createAnother;
        state.choiceMade = Boolean(value.scheduleId || value.createAnother);
        state.algorithm = value.options.algorithm;
        state.params = clone(value.options.params);
        state.timing = clone(value.options.timing);
        state.timingDirty = value.options.timingDirty;
        state.origin = value.origin;
        state.dirty = true;
        state.step = 'connections';
        state.continuation = value;
    }

    function exactRow() { return (state.context && state.context.routines || []).find(row =>
        row.schedule_id === state.scheduleId) || null; }
    function applyRow(row) {
        state.scheduleId = row.schedule_id;
        state.revision = row.schedule_revision;
        state.row = row;
        state.operation = row.definition.job_type;
        state.algorithm = row.definition.algorithm_name;
        state.params = clone(row.definition.algorithm_params || {});
        const info = row.timing || {};
        state.timing = {kind: info.kind || 'advanced', interval: info.interval || 'daily',
            time: info.time || '09:00', weekday: info.weekday || 'mon',
            expression: info.expression || info.schedule_value || '',
            timezone: info.timezone || info.effective_timezone || 'UTC'};
        state.timingDirty = false;
        state.createAnother = false;
        state.choiceMade = true;
    }
    async function refreshContext(initial) {
        // The safe return destination is session-bound even when the fresh context is unavailable.
        if (initial) {
            const continuation = readContinuation();
            if (continuation && PAGE.requestedScheduleId === null) restoreContinuation(continuation);
            else if (continuation) discardContinuation();
        }
        const result = await send(PAGE.urls.context);
        if (!result.ok || !result.data || !Array.isArray(result.data.routines)) {
            state.phase = 'error';
            state.error = readable(result) ? result.data.message || 'Could not load this playlist.' :
                'Could not load this playlist. Try again later.';
            render(); return false;
        }
        state.context = result.data;
        if (initial && state.continuation) discardContinuation();
        if (initial) {
            if (!state.continuation && state.scheduleId !== null) {
                const row = exactRow();
                if (!row) {
                    state.phase = 'error'; state.error = 'This exact routine is unavailable. It was not changed.';
                    render(); return false;
                }
                applyRow(row);
            }
        }
        if (state.continuation && state.scheduleId !== null) {
            const row = exactRow();
            if (!row) {
                state.phase = 'error'; state.error = 'The exact routine was deleted while you were in setup. Your saved setup remains.';
                render(); return false;
            }
            if (row.schedule_revision !== state.continuation.scheduleRevision) {
                state.phase = 'conflict';
                state.error = 'The exact routine changed while you were in setup. Your independently saved setup remains. Use its current definition or return to routines.';
                announce('This routine changed while you were in setup. Choose whether to adopt its current definition.');
                render(); return false;
            }
            state.row = row;
        }
        state.phase = 'idle';
        if (state.continuation) announce('Playlist setup changes may have been saved. Review the routine again before activating.');
        render(); return true;
    }

    function candidates() {
        return (state.context && state.context.routines || []).filter(row =>
            row.definition.job_type === state.operation);
    }
    function selectOperation(value) {
        if (!Object.hasOwn(OPERATIONS, value) || value === state.operation) return;
        const old = state.operation;
        state.operation = value;
        // Activation inherits omitted edit fields. A type change must supply a clean option set.
        if (old !== value) {
            state.params = value === 'rotate' ? {rotation_mode: 'swap', rotation_count: 5, target_size: 50,
                protect_count: 0} : {};
            state.algorithm = SHUFFLES.includes(value) ? (state.context.algorithms[0] || {}).class_name || null : null;
        }
        if (!state.scheduleId) { state.createAnother = false; state.choiceMade = candidates().length === 0; }
        invalidate(); clearError(); render();
    }
    function selectGoal(goal) {
        if (!GOALS[goal]) return;
        state.goal = goal;
        state.step = 'connections';
        if (!state.operation || !GOALS[goal].includes(state.operation)) selectOperation(GOALS[goal][0]);
        render(true);
    }
    function chooseRow(id) {
        const row = (state.context.routines || []).find(item => item.schedule_id === id);
        if (!row) { announce('This exact routine is unavailable. Refresh before editing.'); return; }
        applyRow(row); invalidate(); clearError(); render();
    }
    function createAnother() {
        state.scheduleId = null; state.revision = null; state.row = null;
        state.createAnother = true; state.choiceMade = true;
        invalidate(); render();
    }
    function algorithmMetadata() { return (state.context && state.context.algorithms || []).find(item =>
        item.class_name === state.algorithm) || null; }
    function captureFields() {
        if (!state.context) return;
        if (SHUFFLES.includes(state.operation)) {
            state.algorithm = byId('routine-algorithm').value || null;
            const metadata = algorithmMetadata();
            const next = {};
            Object.entries(metadata && metadata.parameters || {}).forEach(([name, rule]) => {
                const control = byId('routine-param-' + name);
                if (!control) return;
                const value = control.value;
                if (value !== '') next[name] = rule.type === 'integer' ? enteredNumber(value) :
                    rule.type === 'float' ? enteredFloat(value) : value;
            });
            state.params = next;
        } else if (state.operation === 'rotate') {
            state.algorithm = null;
            const params = {target_size: enteredNumber(byId('routine-target-size').value)};
            if (byId('routine-rotation-mode').value) params.rotation_mode = byId('routine-rotation-mode').value;
            if (byId('routine-rotation-count').value !== '')
                params.rotation_count = enteredNumber(byId('routine-rotation-count').value);
            if (byId('routine-protect-count').value !== '')
                params.protect_count = enteredNumber(byId('routine-protect-count').value);
            state.params = params;
        } else if (DRIPS.includes(state.operation)) {
            state.algorithm = null;
            const value = byId('routine-drip-count').value;
            state.params = value === '' ? {} : {drip_count: enteredNumber(value)};
        } else { state.algorithm = null; state.params = {}; }
        const kind = byId('routine-timing-kind').value;
        state.timing = {kind, timezone: byId('routine-timezone').value.trim()};
        if (kind === 'interval') state.timing.interval = byId('routine-interval').value;
        if (kind === 'daily' || kind === 'weekly') state.timing.time = byId('routine-clock').value;
        if (kind === 'weekly') state.timing.weekday = byId('routine-weekday').value;
        if (kind === 'advanced') state.timing.expression = byId('routine-expression').value.trim();
    }
    function reviewedInput() {
        captureFields();
        const definition = {job_type: state.operation};
        if (SHUFFLES.includes(state.operation)) {
            definition.algorithm_name = state.algorithm;
            definition.algorithm_params = clone(state.params);
        } else {
            // Explicit null and a clean type-specific object prevent inherited options on edit.
            definition.algorithm_name = null;
            definition.algorithm_params = clone(state.params);
        }
        if (!state.scheduleId || state.timingDirty) definition.timing = clone(state.timing);
        const body = {request_key: requestKey(), definition, create_another: state.createAnother};
        if (state.scheduleId) { body.schedule_id = state.scheduleId; body.expected_revision = state.revision; }
        return body;
    }
    function requestKey() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        return 'routine-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    }
    async function revisitCandidates(message, confirmedContext) {
        state.review = null; state.reviewedRequest = null; state.activationRequest = null;
        state.step = 'connections'; state.phase = 'idle';
        if (!state.scheduleId) { state.createAnother = false; state.choiceMade = false; }
        if (confirmedContext) state.context = confirmedContext;
        const ready = confirmedContext || await refreshContext(false);
        if (!ready) return;
        announce(message || 'Matching routines changed. Choose an exact routine or Create another, then review again.');
        render(true);
        const list = byId('routine-candidate-list');
        if (!byId('routine-candidates').hidden) list.focus();
    }
    async function reviewRoutine() {
        if (state.phase !== 'idle' || !state.operation) return;
        if (!state.scheduleId && candidates().length && !state.createAnother) {
            text('routine-choice-error', 'Choose an exact routine or Create another routine.');
            byId('routine-choice-error').hidden = false;
            byId('routine-candidate-list').focus(); return;
        }
        const body = reviewedInput();
        setBusy('reviewing', 'Reviewing the current playlist settings…');
        const result = await send(PAGE.urls.review, {method: 'POST', json: body});
        if (!result.ok) {
            state.phase = 'idle';
            if (readable(result) && result.data.error_code === 'routine_not_found') {
                state.phase = 'error'; state.error = result.data.message;
            } else if (readable(result)) {
                if (['prerequisites_changed', 'routine_changed'].includes(result.data.error_code)) {
                    await refreshContext(false);
                }
                if (result.data.error_code === 'routine_changed' && state.scheduleId &&
                        (!exactRow() || exactRow().schedule_revision !== state.revision)) {
                    state.phase = 'conflict';
                    state.error = 'The exact routine changed. Use its current definition or return to routines.';
                    announce(result.data.message); render();
                } else showErrors(result.data);
            } else announce('Review could not be completed. Try again.');
            if (!readable(result) || state.phase === 'error') render();
            return;
        }
        if (!result.data.definition || !result.data.intent || !result.data.dependency_revision) {
            state.phase = 'idle';
            announce('The review response was incomplete. Review again.'); render(); return;
        }
        // Review validates a fresh setup snapshot. Confirm its revision with a new context read
        // before showing resource names or allowing activation; the initial page read may be old.
        announce('Confirming the reviewed playlist settings…');
        const confirmed = await send(PAGE.urls.context);
        state.phase = 'idle';
        if (!confirmed.ok || !confirmed.data || !Array.isArray(confirmed.data.routines) ||
                !confirmed.data.dependency_revision) {
            state.review = null; state.reviewedRequest = null;
            state.step = 'connections';
            announce('Could not confirm the reviewed settings. Review again.');
            render(true); return;
        }
        state.context = confirmed.data;
        if (confirmed.data.dependency_revision !== result.data.dependency_revision) {
            state.review = null; state.reviewedRequest = null;
            state.step = 'connections';
            announce('Playlist settings changed after Review. Check connections and review again.');
            render(true); return;
        }
        if (!body.schedule_id && !body.create_another &&
                Array.isArray(result.data.candidate_schedule_ids) && result.data.candidate_schedule_ids.length) {
            await revisitCandidates('Matching routines were found. Choose an exact routine or Create another, then review again.', confirmed.data);
            return;
        }
        state.review = result.data;
        state.reviewedRequest = body;
        state.step = 'review';
        state.dirty = true;
        announce('Review ready. Nothing has been saved yet.');
        render(true);
    }
    function activationBody() {
        const reviewed = state.reviewedRequest, answer = state.review;
        const body = {
            ...reviewed, definition: clone(answer.definition), intent: clone(answer.intent),
            dependency_revision: answer.dependency_revision,
        };
        return body;
    }
    async function dispatchActivation(body) {
        setBusy('activating', 'Saving the reviewed routine. Leaving cannot cancel this request…');
        const result = await send(PAGE.urls.activate, {method: 'POST', json: body});
        if (!readable(result) || result.status >= 500) {
            state.phase = 'unknown';
            announce('Activation may already be saved. Check activation using the same request.');
            render(); return;
        }
        if (!result.ok) {
            state.phase = 'idle';
            const code = result.data.error_code;
            if (code === 'request_key_conflict') {
                state.phase = 'hard_error';
                announce('This activation key belongs to other changes. Stop and inspect the exact routine.');
            } else if (code === 'existing_candidates') {
                await revisitCandidates('A matching routine is now available. Choose its exact ID or Create another, then review again.');
                return;
            } else if (['prerequisites_changed', 'routine_changed', 'invalid_intent', 'intent_expired',
                'routine_not_found'].includes(code)) {
                state.review = null; state.reviewedRequest = null; state.activationRequest = null;
                state.step = 'connections';
                await refreshContext(false);
                if (state.scheduleId && (!exactRow() || exactRow().schedule_revision !== state.revision)) {
                    state.phase = 'conflict';
                    state.error = 'The exact routine changed. Use its current definition or return to routines.';
                }
                announce(result.data.message || 'The routine changed. Review it again.');
            } else if (code === 'activation_failed') {
                state.phase = 'unknown';
                announce('Could not confirm activation. Check activation with the same request key.');
            } else {
                showErrors(result.data); return;
            }
            render(); return;
        }
        if (!acceptedShape(result.data, body)) {
            state.phase = 'unknown';
            announce('The activation reply was incomplete. It may already be saved. Check activation with the same request.');
            render(); return;
        }
        state.accepted = result.data;
        state.scheduleId = result.data.schedule_id;
        state.revision = result.data.schedule_revision;
        state.phase = 'accepted';
        state.dirty = false;
        discardContinuation();
        announce(result.data.state === 'Deleted' ?
            'The activation was accepted, but this exact routine has since been deleted.' :
            result.data.registration && result.data.registration.status === 'registered' ?
            'Routine saved and scheduled.' : 'Routine saved; scheduling needs attention or is paused.');
        render(true);
    }
    function activate() {
        if (state.phase !== 'idle' || !state.review || !state.reviewedRequest) return;
        if (state.review.candidate_schedule_ids && state.review.candidate_schedule_ids.length &&
                !state.reviewedRequest.schedule_id && !state.reviewedRequest.create_another) {
            announce('Choose an exact routine or Create another, then review again.'); return;
        }
        state.activationRequest = activationBody();
        dispatchActivation(state.activationRequest);
    }
    function checkActivation() {
        if (state.phase !== 'unknown' || !state.activationRequest) return;
        dispatchActivation(state.activationRequest);
    }
    async function setRoutineState(desired) {
        if (state.phase === 'activating' || state.phase === 'reviewing' || state.phase === 'state' ||
                state.phase === 'unknown') return;
        const row = state.accepted || state.row;
        if (!row || !row.schedule_id || !row.schedule_revision) return;
        setBusy('state', desired ? 'Checking prerequisites and scheduling…' : 'Pausing future runs…');
        const result = await send(PAGE.urls.state + '/' + row.schedule_id + '/state',
            {method: 'PUT', json: {desired_enabled: desired, expected_revision: row.schedule_revision}});
        state.phase = state.accepted ? 'accepted' : 'idle';
        if (result.ok) {
            if (state.accepted) state.accepted = {...state.accepted, ...result.data};
            else { state.row = {...state.row, ...result.data}; state.revision = result.data.schedule_revision; }
            state.review = null; state.reviewedRequest = null;
            if (!state.accepted && state.step === 'review') state.step = state.scheduleId ? 'connections' : 'timing';
            announce(desired ? 'Routine resumed. Check its current scheduling status below.' :
                'Routine paused for future runs. An action already running is not cancelled.');
        } else {
            announce(readable(result) ? result.data.message || 'Could not change this routine.' :
                'Could not confirm the state change. Refresh the exact routine before trying again.');
            if (readable(result) && result.data.error_code === 'routine_changed') await refreshContext(false);
        }
        render();
    }

    function renderOperations() {
        const select = byId('routine-operation'), offered = GOALS[state.goal] || GOALS.all;
        replace('routine-operation', offered.map(type => {
            const option = element('option', OPERATIONS[type]); option.setAttribute('value', type); return option;
        }));
        if (state.operation && !offered.includes(state.operation)) {
            const option = element('option', OPERATIONS[state.operation]); option.setAttribute('value', state.operation);
            select.appendChild(option);
        }
        select.value = state.operation || offered[0];
    }
    function renderCandidates() {
        const rows = candidates();
        byId('routine-candidates').hidden = !rows.length;
        replace('routine-candidate-list', rows.map(row => {
            const group = element('div', null, 'sh-routine-candidate');
            const label = element('p', '#' + row.schedule_id + ' · ' + (row.schedule.target_playlist_name ||
                state.context.target.name) + ' · ' + (row.timing && row.timing.text || 'Timing unavailable') +
                ' · ' + row.state);
            const choose = element('button', 'Edit this routine', 'sh-button-secondary');
            choose.type = 'button'; choose.dataset.actionClick = 'routine.choose';
            choose.dataset.scheduleId = String(row.schedule_id);
            choose.setAttribute('aria-label', 'Edit this routine #' + row.schedule_id);
            if (state.scheduleId === row.schedule_id) choose.setAttribute('aria-current', 'true');
            group.append(label, choose); return group;
        }));
        byId('routine-create-another').setAttribute('aria-pressed', String(state.createAnother));
    }
    function renderAlgorithms() {
        const select = byId('routine-algorithm'), algorithms = state.context.algorithms || [];
        replace('routine-algorithm', algorithms.map(item => {
            const option = element('option', item.name); option.setAttribute('value', item.class_name); return option;
        }));
        if (!state.algorithm && algorithms.length) state.algorithm = algorithms[0].class_name;
        select.value = state.algorithm || '';
        const metadata = algorithmMetadata();
        text('routine-algorithm-help', metadata && metadata.description || 'Choose an available algorithm.');
        replace('routine-algorithm-params', Object.entries(metadata && metadata.parameters || {}).map(([name, rule]) => {
            const group = element('div', null, 'sh-form-field');
            const id = 'routine-param-' + name;
            const label = element('label', name.replaceAll('_', ' '), 'sh-field-label'); label.htmlFor = id;
            let control;
            if (rule.type === 'string' && Array.isArray(rule.options)) {
                control = element('select', null, 'sh-input');
                const blank = element('option', 'Use algorithm default'); blank.setAttribute('value', '');
                control.appendChild(blank);
                rule.options.forEach(value => { const option = element('option', value); option.setAttribute('value', value);
                    control.appendChild(option); });
            } else {
                control = element('input', null, 'sh-input');
                control.type = 'number'; control.inputMode = rule.type === 'integer' ? 'numeric' : 'decimal';
                if (rule.min !== undefined) control.min = rule.min;
                if (rule.max !== undefined) control.max = rule.max;
                if (rule.type === 'float') control.step = 'any';
            }
            control.id = id; control.dataset.actionInput = 'routine.edit';
            control.dataset.actionChange = 'routine.edit';
            control.setAttribute('aria-describedby', id + '-error');
            const inherited = state.scheduleId && state.row && state.row.definition.job_type === state.operation;
            control.value = state.params[name] !== undefined ? state.params[name] :
                inherited ? '' : rule.default !== undefined ? rule.default : '';
            const help = element('p', rule.description || '', 'sh-field-help');
            const error = element('p', '', 'sh-field-error'); error.id = id + '-error'; error.hidden = true;
            group.append(label, control, help, error); return group;
        }));
    }
    function renderConnections() {
        renderOperations(); renderCandidates(); renderAlgorithms();
        const capability = (state.context.capabilities || {})[state.operation];
        text('routine-operation-help', capability && !capability.available ?
            'Setup needed before activation: ' + capability.reason :
            'All six operations are available. Setup is checked again during Review.');
        byId('routine-sources-section').hidden = !COLLECTS.includes(state.operation);
        byId('routine-collection-section').hidden = !COLLECTS.includes(state.operation) && !DRIPS.includes(state.operation);
        byId('routine-drip-options').hidden = !DRIPS.includes(state.operation);
        byId('routine-archive-section').hidden = state.operation !== 'rotate';
        byId('routine-algorithm-section').hidden = !SHUFFLES.includes(state.operation);
        const setup = state.context.prerequisites || {}, sources = setup.sources || [];
        replace('routine-sources', sources.map(source => element('li', source.name || source.search_query ||
            source.playlist_id || 'Source #' + source.id)));
        byId('routine-sources-empty').hidden = sources.length > 0;
        const legacy = state.row && state.row.definition.source_playlist_ids || [];
        const saved = new Set(sources.map(source => source.playlist_id).filter(Boolean));
        const only = legacy.filter(id => !saved.has(id));
        byId('routine-legacy-sources').hidden = !only.length;
        replace('routine-legacy-source-list', only.map(id => element('li', id)));
        const collection = setup.collection, archive = setup.archive;
        text('routine-collection-summary', collection ? (collection.name || collection.playlist_id) +
            ' · Drip ' + (collection.drip_enabled ? 'enabled' : 'off') + ' · ' + collection.drip_count +
            ' tracks per run' : 'No collection linked. Save one in playlist setup.');
        text('routine-archive-summary', archive ? (archive.name || archive.playlist_id) :
            'No archive paired. Save one in playlist setup.');
        byId('routine-drip-count').value = state.params.drip_count === undefined ? '' : state.params.drip_count;
        const inheritedRotation = state.scheduleId && state.row && state.row.definition.job_type === 'rotate';
        byId('routine-rotation-mode').value = state.params.rotation_mode || (inheritedRotation ? '' : 'swap');
        byId('routine-rotation-count').value = displayNumber(state.params.rotation_count, inheritedRotation ? '' : 5);
        byId('routine-target-size').value = displayNumber(state.params.target_size, 50);
        byId('routine-protect-count').value = displayNumber(state.params.protect_count, inheritedRotation ? '' : 0);
    }
    function renderTiming() {
        const info = state.row && state.row.timing;
        byId('routine-legacy-timing').hidden = !info || state.timingDirty === true;
        if (info && !state.timingDirty) text('routine-legacy-timing',
            'Saved timing: ' + info.text + (info.legacy ? ' · legacy scheduler timezone' : '') +
            (info.explanation ? ' · ' + info.explanation : '') + '. Leave timing untouched to preserve it.');
        byId('routine-timing-kind').value = state.timing.kind;
        byId('routine-interval').value = state.timing.interval || 'daily';
        byId('routine-clock').value = state.timing.time ?? '09:00';
        byId('routine-weekday').value = state.timing.weekday || 'mon';
        byId('routine-expression').value = state.timing.expression || '';
        byId('routine-timezone').value = state.timing.timezone ?? 'UTC';
        byId('routine-interval-fields').hidden = state.timing.kind !== 'interval';
        byId('routine-clock-fields').hidden = !['daily', 'weekly'].includes(state.timing.kind);
        byId('routine-weekday-fields').hidden = state.timing.kind !== 'weekly';
        byId('routine-advanced-fields').hidden = state.timing.kind !== 'advanced';
    }
    function fact(label, value) {
        const group = element('div', null, 'sh-routine-fact');
        group.append(element('dt', label), element('dd', value)); return group;
    }
    const INTERVAL_TEXT = {
        every_6h: 'Every 6 hours', every_12h: 'Every 12 hours', daily: 'Every day',
        every_3d: 'Every 3 days', weekly: 'Every week',
    };
    const WEEKDAY_TEXT = {
        mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday',
        fri: 'Friday', sat: 'Saturday', sun: 'Sunday',
    };
    function friendlyTiming(definition) {
        if (!definition) return 'Timing unavailable';
        if (definition.schedule_type === 'interval')
            return (INTERVAL_TEXT[definition.schedule_value] || 'Custom elapsed interval') + ' · elapsed interval';
        const raw = String(definition.schedule_value || '');
        const parts = raw.split(' '), zone = definition.timezone || 'scheduler timezone';
        if (definition.schedule_type === 'cron' && parts.length === 5 && /^\d+$/.test(parts[0]) &&
                /^\d+$/.test(parts[1]) && Number(parts[0]) < 60 && Number(parts[1]) < 24 &&
                parts[2] === '*' && parts[3] === '*') {
            const at = String(parts[1]).padStart(2, '0') + ':' + String(parts[0]).padStart(2, '0');
            if (parts[4] === '*') return 'Every day at ' + at + ' ' + zone;
            if (WEEKDAY_TEXT[parts[4]]) return 'Every ' + WEEKDAY_TEXT[parts[4]] + ' at ' + at + ' ' + zone;
        }
        return 'Advanced timing · cron ' + raw + ' · ' + zone;
    }
    function draftTiming() {
        const timing = state.timing;
        if (state.row && !state.timingDirty) return state.row.timing.text;
        if (timing.kind === 'interval') return (INTERVAL_TEXT[timing.interval] || 'Elapsed interval') +
            ' · elapsed interval';
        if (timing.kind === 'daily') return 'Every day at ' + (timing.time || 'time needed') +
            ' ' + timing.timezone;
        if (timing.kind === 'weekly') return 'Every ' + (WEEKDAY_TEXT[timing.weekday] || 'weekday') +
            ' at ' + (timing.time || 'time needed') + ' ' + timing.timezone;
        return 'Advanced timing · ' + (timing.expression || 'expression needed') + ' · ' + timing.timezone;
    }
    function optionFacts(definition, params) {
        if (SHUFFLES.includes(definition.job_type)) {
            const metadata = (state.context.algorithms || []).find(item =>
                item.class_name === definition.algorithm_name);
            return Object.entries(params).map(([name, value]) => [
                name === 'keep_first' ? 'Keep first tracks' :
                    name.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase()),
                String(value) + (metadata && metadata.parameters && metadata.parameters[name] &&
                    metadata.parameters[name].description ? ' · ' + metadata.parameters[name].description : ''),
            ]);
        }
        if (definition.job_type === 'rotate') return [
            ['Rotation mode', params.rotation_mode === 'swap' ? 'Exchange tracks' : 'Saved default'],
            ['Tracks exchanged', params.rotation_count === undefined ? 'Saved default' : String(params.rotation_count)],
            ['Target size cap', params.target_size === undefined ? 'Not set' : String(params.target_size)],
            ['Protected first tracks', params.protect_count === undefined ? 'Saved default' : String(params.protect_count)],
        ];
        return [];
    }
    function reviewFacts() {
        if (!state.review) return [];
        const definition = state.review.definition, setup = state.context.prerequisites || {};
        const params = definition.algorithm_params || {};
        const sources = (setup.sources || []).map(item => item.name || item.search_query || item.playlist_id);
        const extras = state.review.resource_scope && state.review.resource_scope.routine_only_source_playlist_ids || [];
        const facts = [
            ['Playlist', state.context.target.name], ['Operation', OPERATIONS[definition.job_type]],
            ['Exact routine', state.scheduleId ? '#' + state.scheduleId : 'New routine'],
        ];
        if (COLLECTS.includes(definition.job_type)) {
            facts.push(['Saved sources', sources.length ? sources.join(', ') : 'None saved']);
            if (extras.length) facts.push(['Routine-only sources', extras.join(', ')]);
        }
        if (COLLECTS.includes(definition.job_type) || DRIPS.includes(definition.job_type)) {
            facts.push(['Collection', setup.collection ? setup.collection.name || setup.collection.playlist_id :
                'Not linked']);
        }
        if (DRIPS.includes(definition.job_type)) {
            facts.push(['Drip', setup.collection && setup.collection.drip_enabled ?
                'Enabled · ' + (params.drip_count || setup.collection.drip_count) + ' tracks per run' : 'Off']);
        }
        if (definition.job_type === 'rotate' || COLLECTS.includes(definition.job_type) && setup.archive) {
            facts.push(['Archive', setup.archive ? setup.archive.name || setup.archive.playlist_id : 'Not paired']);
        }
        if (SHUFFLES.includes(definition.job_type)) {
            const algorithm = (state.context.algorithms || []).find(item =>
                item.class_name === definition.algorithm_name);
            facts.push(['Shuffle method', algorithm ? algorithm.name : 'Unavailable algorithm']);
        }
        facts.push(...optionFacts(definition, params));
        if (DRIPS.includes(definition.job_type) && params.drip_count !== undefined)
            facts.push(['Routine Drip count', String(params.drip_count) + ' tracks per run']);
        const unchanged = state.row && !state.timingDirty && state.row.definition.schedule_type ===
            definition.schedule_type && state.row.definition.schedule_value === definition.schedule_value;
        facts.push(['Planned timing', unchanged ? state.row.timing.text : friendlyTiming(definition)]);
        facts.push(['Playlist setup', 'Settings saved there remain if you close this task']);
        return facts;
    }
    function renderReview() {
        replace('routine-review-details', [(() => {
            const list = element('dl'); reviewFacts().forEach(pair => list.appendChild(fact(pair[0], pair[1])));
            return list;
        })()]);
        const candidates = state.review && state.review.candidate_schedule_ids || [];
        const blocked = candidates.length && !state.scheduleId && !state.createAnother;
        byId('routine-review-warning').hidden = !blocked;
        if (blocked) text('routine-review-warning', 'Choose an exact routine or Create another, then review again.');
    }
    function registrationWords(row) {
        if (row && row.state === 'Deleted') return 'Routine deleted after accepted activation';
        const registration = row && row.registration || {};
        if (registration.status === 'registered') return 'Active' + (registration.next_run_at ?
            ' · Next run ' + registration.next_run_at : ' · Next run unavailable');
        if (registration.status === 'paused') return 'Paused · future runs stopped';
        return 'Saved; scheduling needs attention (' + (registration.status || 'unavailable') + ')';
    }
    function renderResult() {
        const row = state.accepted || state.row;
        if (!row) return;
        const id = row.schedule_id;
        text('routine-result-heading', row.state === 'Deleted' ? 'Accepted routine was later deleted' :
            state.phase === 'accepted' ? 'Routine saved' : 'Exact routine #' + id);
        const list = element('dl');
        list.append(fact('Routine', '#' + id), fact('State', row.state || 'Saved'),
            fact('Scheduling', registrationWords(row)));
        if (state.accepted && state.accepted.accepted_at) list.append(fact('Accepted', state.accepted.accepted_at));
        replace('routine-result-details', [list]);
        const actions = [];
        (row.state === 'Deleted' ? [] : [['History', 'history'], ['Run now', 'run'], ['Delete', 'delete']])
            .forEach(([label, view]) => {
            const link = element('a', label + ' for routine #' + id, 'sh-button-secondary');
            link.href = PAGE.urls.management + '?routine_id=' + id + '&view=' + view;
            actions.push(link);
        });
        replace('routine-result-actions', actions);
    }
    function renderSummary() {
        if (!state.context) return;
        replace('routine-summary-list', [
            fact('Playlist', state.context.target.name),
            fact('Operation', OPERATIONS[state.operation] || 'Choose a goal'),
            fact('Routine', state.scheduleId ? '#' + state.scheduleId : 'New'),
            fact('Timing', draftTiming()),
        ]);
        text('routine-summary-note', state.accepted ? registrationWords(state.accepted) :
            'Planned timing is not an actual next run. Review makes no changes.');
    }
    function render(focus) {
        const loading = !state.context && state.phase === 'loading';
        const backUrl = state.origin === 'routines' ? PAGE.urls.management : PAGE.urls.workshop;
        byId('routine-close').href = backUrl;
        byId('routine-error-back').href = backUrl;
        byId('routine-progress').hidden = loading || Boolean(state.scheduleId) ||
            ['error', 'accepted', 'unknown', 'hard_error'].includes(state.phase);
        const shown = state.phase === 'error' ? 'error' : state.phase === 'accepted' ? 'result' :
            state.phase === 'conflict' ? 'error' : state.step;
        byId('routine-task').dataset.view = shown;
        ['goal', 'connections', 'timing', 'review', 'result', 'error'].forEach(name => {
            const compactTiming = name === 'timing' && Boolean(state.scheduleId) && shown === 'connections';
            byId('routine-' + name + '-step').hidden = loading || (name !== shown && !compactTiming);
        });
        if (state.phase === 'error' || state.phase === 'conflict') text('routine-error-detail', state.error ||
            'The exact routine changed. Return to routines to inspect its current definition.');
        byId('routine-adopt-current').hidden = state.phase !== 'conflict' || !exactRow();
        if (state.context) {
            renderConnections(); renderTiming(); renderReview(); renderSummary();
            if (state.scheduleId) renderResult();
        }
        document.querySelectorAll('[data-routine-progress]').forEach(node => {
            if (node.dataset.routineProgress === state.step) node.setAttribute('aria-current', 'step');
            else node.removeAttribute('aria-current');
        });
        const busy = ['reviewing', 'activating', 'state'].includes(state.phase);
        byId('routine-task').toggleAttribute('aria-busy', busy);
        const footer = byId('routine-footer');
        footer.hidden = loading || state.phase === 'error' || state.phase === 'conflict';
        byId('routine-back').hidden = state.step === 'goal' || state.phase === 'accepted' || state.phase === 'unknown' ||
            state.phase === 'hard_error' || state.scheduleId && state.step === 'connections';
        byId('routine-next').hidden = !['goal', 'connections'].includes(state.step) || Boolean(state.scheduleId) ||
            state.phase === 'accepted' ||
            state.phase === 'unknown' || state.phase === 'hard_error';
        byId('routine-review').hidden = !(state.step === 'timing' ||
            Boolean(state.scheduleId) && state.step === 'connections') || state.phase === 'accepted' ||
            state.phase === 'unknown' || state.phase === 'hard_error';
        byId('routine-activate').hidden = state.step !== 'review' || state.phase === 'accepted' ||
            state.phase === 'unknown' || state.phase === 'hard_error';
        byId('routine-activate').textContent = state.scheduleId ? 'Save routine' : 'Activate routine';
        byId('routine-check').hidden = state.phase !== 'unknown';
        byId('routine-try-registration').hidden = state.phase !== 'accepted' || !state.accepted ||
            state.accepted.state === 'Deleted' ||
            !['needs_attention', 'unavailable'].includes((state.accepted.registration || {}).status);
        const row = state.accepted || state.row;
        byId('routine-state').hidden = !row || row.state === 'Deleted' ||
            state.phase === 'unknown' || state.phase === 'hard_error';
        if (row) byId('routine-state').textContent = row.schedule && row.schedule.is_enabled ?
            'Pause routine #' + row.schedule_id : 'Resume routine #' + row.schedule_id;
        ['routine-next', 'routine-back', 'routine-review', 'routine-activate', 'routine-check',
            'routine-state', 'routine-try-registration'].forEach(id => {
            if (busy) byId(id).setAttribute('aria-disabled', 'true');
            else byId(id).removeAttribute('aria-disabled');
        });
        if (focus) {
            const heading = byId('routine-' + shown + '-heading');
            if (heading) {
                heading.focus({preventScroll: true});
                // The mobile footer and hidden steps resize after focus. Scroll once the
                // resulting layout has settled so the focused heading remains visible.
                const reveal = () => {
                    if (document.activeElement === heading && !heading.closest('[hidden]'))
                        heading.scrollIntoView({block: 'start', behavior: 'instant'});
                };
                if (typeof window.requestAnimationFrame === 'function')
                    window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
                else reveal();
            }
        }
    }
    function next() {
        if (state.phase !== 'idle') return;
        if (state.step === 'goal') {
            state.step = 'connections'; if (!state.operation) selectOperation(GOALS[state.goal][0]);
        } else if (state.step === 'connections') {
            if (!state.scheduleId && candidates().length && !state.createAnother) {
                text('routine-choice-error', 'Choose an exact routine or Create another routine.');
                byId('routine-choice-error').hidden = false; return;
            }
            captureFields(); state.step = 'timing';
        }
        render(true);
    }
    function back() {
        if (state.phase !== 'idle') return;
        if (state.step === 'review') {
            state.step = state.scheduleId ? 'connections' : 'timing';
            state.review = null; state.reviewedRequest = null;
        }
        else if (state.step === 'timing') { captureFields(); state.step = 'connections'; }
        else if (state.step === 'connections' && !state.scheduleId) state.step = 'goal';
        render(true);
    }
    function setup(section, trigger) {
        if (state.phase !== 'idle' || !['sources', 'collection', 'archive'].includes(section)) return;
        const path = PAGE.urls.setup + '?section=' + encodeURIComponent(section) + '&return=' +
            encodeURIComponent(PAGE.urls.self);
        const saved = persistContinuation();
        const leave = () => {
            state.handoff = path;
            window.AppNavigation.assign(path, trigger).then(allowed => {
                if (!allowed) { state.handoff = null; if (saved) discardContinuation(); }
            });
        };
        if (saved) leave();
        else {
            window.AppConfirm.ask({title: 'Leave without keeping routine choices?',
                message: 'This browser cannot keep your task choices for the setup return. ' +
                    'Changes in Playlist setup can still be saved independently.',
                confirmLabel: 'Continue to setup', cancelLabel: 'Stay', tone: 'danger', trigger})
                .then(allowed => { if (allowed) leave(); });
        }
    }
    function adoptCurrent() {
        if (state.phase !== 'conflict') return;
        const row = exactRow();
        if (!row) return;
        applyRow(row); state.continuation = null; state.phase = 'idle'; state.step = 'connections';
        state.review = null; state.reviewedRequest = null; state.activationRequest = null;
        state.dirty = true;
        announce('Current routine definition loaded. Review again before saving.'); render(true);
    }
    function navigationGuard(detail) {
        if (['activating', 'reviewing', 'state'].includes(state.phase)) {
            announce('Wait for this request to finish. Leaving does not cancel it.'); return false;
        }
        if (state.handoff && detail && detail.url === state.handoff) return true;
        if (!state.dirty && state.phase !== 'unknown') return true;
        const attention = state.phase === 'unknown';
        return window.AppConfirm.ask({
            title: attention ? 'Leave an unconfirmed activation?' : 'Discard routine changes?',
            message: attention ? 'The routine may already be saved. Check activation on this page before leaving. ' +
                'Recovery details are held only here.' : 'Unsaved routine choices will be discarded. ' +
                'Settings you saved in Playlist setup and Spotify playlists you created will remain.',
            confirmLabel: attention ? 'Leave anyway' : 'Discard and leave', cancelLabel: 'Stay',
            tone: 'danger', trigger: detail && detail.element,
        }).then(allowed => { if (allowed) discardContinuation(); return allowed; });
    }
    function unloadCheck(approved) {
        return ['activating', 'reviewing', 'state'].includes(state.phase) ||
            (!approved && (state.dirty || state.phase === 'unknown'));
    }
    window.registerActions({
        'routine.goal': control => selectGoal(control.dataset.goal),
        'routine.operation': control => selectOperation(control.value),
        'routine.choose': control => chooseRow(Number(control.dataset.scheduleId)),
        'routine.create-another': createAnother,
        'routine.algorithm': control => {
            captureFields(); state.algorithm = control.value; state.params = {}; invalidate(); render();
        },
        'routine.edit': control => {
            if (control.closest('#routine-timing-step')) state.timingDirty = true;
            captureFields(); invalidate();
        },
        'routine.timing-kind': control => {
            state.timingDirty = true; state.timing.kind = control.value; captureFields(); invalidate(); render();
        },
        'routine.setup': (control) => setup(control.dataset.section, control),
        'routine.adopt': adoptCurrent,
        'routine.next': next, 'routine.back': back,
        'routine.review': reviewRoutine, 'routine.activate': activate, 'routine.check': checkActivation,
        'routine.try-registration': () => setRoutineState(true),
        'routine.state': () => {
            const row = state.accepted || state.row;
            if (row) setRoutineState(!(row.schedule && row.schedule.is_enabled));
        },
    });
    if (window.AppNavigation) {
        window.AppNavigation.addGuard(navigationGuard);
        window.AppNavigation.addUnloadCheck(unloadCheck);
    }
    if (window.AppPanels) window.AppPanels.reserveFooter(byId('routine-footer'), document.documentElement);
    refreshContext(true).then(ready => { if (ready) byId('routine-heading').focus(); });
    // A narrow read-only seam for the runtime contract tests.
    window.GuidedRoutine = Object.freeze({operations: OPERATIONS, continuationKey: KEY});
}());
