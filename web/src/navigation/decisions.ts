export interface NavigationDecision {
  message: string;
  decide?: () => Promise<boolean>;
}
/** One decision coordinator covers router, native links, and native forms. */
export async function decideNavigation(
  blockers: readonly NavigationDecision[],
  fallback: (message: string) => Promise<boolean>,
) {
  for (const blocker of blockers) {
    if (blocker.decide && !(await blocker.decide())) return false;
  }
  const messages = blockers.filter((b) => !b.decide).map((b) => b.message);
  return messages.length ? fallback(messages.join(" ")) : true;
}
