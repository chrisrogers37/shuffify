import { isOperation } from "../api/client.ts";
import type { ApiClient } from "../api/client.ts";
import type { Operation } from "../api/contracts.ts";
import { pollOperation } from "../api/operations.ts";
import type { PollOptions } from "../api/operations.ts";
export interface ObservedOperation {
  id: number;
  operation: Operation | null;
  observation: "watching" | "settled" | "unavailable";
  error: Error | null;
}
export const terminalOperationStates = new Set([
  "succeeded",
  "failed",
  "rolled_back",
  "needs_attention",
]);
/** Observation belongs to the document, rather than a task or page subscription.
 * Removing a subscriber never cancels an accepted action or its read loop. */
export function createOperationStore(
  api: ApiClient,
  options: Pick<PollOptions, "intervalMs" | "wait" | "maxReadRetries"> = {},
) {
  let snapshot: ReadonlyMap<number, ObservedOperation> = new Map();
  const listeners = new Set<() => void>(),
    watching = new Map<number, AbortController>();
  function update(value: ObservedOperation) {
    snapshot = new Map(snapshot).set(value.id, value);
    for (const listener of listeners) listener();
  }
  function observe(id: number): void {
    if (!Number.isSafeInteger(id) || id <= 0)
      throw new TypeError("Operation IDs must be positive integers.");
    if (watching.has(id)) return;
    const current = snapshot.get(id);
    if (
      current?.operation &&
      terminalOperationStates.has(current.operation.state)
    )
      return;
    if (!current || current.observation !== "watching" || current.error)
      update({
        id,
        operation: current?.operation ?? null,
        observation: "watching",
        error: null,
      });
    const controller = new AbortController();
    watching.set(id, controller);
    void pollOperation(api, id, {
      ...options,
      signal: controller.signal,
      onUpdate: (operation) => {
        const latest = snapshot.get(id)?.operation;
        if (latest && terminalOperationStates.has(latest.state)) return;
        update({
          id,
          operation,
          observation: terminalOperationStates.has(operation.state)
            ? "settled"
            : "watching",
          error: null,
        });
      },
    })
      .catch((error) => {
        const latest = snapshot.get(id)?.operation;
        if (latest && terminalOperationStates.has(latest.state)) return;
        update({
          id,
          operation: latest ?? null,
          observation: "unavailable",
          error:
            error instanceof Error
              ? error
              : new Error("Operation status could not be loaded."),
        });
      })
      .finally(() => {
        if (watching.get(id) === controller) watching.delete(id);
      });
  }
  function accept(operation: Operation): void {
    if (!isOperation(operation))
      throw new TypeError("A validated accepted operation is required.");
    const current = snapshot.get(operation.id);
    if (
      current?.operation &&
      terminalOperationStates.has(current.operation.state)
    )
      return;
    if (terminalOperationStates.has(operation.state))
      watching.get(operation.id)?.abort();
    if (!current || terminalOperationStates.has(operation.state))
      update({
        id: operation.id,
        operation,
        observation: terminalOperationStates.has(operation.state)
          ? "settled"
          : "watching",
        error: null,
      });
    if (!terminalOperationStates.has(operation.state)) observe(operation.id);
  }
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    accept,
    observe,
  };
}
export type OperationStore = ReturnType<typeof createOperationStore>;
const stores = new WeakMap<ApiClient, Map<string, OperationStore>>();
/** Never share cached owned results between authenticated actors. */
export function getOperationStore(
  api: ApiClient,
  actorId: string,
): OperationStore {
  if (!actorId.trim())
    throw new TypeError("An authenticated actor identity is required.");
  let actors = stores.get(api);
  if (!actors) {
    actors = new Map();
    stores.set(api, actors);
  }
  let store = actors.get(actorId);
  if (!store) {
    store = createOperationStore(api);
    actors.set(actorId, store);
  }
  return store;
}
