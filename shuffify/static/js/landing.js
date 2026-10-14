/* The consent form keeps its native required validation without JavaScript. */
(function () {
    'use strict';

    function initializeConsent() {
        const checkbox = document.getElementById('legal-consent');
        const loginButton = document.getElementById('login-button');
        const caption = document.getElementById('cta-subtext');
        if (!checkbox || !loginButton) return;

        function updateButtonState() {
            loginButton.disabled = !checkbox.checked;
            if (caption) {
                caption.textContent = checkbox.checked
                    ? 'Free · Ready to Start'
                    : 'Free · Approve & Connect';
            }
        }

        checkbox.addEventListener('change', updateButtonState);
        window.addEventListener('pageshow', updateButtonState);
        updateButtonState();
    }

    function initializeDemo() {
        const region = document.getElementById('shuffle-demo');
        const list = document.getElementById('shuffle-track-list');
        const controls = document.getElementById('shuffle-demo-controls');
        const shuffleButton = document.getElementById('demo-shuffle');
        const undoButton = document.getElementById('demo-undo');
        const caption = document.getElementById('shuffle-demo-caption');
        const badge = document.getElementById('shuffle-demo-badge');
        const status = document.getElementById('shuffle-demo-status');
        if (!region || !list || !controls || !shuffleButton || !undoButton
            || !caption || !badge || !status) return;

        const rows = Array.from(list.querySelectorAll('[data-demo-track]'));
        if (rows.length !== 5) return;
        const original = rows.map(function (row) {
            const position = row.getAttribute('data-demo-original-position');
            const destination = row.getAttribute('data-demo-shuffled-position');
            return {
                row: row,
                position: /^\d+$/.test(position || '') ? Number(position) : NaN,
                destination: /^\d+$/.test(destination || '') ? Number(destination) : NaN,
                number: row.querySelector('.sh-landing-row-number'),
                origin: row.querySelector('.sh-landing-track-origin')
            };
        });
        if (original.some(function (track, index) {
            return !Number.isSafeInteger(track.position) || track.position < 1
                || track.position !== original[0].position + index
                || !Number.isInteger(track.destination) || track.destination < 1
                || track.destination > rows.length || !track.number || !track.origin;
        }) || new Set(original.map(function (track) {
            return track.destination;
        })).size !== rows.length) return;

        original.forEach(function (track) {
            track.originalNumber = track.number.textContent;
            track.originalOrigin = track.origin.textContent;
        });
        const reordered = original.slice().sort(function (left, right) {
            return left.destination - right.destination;
        });
        const originalCaption = caption.textContent;
        const originalBadge = badge.textContent;
        const originalStart = list.getAttribute('start');
        const range = original[0].position + '–' + original[original.length - 1].position;
        const motionPreference = typeof window.matchMedia === 'function'
            ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
        let shuffled = false;
        let busy = false;
        let run = 0;
        let animations = [];

        function updateControls() {
            list.setAttribute('aria-busy', String(busy));
            shuffleButton.setAttribute('aria-disabled', String(busy || shuffled));
            undoButton.setAttribute('aria-disabled', String(busy || !shuffled));
        }

        function complete(expectedRun) {
            if (expectedRun !== run || !busy) return;
            const completed = animations;
            animations = [];
            busy = false;
            // Cancelling removes any animation effect; the DOM already owns the final order.
            completed.forEach(function (animation) {
                try { animation.cancel(); } catch (_) { /* Older animation APIs may throw. */ }
            });
            updateControls();
            status.textContent = shuffled
                ? 'Sample playlist reordered. Positions 1–5. Undo is available.'
                : 'Original sample order restored. Positions ' + range + '.';
        }

        function reorder(nextState) {
            if (busy || shuffled === nextState) return;
            busy = true;
            const currentRun = ++run;
            updateControls();

            const shouldAnimate = !(motionPreference && motionPreference.matches)
                && typeof window.getComputedStyle === 'function'
                && original.every(function (track) {
                    return typeof track.row.animate === 'function';
                });
            const before = shouldAnimate ? original.map(function (track) {
                return track.row.getBoundingClientRect();
            }) : [];

            shuffled = nextState;
            const ordered = shuffled ? reordered : original;
            ordered.forEach(function (track, index) {
                list.appendChild(track.row);
                track.number.textContent = shuffled
                    ? String(index + 1).padStart(2, '0') : track.originalNumber;
                track.origin.textContent = shuffled
                    ? 'Previously #' + track.position : track.originalOrigin;
            });
            list.setAttribute('start', shuffled ? '1' : originalStart);
            region.setAttribute('data-demo-state', shuffled ? 'shuffled' : 'original');
            caption.textContent = shuffled ? 'A fresh order · positions 1–5' : originalCaption;
            badge.textContent = shuffled ? 'Reordered' : originalBadge;

            if (shouldAnimate) {
                try {
                    const computed = window.getComputedStyle(list);
                    const timing = computed.getPropertyValue('--sh-motion-reorder').trim();
                    const easing = computed.getPropertyValue('--sh-motion-ease').trim();
                    const match = timing.match(/^(\d+(?:\.\d+)?)(ms|s)$/);
                    const duration = match ? Number(match[1]) * (match[2] === 's' ? 1000 : 1) : 0;
                    if (duration > 0 && easing) {
                        original.forEach(function (track, index) {
                            const after = track.row.getBoundingClientRect();
                            const x = before[index].left - after.left;
                            const y = before[index].top - after.top;
                            if (!x && !y) return;
                            const animation = track.row.animate([
                                { transform: 'translate(' + x + 'px, ' + y + 'px)' },
                                { transform: 'translate(0, 0)' }
                            ], { duration: duration, easing: easing });
                            animations.push(animation);
                            // Attach both outcomes immediately, including partial animation failures.
                            animation.finished.then(function () {}, function () {});
                        });
                    }
                } catch (_) {
                    complete(currentRun);
                    return;
                }
            }
            if (!animations.length) {
                complete(currentRun);
                return;
            }
            Promise.allSettled(animations.map(function (animation) {
                return animation.finished;
            })).then(function () { complete(currentRun); });
        }

        shuffleButton.addEventListener('click', function () { reorder(true); });
        undoButton.addEventListener('click', function () { reorder(false); });
        if (motionPreference) {
            const updateMotion = function () {
                if (motionPreference.matches) complete(run);
            };
            if (typeof motionPreference.addEventListener === 'function') {
                motionPreference.addEventListener('change', updateMotion);
            } else if (typeof motionPreference.addListener === 'function') {
                motionPreference.addListener(updateMotion);
            }
        }
        updateControls();
        controls.hidden = false;
    }

    function initializeLanding() {
        initializeConsent();
        initializeDemo();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeLanding, { once: true });
    } else {
        initializeLanding();
    }
}());
