/** Exact-ID routine cards shared by the Routines page and Workshop. */
(function (global) {
    'use strict';

    const labels = {
        raid: 'Collect', shuffle: 'Shuffle', raid_and_shuffle: 'Collect and shuffle',
        raid_and_drip: 'Collect and add', drip: 'Add collected tracks', rotate: 'Rotate',
    };
    const statuses = {success: 'Succeeded', failed: 'Failed', running: 'In progress',
        skipped: 'Did not run', partial: 'Partly completed', unknown: 'Outcome unknown',
        failed_rolled_back: 'Failed step restored'};

    function element(tag, className, value) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (value !== undefined) node.textContent = value;
        return node;
    }
    function button(className, action, id, label) {
        const node = element('button', className, label);
        node.type = 'button';
        node.dataset.routineAction = action;
        node.dataset.scheduleId = String(id);
        return node;
    }
    function dateLabel(value) {
        if (!value) return null;
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? null : date.toLocaleString();
    }
    function editUrl(row, origin) {
        return '/playlist/' + encodeURIComponent(row.schedule.target_playlist_id) +
            '/routines/new?schedule_id=' + row.schedule_id + '&from=' + origin;
    }

    function create(options) {
        const root = options.root;
        const targetId = options.targetId || null;
        let rows = (options.rows || []).slice();
        let latest = {};
        let latestError = false;
        let latestRead = 0;
        let prerequisites = {};
        let busyId = null;
        const pending = new Set();
        const send = options.request || ((url, init) => fetch(url, init));
        const list = root.querySelector('[data-routine-list]');
        const empty = root.querySelector('[data-routine-empty]');
        const matchEmpty = root.querySelector('[data-routine-no-match]');
        const loading = root.querySelector('[data-routine-loading]');
        const error = root.querySelector('[data-routine-error]');
        const resultError = root.querySelector('[data-routine-result-error]');
        const count = root.querySelector('[data-routine-count]');
        const filters = {
            playlist: root.querySelector('[data-routine-filter="playlist"]'),
            type: root.querySelector('[data-routine-filter="type"]'),
            state: root.querySelector('[data-routine-filter="state"]'),
        };

        function filtered() {
            return rows.filter(row =>
                (!filters.playlist || !filters.playlist.value || row.schedule.target_playlist_id === filters.playlist.value) &&
                (!filters.type || !filters.type.value || row.schedule.job_type === filters.type.value) &&
                (!filters.state || !filters.state.value || row.state === filters.state.value));
        }
        function createCard(row) {
            const id = row.schedule_id;
            const card = element('article', 'schedule-card sh-routine-card');
            card.dataset.scheduleId = String(id);
            const title = element('h2', 'sh-routine-title', row.schedule.target_playlist_name || 'Playlist');
            title.id = 'routine-title-' + id;
            title.tabIndex = -1;
            card.appendChild(title);
            card.appendChild(element('p', 'sh-detail', (labels[row.schedule.job_type] || row.schedule.job_type) +
                ' · Routine #' + id));
            const state = element('p', 'routine-status sh-badge', row.state || 'Saved—registration needs attention');
            card.appendChild(state);
            const timing = row.timing || {};
            card.appendChild(element('p', 'sh-detail sh-text-muted', 'Planned: ' + (timing.text || 'Timing unavailable') +
                (timing.effective_timezone ? ' · ' + timing.effective_timezone : '')));
            if (timing.legacy) card.appendChild(element('p', 'sh-caption', 'Legacy timing uses the scheduler timezone.'));
            if (timing.explanation) card.appendChild(element('p', 'sh-caption', timing.explanation));
            const next = row.registration && row.registration.status === 'registered' ?
                dateLabel(row.registration.next_run_at) : null;
            card.appendChild(element('p', 'sh-caption', next ? 'Registered next run: ' + next :
                'Registered next run: unavailable'));
            if (row.state && row.state.includes('needs attention')) {
                card.appendChild(element('p', 'sh-routine-callout-warning sh-caption',
                    'Routine saved; scheduling needs attention. Open this routine to check it.'));
            }
            const check = prerequisites[row.schedule.target_playlist_id];
            const capability = check && check !== 'loading' && check[row.schedule.job_type];
            if (check === 'loading') {
                card.appendChild(element('p', 'sh-caption', 'Checking prerequisites…'));
            } else if (check === null) {
                card.appendChild(element('p', 'sh-routine-callout-warning sh-caption',
                    'Prerequisites could not be checked. Reload to try again.'));
            } else if (capability && !capability.available) {
                card.appendChild(element('p', 'sh-routine-callout-warning sh-caption',
                    'Missing prerequisite: ' + (capability.reason || 'Check playlist settings.')));
            }
            const latestSlot = element('div', 'sh-routine-latest');
            latestSlot.dataset.routineLatest = String(id);
            const execution = latest[id];
            if (execution === null || execution === undefined) {
                latestSlot.textContent = latestError ? 'Latest result unavailable.' : 'Never run.';
            } else if (global.RoutineResults) {
                global.RoutineResults.renderExecution(latestSlot, execution, {density: 'compact'});
            } else {
                latestSlot.textContent = 'Last run: ' + (statuses[execution.status] || 'Needs attention');
            }
            card.appendChild(latestSlot);

            const actions = element('div', 'sh-routine-actions');
            const edit = element('a', 'routine-edit sh-button-secondary', 'Edit this routine');
            edit.href = editUrl(row, options.origin || 'routines');
            edit.dataset.scheduleId = String(id);
            actions.appendChild(edit);
            const pause = button('routine-toggle sh-button-secondary', 'state', id,
                row.schedule.is_enabled ? 'Pause' : 'Resume');
            actions.appendChild(pause);
            const run = button('routine-run sh-button-secondary', 'run', id, 'Run now');
            run.dataset.liveOperationKey = 'schedule:' + id;
            actions.appendChild(run);
            const history = button('routine-history sh-button-secondary', 'history', id, 'History');
            history.setAttribute('aria-expanded', 'false');
            history.setAttribute('aria-controls', 'history-' + id);
            actions.appendChild(history);
            actions.appendChild(button('routine-delete sh-button-danger', 'delete', id, 'Delete routine'));
            // Each action is described by the card's title, so it names the routine it acts on.
            actions.querySelectorAll('a, button').forEach(control => control.setAttribute('aria-describedby', title.id));
            card.appendChild(actions);
            const historySlot = element('div', 'sh-routine-disclosure');
            historySlot.id = 'history-' + id;
            historySlot.hidden = true;
            card.appendChild(historySlot);
            if (options.workshop) {
                actions.querySelectorAll('button, a').forEach(control => {
                    control.dataset.workshopCompat = control === pause ? 'pause' :
                        control === history ? 'view' : control === run ? 'view' : 'routine';
                });
            }
            if (busyId === id) {
                card.setAttribute('aria-busy', 'true');
                run.disabled = true;
                card.querySelector('.routine-delete').disabled = true;
            }
            return card;
        }
        function render() {
            const openHistory = new Map();
            list.querySelectorAll('.schedule-card').forEach(card => {
                const slot = card.querySelector('.sh-routine-disclosure');
                if (slot && !slot.hidden) openHistory.set(card.dataset.scheduleId, slot);
            });
            const focused = list.contains(document.activeElement) ? document.activeElement : null;
            const focusedCard = focused && focused.closest('.schedule-card');
            const focusAction = focused && focused.dataset.routineAction;
            const focusEdit = focused && focused.classList.contains('routine-edit');
            const focusId = focusedCard && focusedCard.dataset.scheduleId;
            const visible = filtered();
            list.replaceChildren(...visible.map(createCard));
            openHistory.forEach((oldSlot, id) => {
                const card = list.querySelector('.schedule-card[data-schedule-id="' + id + '"]');
                if (!card) return;
                card.querySelector('.sh-routine-disclosure').remove();
                card.appendChild(oldSlot);
                card.querySelector('.routine-history').setAttribute('aria-expanded', 'true');
            });
            if (count) count.textContent = rows.length + ' routine' + (rows.length === 1 ? '' : 's');
            if (empty) empty.hidden = rows.length !== 0;
            if (matchEmpty) matchEmpty.hidden = rows.length === 0 || visible.length !== 0;
            if (global.PlaylistOperations) global.PlaylistOperations.syncControls();
            if (options.afterRender) options.afterRender();
            if (focusId) {
                const replacement = list.querySelector('.schedule-card[data-schedule-id="' + focusId + '"] ' +
                    (focusEdit ? '.routine-edit' : '[data-routine-action="' + focusAction + '"]'));
                if (replacement) replacement.focus();
            }
        }
        async function loadLatest() {
            const read = ++latestRead;
            try {
                const suffix = targetId ? '?playlist_id=' + encodeURIComponent(targetId) : '';
                const reply = await fetch('/schedules/latest-results' + suffix, {cache: 'no-store'});
                const data = await reply.json();
                if (!reply.ok || !data.success || !data.results) throw new Error('Latest results unavailable');
                if (read !== latestRead) return;
                latest = data.results;
                latestError = false;
            } catch (_) {
                if (read !== latestRead) return;
                latest = {};
                latestError = true;
            }
            if (resultError) resultError.hidden = !latestError;
            render();
        }
        async function loadHistory(row, slot, trigger) {
            slot.hidden = false;
            trigger.setAttribute('aria-expanded', 'true');
            slot.textContent = 'Loading recent history…';
            try {
                const reply = await fetch('/schedules/' + row.schedule_id + '/history', {cache: 'no-store'});
                const data = await reply.json();
                if (!reply.ok || !data.success || !Array.isArray(data.history)) throw new Error('History unavailable');
                slot.replaceChildren();
                if (!data.history.length) {
                    slot.textContent = 'No executions yet.';
                    return;
                }
                slot.appendChild(element('p', 'sh-caption', 'Latest ' + data.history.length + ' executions'));
                data.history.forEach(execution => {
                    const item = element('div', 'sh-routine-history-row');
                    global.RoutineResults.renderExecution(item, execution, {density: 'full'});
                    slot.appendChild(item);
                });
            } catch (_) {
                slot.textContent = 'Could not load this routine’s history. Try again.';
            }
        }
        async function changeState(row) {
            const key = 'state:' + row.schedule_id;
            if (pending.has(key)) return;
            pending.add(key);
            let refresh = false;
            try {
                const desired = !row.schedule.is_enabled;
                const reply = await send('/routines/' + row.schedule_id + '/state', {
                    method: 'PUT', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({desired_enabled: desired, expected_revision: row.schedule_revision}),
                }, {kind: 'state', scheduleId: row.schedule_id});
                const data = await reply.json();
                if (!reply.ok || !data.success || data.schedule_id !== row.schedule_id) {
                    throw new Error(data.message || 'Could not change this routine. Refresh and try again.');
                }
                rows = rows.map(item => item.schedule_id === row.schedule_id ? data : item);
                if (options.onChange) options.onChange('state', row.schedule_id);
                render();
            } catch (error) {
                if (options.onError) options.onError(error instanceof TypeError ?
                    'Could not confirm this state change. Check the current routine before trying again.' : error.message);
                refresh = true;
            } finally { pending.delete(key); }
            if (refresh && options.refresh) await options.refresh();
        }
        async function remove(row, trigger) {
            if (busyId === row.schedule_id || pending.has('delete:' + row.schedule_id)) return;
            const askConfirmation = global.AppConfirm && global.AppConfirm.ask;
            if (!askConfirmation || await askConfirmation({title: 'Delete this routine?',
                message: 'This removes routine #' + row.schedule_id + ' and its run history. It does not delete the Spotify playlist.',
                confirmLabel: 'Delete routine', cancelLabel: 'Keep routine'}) !== true) return;
            pending.add('delete:' + row.schedule_id);
            let refresh = false;
            try {
                const reply = await send('/schedules/' + row.schedule_id, {method: 'DELETE'},
                    {kind: 'delete', scheduleId: row.schedule_id});
                const data = await reply.json();
                if (!reply.ok || !data.success) throw new Error(data.message || 'Could not delete this routine.');
                const next = trigger.closest('.schedule-card').nextElementSibling ||
                    trigger.closest('.schedule-card').previousElementSibling;
                rows = rows.filter(item => item.schedule_id !== row.schedule_id);
                if (options.onChange) options.onChange('delete', row.schedule_id);
                render();
                const focus = next && list.querySelector('[data-schedule-id="' + next.dataset.scheduleId + '"] .routine-edit');
                (focus || root.querySelector('[data-routine-heading]') || root).focus();
            } catch (error) {
                if (options.onError) options.onError(error instanceof TypeError ?
                    'Could not confirm deletion. Check the current routine before trying again.' : error.message);
                refresh = true;
            } finally { pending.delete('delete:' + row.schedule_id); }
            if (refresh && options.refresh) await options.refresh();
        }
        root.addEventListener('click', event => {
            const trigger = event.target.closest('[data-routine-action]');
            if (!trigger || !root.contains(trigger)) return;
            const id = Number(trigger.dataset.scheduleId);
            const row = rows.find(item => item.schedule_id === id);
            if (!row) return;
            const action = trigger.dataset.routineAction;
            if (action === 'state') changeState(row);
            if (action === 'delete') remove(row, trigger);
            if (action === 'run' && options.onRun) options.onRun(row, trigger);
            if (action === 'history') {
                const slot = root.querySelector('#history-' + id);
                if (!slot.hidden) { slot.hidden = true; trigger.setAttribute('aria-expanded', 'false'); }
                else loadHistory(row, slot, trigger);
            }
        });
        Object.values(filters).forEach(control => { if (control) control.addEventListener('change', render); });
        if (global.AppNavigation) {
            global.AppNavigation.addGuard(() => {
                if (!pending.size) return true;
                if (options.onError) options.onError('Wait for this routine change to finish before leaving.');
                return false;
            });
            global.AppNavigation.addUnloadCheck(() => pending.size > 0);
        }
        return {
            render, loadLatest, rows: () => rows.slice(),
            setRows(next) { rows = next.slice(); render(); },
            setPrerequisites(next) { prerequisites = next || {}; render(); },
            setPrerequisite(target, value) { prerequisites[target] = value; render(); },
            setBusy(id) {
                busyId = id;
                list.querySelectorAll('.schedule-card').forEach(card => {
                    const running = Number(card.dataset.scheduleId) === id;
                    if (running) card.setAttribute('aria-busy', 'true');
                    else card.removeAttribute('aria-busy');
                    const run = card.querySelector('.routine-run');
                    const remove = card.querySelector('.routine-delete');
                    if (run) run.disabled = running;
                    if (remove) remove.disabled = running;
                });
            },
            showLoading(active) { if (loading) loading.hidden = !active; },
            showError(active) {
                if (error) error.hidden = !active;
                if (active) {
                    if (empty) empty.hidden = true;
                    if (matchEmpty) matchEmpty.hidden = true;
                }
            },
            openHistory(id) {
                const trigger = root.querySelector('[data-routine-action="history"][data-schedule-id="' + id + '"]');
                if (trigger) { trigger.click(); trigger.closest('.schedule-card').scrollIntoView({block: 'start'}); }
            },
        };
    }
    global.RoutineManagement = {create: create, editUrl: editUrl};
})(window);
