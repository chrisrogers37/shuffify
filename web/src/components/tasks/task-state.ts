export type CloseReason = "close" | "escape" | "replace" | "suspend";
export function createTransitionGate() {
  let pending = false;
  return {
    run: async (transition: () => boolean | Promise<boolean>) => {
      if (pending) return false;
      pending = true;
      try {
        return await transition();
      } finally {
        pending = false;
      }
    },
  };
}

/** Opt-in restoration only. Callers include the authenticated actor in the key. */
export function createTaskScrollCache() {
  const views = new Map<string, { scroll: number; pageScroll: number }>();
  return {
    read: (key?: string) => ({
      ...((key ? views.get(key) : undefined) ?? { scroll: 0, pageScroll: 0 }),
    }),
    save: (
      key: string | undefined,
      view: { scroll: number; pageScroll: number },
    ) => {
      if (key)
        views.set(key, {
          scroll: Math.max(0, view.scroll),
          pageScroll: Math.max(0, view.pageScroll),
        });
    },
  };
}
