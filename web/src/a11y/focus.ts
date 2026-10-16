export function restoreFocus(
  trigger: HTMLElement | null,
  fallback: HTMLElement | null,
) {
  requestAnimationFrame(() => {
    const usable = (node: HTMLElement | null) =>
      node?.isConnected &&
      !node.closest("[inert], [hidden]") &&
      !node.matches(":disabled");
    const target = usable(trigger)
      ? trigger
      : usable(fallback)
        ? fallback
        : null;
    target?.focus({ preventScroll: true });
  });
}
