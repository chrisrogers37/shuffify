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

    var uncertainAssets = [];
    function requestKey() {
        return global.crypto && global.crypto.randomUUID ? global.crypto.randomUUID()
            : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    }
    // One deliberate creation intent. Uncertain requests stay retained in page memory;
    // another press can read the same accepted operation, never send its POST again.
    async function asset(url, options, limits) {
        options = Object.assign({}, options); limits = limits || {};
        var prior = limits.newIntent ? null : uncertainAssets.slice().reverse().find(function (item) {
            return item.url === url && item.body === options.body;
        });
        var record = prior || {url: url, body: options.body, key: requestKey(), accepted: null};
        var unknown = function () { return {ok: false, answered: false, status: 0, retryAfter: null,
            data: {}, operationId: record.accepted ? record.accepted.operation_id : null, requestKey: record.key}; };
        if (prior && !prior.accepted) return unknown();
        var controller = new AbortController(), started = Date.now(), timeout = limits.timeoutMs || 300000;
        var external = options.signal, abort = function () { controller.abort(); };
        if (external) {
            external.addEventListener('abort', abort, {once: true});
            if (external.aborted) abort();
        }
        var timer = setTimeout(abort, timeout);
        options.signal = controller.signal;
        options.headers = Object.assign({'X-Requested-With': 'XMLHttpRequest'}, options.headers,
            {'Idempotency-Key': record.key});
        try {
            if (!prior) {
                uncertainAssets.push(record);
                var response = await global.fetch(url, options);
                var data = await response.json();
                if (!data || typeof data !== 'object' || Array.isArray(data)) return unknown();
                if (response.status !== 202) {
                    if (response.status >= 500) return unknown();
                    uncertainAssets = uncertainAssets.filter(function (item) { return item !== record; });
                    var header = response.headers && response.headers.get ? response.headers.get('Retry-After') : null;
                    var delay = [header, data.retry_after].map(function (value) { return Number.parseInt(value, 10); })
                        .find(function (value) { return Number.isFinite(value) && value > 0; }) || null;
                    return {ok: response.ok && data.success === true, answered: true, status: response.status,
                        data: data, retryAfter: delay};
                }
                if (!Number.isSafeInteger(data.operation_id) || data.operation_id < 1 || !data.operation ||
                        data.operation.id !== data.operation_id) return unknown();
                record.accepted = data;
            }
            var result = await poll(record.accepted, {signal: controller.signal,
                timeoutMs: Math.max(1, timeout - (Date.now() - started)), pollMs: limits.pollMs});
            var retained = !result.ok && ['created', 'unknown'].includes(result.data.outcome) ||
                limits.retainDismissed && ['complete', 'unknown'].includes(result.data.database_status);
            if (!retained) uncertainAssets = uncertainAssets.filter(function (item) { return item !== record; });
            return Object.assign(result, {answered: true, operationId: record.accepted.operation_id,
                requestKey: record.key, retryAfter: Number(result.data.retry_after) || null});
        } catch (_) {
            return unknown();
        } finally {
            clearTimeout(timer);
            if (external) external.removeEventListener('abort', abort);
        }
    }

    function dismiss(url, options, limits) {
        return asset(url, options, Object.assign({}, limits, {retainDismissed: true}));
    }
    global.OperationQueue = Object.freeze({poll: poll, asset: asset, dismiss: dismiss, requestKey: requestKey});
})(window);
