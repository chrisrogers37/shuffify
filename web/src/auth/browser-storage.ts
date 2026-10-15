/** Actor-bound routine choices retained for a same-tab setup handoff. */
export const ROUTINE_CONTINUATION_KEY = "shuffify:react-routine-continuation:v1";

export function clearRoutineContinuation() {
  try {
    window.sessionStorage.removeItem(ROUTINE_CONTINUATION_KEY);
  } catch {
    // Optional browser storage must not prevent native signout.
  }
}
