/** Await an owned queued operation without repeating its admitted write. */
(function (global) {
    'use strict';
    var terminal = ['succeeded', 'failed', 'rolled_back', 'needs_attention'];

    async function poll(accepted, options) {
        options = options || {};
        var id = accepted && accepted.operation_id;
        if (!Number.isSafeInteger(id) || id < 1 || !accepted.operation || accepted.operation.id !== id) {
            throw new Error('Invalid accepted operation');
        }
        var controller = new AbortController();
        var signal = options.signal;
        var abort = function () { controller.abort(); };
        if (signal) {
            signal.addEventListener('abort', abort, {once: true});
            if (signal.aborted) abort();
        }
        var timer = setTimeout(abort, options.timeoutMs || 300000);
        try {
            var operation = accepted.operation;
            while (!terminal.includes(operation.state)) {
                if (controller.signal.aborted) throw new Error('Operation status is unavailable');
                await new Promise(function (resolve, reject) {
                    var abortWait = function () { clearTimeout(pause); reject(new Error('Operation status is unavailable')); };
                    var pause = setTimeout(function () {
                        controller.signal.removeEventListener('abort', abortWait);
                        resolve();
                    }, options.pollMs === undefined ? 500 : options.pollMs);
                    controller.signal.addEventListener('abort', abortWait, {once: true});
                    if (controller.signal.aborted) abortWait();
                });
                var response = await global.fetch('/api/operations/' + id, {
                    signal: controller.signal, cache: 'no-store', headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                var body = await response.json();
                if (!response.ok || !body || !body.success || !body.operation || body.operation.id !== id) {
                    throw new Error('Operation status is unavailable');
                }
                operation = body.operation;
                if (!['queued', 'running', 'compensating'].concat(terminal).includes(operation.state)) {
                    throw new Error('Invalid operation status');
                }
            }
            if (!operation.result || typeof operation.result !== 'object' || Array.isArray(operation.result)) {
                throw new Error('Operation result is unavailable');
            }
            return {data: operation.result, ok: operation.state === 'succeeded',
                status: operation.result.http_status || (operation.state === 'succeeded' ? 200 : 502)};
        } catch (error) {
            error.operationId = id;
            throw error;
        } finally {
            clearTimeout(timer);
            if (signal) signal.removeEventListener('abort', abort);
        }
    }

    global.OperationQueue = Object.freeze({poll: poll, requestKey: function () {
        return global.crypto && global.crypto.randomUUID ? global.crypto.randomUUID()
            : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    }});
})(window);
