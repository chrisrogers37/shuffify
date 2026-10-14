/** Library quick shuffle: selecting a method never writes to Spotify; Run follows review. */
(function () {
    'use strict';

    function init() {
        const library = document.querySelector('.sh-library');
        if (!library) return;

        const operations = window.PlaylistOperations;
        // Option edits after a review invalidate that review before anything is sent.
        const revisions = new Map();
        // Each card's display hooks, by playlist ID.
        const cardsById = new Map();

        // A verified restore names the playlist's verified contents; their length is its track count.
        function verifiedTrackCount(record) {
            const resources = record.operation ? record.operation.resources : [];
            const target = resources.find(resource => resource.resource === record.intent.targetId &&
                (Array.isArray(resource.current_track_uris) || Array.isArray(resource.desired_track_uris)));
            if (!target) return null;
            return (Array.isArray(target.current_track_uris) ? target.current_track_uris : target.desired_track_uris).length;
        }

        function setVisibilityDisplay(playlistId, publicState) {
            const tile = Array.from(library.querySelectorAll('.card-tile'))
                .find(card => card.dataset.playlistId === playlistId);
            const button = tile && tile.querySelector('.visibility-btn');
            if (!button) throw new Error('Playlist control unavailable');
            button.dataset.public = String(publicState);
            // Its visible words name the button: a Spotify change made now, after its review.
            const label = button.querySelector('.visibility-label');
            if (label) label.textContent = publicState ? 'Make private now' : 'Make public now';
        }

        // Undo history lives in the shared Flask session, so the controller serializes
        // every Library write and keeps viewing controls available while one runs.
        operations.configure({
            getInputRevision: (kind, playlistId) => (kind === 'shuffle' ? revisions.get(playlistId) || 0 : 0),
            onBusy: running => {
                if (running) library.setAttribute('aria-busy', 'true');
                else library.removeAttribute('aria-busy');
            },
            afterComplete: record => {
                if (record.intent.kind === 'undo') {
                    // Restoring can add or remove tracks; a shuffle keeps the count it had.
                    const count = verifiedTrackCount(record), card = cardsById.get(record.intent.targetId);
                    if (count === null || !card) throw new Error('Verified track count unavailable');
                    card.setTrackCount(count);
                    return;
                }
                if (record.intent.kind !== 'privacy') return;
                const publicState = record.response.public;
                if (typeof publicState !== 'boolean') throw new Error('Verified visibility unavailable');
                setVisibilityDisplay(record.intent.targetId, publicState);
            },
            // A matching check shows the requested state it observed; it never claims causation.
            afterObservation: record => setVisibilityDisplay(record.intent.targetId, record.intent.action.public),
        });
        window.LibraryPlaylistActions = {
            setVisibility: async (playlistId, button) => {
                if (operations.isBusy()) return null;
                const desiredPublic = button.dataset.public !== 'true';
                const handle = await operations.prepare({kind: 'privacy', targetId: playlistId,
                    body: {public: desiredPublic}, trigger: button});
                // The shared announcer reports the run; the Library adds no second message.
                return handle ? operations.run(handle) : null;
            },
        };

        library.querySelectorAll('.library-shuffle-form').forEach(form => {
            const tile = form.closest('.card-tile');
            const playlistId = form.dataset.playlistId;
            const select = form.querySelector('.library-shuffle-method');
            const details = form.querySelector('.library-shuffle-details');
            const summary = form.querySelector('.library-shuffle-summary');
            const params = form.querySelector('.library-shuffle-params');
            const description = form.querySelector('.library-shuffle-description');
            const quick = form.querySelector('.library-quick-shuffle');
            const undo = form.querySelector('.library-undo');
            const undoForm = tile.querySelector('.undo-form');
            const feedback = form.querySelector('.library-shuffle-feedback');
            let trackCount = Number(form.dataset.trackCount);
            const defaults = JSON.parse(form.dataset.shuffleDefaults);
            const valuesByMethod = new Map();
            let previousMethod = '';

            // A verified restore's count: the card's label, the option limits and, for a
            // card with a method picker, whether Shuffle now can run. Option values stay.
            cardsById.set(playlistId, {setTrackCount: count => {
                trackCount = count;
                form.dataset.trackCount = String(count);
                const label = tile.querySelector('.library-track-count');
                if (label) label.textContent = count + (count === 1 ? ' track' : ' tracks');
                const keepFirst = params && params.querySelector('input[name="keep_first"]');
                if (keepFirst) keepFirst.max = String(count);
                if (select) operations.setControlDisabled(quick, count <= 0);
            }});

            // Enter in an option field is not a request to modify Spotify.
            form.addEventListener('submit', event => event.preventDefault());
            undo.addEventListener('click', () => updatePlaylist('undo'));
            // Empty playlists render no method picker; their quick shuffle stays disabled.
            if (!select) return;
            // Any user edit of the method or its options counts; rendering does not.
            const revise = () => revisions.set(playlistId, (revisions.get(playlistId) || 0) + 1);
            form.addEventListener('input', revise);
            form.addEventListener('change', revise);

            // The card's own note shows a run's outcome as text and never takes focus. The
            // shared announcer already reads every reviewed run, so the note is not a live
            // region; only a client error the controller never announces (a review that could
            // not start, a run that threw) is read out, as an alert.
            function note(message, error, alert) {
                feedback.textContent = message;
                feedback.hidden = false;
                feedback.classList.toggle('sh-field-error', error);
                feedback.classList.toggle('sh-text-muted', !error);
                if (alert) feedback.setAttribute('role', 'alert');
                else feedback.removeAttribute('role');
            }

            function defaultValue(info, value) {
                if (info.options) return info.options.includes(value) ? value : info.default;
                if (info.type === 'integer' || info.type === 'float') {
                    if (typeof value !== 'number' || !Number.isFinite(value)) return info.default;
                    if (info.type === 'integer' && !Number.isInteger(value)) return info.default;
                    if (info.min !== undefined && value < info.min) return info.default;
                    if (info.max !== undefined && value > info.max) return info.default;
                    return value;
                }
                return typeof value === 'string' ? value : info.default;
            }

            function renderMethod() {
                if (previousMethod) {
                    valuesByMethod.set(previousMethod, Object.fromEntries(
                        Array.from(params.querySelectorAll('input, select')).map(input => [input.name, input.value])
                    ));
                }
                const option = select.selectedOptions[0];
                const method = select.value;
                params.replaceChildren();
                description.textContent = method ? option.dataset.description : 'Choose how you want this playlist to change.';
                form.querySelector('.library-method-name').textContent = method ? option.textContent : 'Choose method';
                form.querySelector('.library-method-change').hidden = !method;
                quick.textContent = method ? 'Shuffle now' : 'Choose shuffle';
                const cachedValues = valuesByMethod.get(method);
                const savedValues = defaults.algorithm === method ? defaults.params : {};
                const parameters = method ? JSON.parse(option.dataset.parameters) : {};

                Object.entries(parameters).forEach(([name, info]) => {
                    const wrapper = document.createElement('div');
                    const label = document.createElement('label');
                    const id = `shuffle-param-${form.dataset.playlistId}-${name}`;
                    label.className = 'sh-field-label';
                    label.htmlFor = id;
                    label.textContent = name === 'keep_first' ? 'Keep first tracks' : info.description;
                    const input = document.createElement(info.options ? 'select' : 'input');
                    input.className = 'sh-input';
                    input.name = name;
                    input.id = id;
                    input.required = true;
                    let value = defaultValue(info, savedValues && savedValues[name]);
                    if (name === 'keep_first') value = Math.min(Number(value), trackCount);
                    if (info.options) {
                        info.options.forEach(choice => {
                            const item = document.createElement('option');
                            item.value = choice;
                            item.textContent = choice;
                            input.appendChild(item);
                        });
                    } else {
                        input.type = ['integer', 'float'].includes(info.type) ? 'number' : 'text';
                        if (info.min !== undefined) input.min = info.min;
                        if (info.max !== undefined) input.max = info.max;
                        if (name === 'keep_first') input.max = trackCount;
                        if (info.type === 'integer') input.step = '1';
                        if (info.type === 'float') input.step = 'any';
                    }
                    input.value = cachedValues && Object.hasOwn(cachedValues, name) ? cachedValues[name] : value;
                    wrapper.append(label, input);
                    params.appendChild(wrapper);
                });
                previousMethod = method;
            }

            function closeOptions() {
                details.open = false;
                summary.focus();
            }

            async function updatePlaylist(kind) {
                if (operations.isBusy() || (kind === 'shuffle' && trackCount <= 0)) return;
                if (kind === 'shuffle') {
                    if (!select.value) {
                        details.open = true;
                        select.focus();
                        return;
                    }
                    const invalid = Array.from(params.querySelectorAll('input, select'))
                        .find(input => !input.checkValidity());
                    if (invalid) {
                        details.open = true;
                        invalid.reportValidity();
                        return;
                    }
                }
                const target = kind === 'undo' ? undoForm : form;
                const body = Object.fromEntries(new FormData(target));
                const button = kind === 'undo' ? undo : quick;
                let handle;
                try {
                    handle = await operations.prepare({kind, targetId: playlistId, body, trigger: button});
                } catch (error) {
                    note(error.message || 'This action could not be reviewed. Your options are still here.', true, true);
                    return;
                }
                // Cancel, invalid or changed reviews send nothing.
                if (!handle) return;
                const label = button.textContent;
                button.textContent = kind === 'undo' ? 'Restoring…' : 'Shuffling…';
                try {
                    const record = await operations.run(handle);
                    if (!record) return;
                    const data = record.response || {};
                    // Restoring the earliest saved version hides Undo; focus stays in the results.
                    if (record.complete && data.playlist_state && Number.isInteger(data.playlist_state.current_index)) {
                        undo.classList.toggle('hidden', data.playlist_state.current_index <= 0);
                    }
                    if (record.complete && !record.attention) {
                        note(data.message || (kind === 'undo' ? 'Previous saved version restored.' : 'Playlist shuffled.'),
                            false, false);
                    } else {
                        note(record.refreshMessage || record.message || data.message ||
                            'This action needs attention. View the live action results.', true, false);
                    }
                } catch (error) {
                    note(error.message || 'Could not update the playlist. Your options are still here.', true, true);
                } finally {
                    button.textContent = label;
                }
            }

            select.disabled = false;
            quick.disabled = trackCount <= 0;
            renderMethod();
            select.addEventListener('change', renderMethod);
            quick.addEventListener('click', () => updatePlaylist('shuffle'));
            form.querySelector('.library-shuffle-done').addEventListener('click', closeOptions);
        });

        // Disclosures are view-only and stay usable while a live action runs.
        library.querySelectorAll('details').forEach(details => {
            details.addEventListener('keydown', event => {
                if (event.key !== 'Escape' || event.target.tagName === 'SELECT' || !details.open) return;
                event.preventDefault();
                event.stopPropagation();
                details.open = false;
                details.querySelector('summary').focus();
            });
        });
    }

    document.addEventListener('DOMContentLoaded', init);
}());
