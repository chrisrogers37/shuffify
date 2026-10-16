import { useEffect, useState, useSyncExternalStore } from "react";
import type { KeyboardEvent } from "react";
import type { LibraryController } from "./controller";
export function useLibrary(c: LibraryController) {
  return useSyncExternalStore(c.subscribe, c.getSnapshot, c.getSnapshot);
}
export function useClock(until = 0) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (until <= Date.now()) return;
    const timer = setInterval(() => {
      const value = Date.now();
      setNow(value);
      if (value >= until) clearInterval(timer);
    }, 250);
    return () => clearInterval(timer);
  }, [until]);
  return now;
}
export function escapeDisclosure(event: KeyboardEvent<HTMLDetailsElement>) {
  if (
    event.key === "Escape" &&
    event.target instanceof HTMLElement &&
    event.target.tagName !== "SELECT"
  ) {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.open = false;
    event.currentTarget.querySelector("summary")?.focus();
  }
}
