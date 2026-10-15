import type { SessionContext } from "../api/contracts.ts";
export type SessionState =
  | { status: "pending" }
  | { status: "ready"; session: SessionContext }
  | { status: "error"; error: Error };
export function createSessionStore(
  fetchSession: () => Promise<SessionContext>,
) {
  let state: SessionState = { status: "pending" };
  let pending: Promise<void> | undefined;
  const listeners = new Set<() => void>();
  const update = (value: SessionState) => {
    state = value;
    listeners.forEach((listener) => listener());
  };
  const load = () => {
    if (pending) return pending;
    if (state.status === "ready") return Promise.resolve();
    pending = fetchSession()
      .then(
        (session) => update({ status: "ready", session }),
        (error) =>
          update({
            status: "error",
            error:
              error instanceof Error ? error : new Error("Session unavailable"),
          }),
      )
      .finally(() => {
        pending = undefined;
      });
    return pending;
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    load,
    refresh: () => {
      update({ status: "pending" });
      return load();
    },
    consumeNotice: () => {
      if (state.status === "ready" && state.session.notice)
        update({
          status: "ready",
          session: { ...state.session, notice: null },
        });
    },
  };
}
export type SessionStore = ReturnType<typeof createSessionStore>;
