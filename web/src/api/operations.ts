import type { ApiClient } from "./client.ts";
import type { Operation } from "./contracts.ts";
import { ApiRequestError, NetworkError, RateLimitError } from "./errors.ts";

export interface PollOptions {
  signal?: AbortSignal;
  intervalMs?: number;
  wait?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
  onUpdate?: (operation: Operation) => void;
  maxReadRetries?: number;
}
interface Subscriber {
  resolve: (operation: Operation) => void;
  reject: (error: unknown) => void;
  onUpdate?: (operation: Operation) => void;
  detach: () => void;
}
interface PollEntry {
  controller: AbortController;
  subscribers: Set<Subscriber>;
}
const polls = new WeakMap<ApiClient, Map<number, PollEntry>>();
const terminalStates = new Set([
  "succeeded",
  "failed",
  "rolled_back",
  "needs_attention",
]);
const abortError = (signal?: AbortSignal) =>
  signal?.reason ??
  new DOMException("Operation observation stopped.", "AbortError");

/** Abort observation and its timer; this never sends an operation cancellation or a write. */
function waitFor(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError(signal));
      return;
    }
    // Browsers clamp overflowing timeout values to 1ms. Chunk long server hints instead.
    const started = Date.now();
    let remaining = milliseconds;
    let timer: ReturnType<typeof setTimeout>;
    const cleanup = () => signal.removeEventListener("abort", aborted);
    const tick = () => {
      remaining = milliseconds - (Date.now() - started);
      if (remaining > 0)
        timer = setTimeout(tick, Math.min(remaining, 2_147_483_647));
      else {
        cleanup();
        resolve();
      }
    };
    const aborted = () => {
      clearTimeout(timer);
      cleanup();
      reject(abortError(signal));
    };
    signal.addEventListener("abort", aborted, { once: true });
    timer = setTimeout(tick, Math.min(remaining, 2_147_483_647));
  });
}

function isRetryable(error: unknown): boolean {
  return (
    error instanceof RateLimitError ||
    error instanceof NetworkError ||
    (error instanceof ApiRequestError &&
      error.status >= 500 &&
      error.error.retryable !== false)
  );
}

/** Share one sequential GET loop per client and operation ID, with independent observers. */
export function pollOperation(
  client: ApiClient,
  id: number,
  options: PollOptions = {},
): Promise<Operation> {
  if (!Number.isSafeInteger(id) || id <= 0)
    return Promise.reject(
      new TypeError("Operation IDs must be positive integers."),
    );
  if (options.signal?.aborted)
    return Promise.reject(abortError(options.signal));
  const interval = options.intervalMs ?? 1000;
  const maxRetries = options.maxReadRetries ?? 3;
  if (
    !Number.isFinite(interval) ||
    interval < 0 ||
    !Number.isSafeInteger(maxRetries) ||
    maxRetries < 0
  ) {
    return Promise.reject(
      new TypeError("Polling requires a nonnegative interval and retry count."),
    );
  }
  let byId = polls.get(client);
  if (!byId) {
    byId = new Map();
    polls.set(client, byId);
  }
  let entry = byId.get(id);
  const fresh = !entry;
  if (!entry) {
    entry = { controller: new AbortController(), subscribers: new Set() };
    byId.set(id, entry);
  }
  const shared = entry;
  const entries = byId;
  const observed = new Promise<Operation>((resolve, reject) => {
    const aborted = () => {
      shared.subscribers.delete(subscriber);
      subscriber.detach();
      reject(abortError(options.signal));
      // Effect replay may subscribe again in this turn. Keep its existing GET alive.
      queueMicrotask(() => {
        if (shared.subscribers.size === 0 && entries.get(id) === shared) {
          entries.delete(id);
          shared.controller.abort();
        }
      });
    };
    const subscriber: Subscriber = {
      resolve,
      reject,
      ...(options.onUpdate ? { onUpdate: options.onUpdate } : {}),
      detach: () => options.signal?.removeEventListener("abort", aborted),
    };
    shared.subscribers.add(subscriber);
    options.signal?.addEventListener("abort", aborted, { once: true });
  });
  if (fresh) {
    const wait = options.wait ?? waitFor;
    void (async () => {
      let failures = 0;
      try {
        while (!shared.controller.signal.aborted) {
          let operation: Operation;
          try {
            operation = await client.getOperation(id, {
              signal: shared.controller.signal,
            });
            failures = 0;
          } catch (error) {
            if (
              shared.controller.signal.aborted ||
              !isRetryable(error) ||
              failures >= maxRetries
            )
              throw error;
            const backoff = Math.min(
              Math.max(interval, 1000) * 2 ** failures,
              10_000,
            );
            const hint =
              error instanceof ApiRequestError ? error.retryAfterMs : null;
            failures += 1;
            await wait(Math.max(backoff, hint ?? 0), shared.controller.signal);
            continue;
          }
          for (const subscriber of shared.subscribers) {
            try {
              subscriber.onUpdate?.(operation);
            } catch (error) {
              subscriber.detach();
              shared.subscribers.delete(subscriber);
              subscriber.reject(error);
            }
          }
          if (terminalStates.has(operation.state)) {
            for (const subscriber of shared.subscribers) {
              subscriber.detach();
              subscriber.resolve(operation);
            }
            return;
          }
          if (shared.subscribers.size === 0) return;
          await wait(interval, shared.controller.signal);
        }
      } catch (error) {
        for (const subscriber of shared.subscribers) {
          subscriber.detach();
          subscriber.reject(error);
        }
      } finally {
        shared.subscribers.clear();
        if (entries.get(id) === shared) entries.delete(id);
      }
    })();
  }
  return observed;
}
