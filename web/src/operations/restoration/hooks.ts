import { useSyncExternalStore } from "react";
import type { RestorationController } from "./controller";
export function useRestoration(controller: RestorationController) {
  return useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
}
