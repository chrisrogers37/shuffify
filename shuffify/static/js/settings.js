/* Progressively enhance the native settings POST; no script is required to save. */
(function () {
    'use strict';

    const form = document.getElementById('settings-form');
    if (!form || !window.fetch || !window.FormData) return;

    const button = form.querySelector('button[type="submit"]');
    const fields = document.getElementById('settings-fields');
    const errorRegion = document.getElementById('settings-error');
    const limit = document.getElementById('max_snapshots_per_playlist');
    const limitError = document.getElementById('max_snapshots_per_playlist-error');
    const unconfirmedMessage = 'Could not confirm your settings were saved. Check your connection and try again.';
    let saving = false;

    function updateLimitError() {
        const invalid = !limit.validity.valid;
        limit.setAttribute('aria-invalid', String(invalid));
        limitError.textContent = invalid ? 'Choose a whole number from 1 to 50.' : '';
        limitError.hidden = !invalid;
    }

    limit.addEventListener('invalid', updateLimitError);
    limit.addEventListener('input', updateLimitError);

    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (saving) return;
        if (!form.reportValidity()) return;

        const body = new FormData(form);
        const originalText = button.textContent;
        // Focus stays where the save began: Save is marked busy (aria-disabled), never disabled,
        // and a field that disabling the fields takes focus from gets it back afterwards.
        const held = document.activeElement;
        saving = true;
        fields.disabled = true;
        button.setAttribute('aria-disabled', 'true');
        button.textContent = 'Saving…';
        form.setAttribute('aria-busy', 'true');
        errorRegion.hidden = true;
        errorRegion.textContent = '';

        try {
            const response = await fetch(form.dataset.apiUrl, {
                method: 'POST',
                body: body,
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            });
            let data;
            try {
                data = await response.json();
                if (!data || typeof data !== 'object') throw new Error('Invalid response');
            } catch (_) {
                throw new Error(unconfirmedMessage);
            }
            if (!response.ok || !data.success) {
                throw new Error(data.message || 'Could not save your settings. Please try again.');
            }
            window.showNotification(data.message || 'Settings saved.', 'success');
        } catch (error) {
            errorRegion.textContent = error.message || unconfirmedMessage;
            errorRegion.hidden = false;
            errorRegion.focus();
        } finally {
            saving = false;
            fields.disabled = false;
            button.removeAttribute('aria-disabled');
            button.textContent = originalText;
            form.removeAttribute('aria-busy');
            if (held && held !== document.body && document.activeElement === document.body) held.focus();
        }
    });
})();
