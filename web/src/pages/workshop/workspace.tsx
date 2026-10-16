import { createContext, useContext, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import type { WorkshopController } from "./core/controller";
import type { Occurrence, ReviewedAction, ToolId } from "./core/types";
export interface WorkshopToolContext {
  controller: WorkshopController;
  openReview: (review: ReviewedAction) => void;
  resolveDraft: (action: string) => Promise<boolean>;
  openTool: (
    id: ToolId,
    trigger: HTMLElement,
    mode?: "push" | "replace",
  ) => void;
  fallback: HTMLElement | null;
}
export interface WorkshopTool {
  id: ToolId;
  title: string;
  render: (context: WorkshopToolContext) => ReactNode;
  rowActions?: (
    context: WorkshopToolContext,
    occurrence: Occurrence,
  ) => ReactNode;
}
export const WorkshopWorkspaceContext =
  createContext<WorkshopToolContext | null>(null);
export function useWorkshopWorkspace() {
  const value = useContext(WorkshopWorkspaceContext);
  if (!value) throw Error("Workshop workspace is required.");
  return value;
}
export const useWorkshop = (c: WorkshopController) =>
  useSyncExternalStore(c.subscribe, c.getSnapshot, c.getSnapshot);
