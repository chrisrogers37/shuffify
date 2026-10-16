import type {
  Operation,
  OperationRestoreRequest,
} from "../../api/contracts.ts";
import type { RestoreContext } from "./client.ts";

export type RestorationPhase =
  | "idle"
  | "loading"
  | "ready"
  | "submitting"
  | "unknown"
  | "observing"
  | "unavailable"
  | "attention"
  | "refused"
  | "resolved";
export interface RestorationAttempt {
  readonly key: string;
  readonly body: OperationRestoreRequest;
  readonly context: RestoreContext;
  readonly operationId: number | null;
  readonly operation: Operation | null;
}
export interface RestorationState {
  readonly actor: string;
  readonly parentId: number;
  readonly parent: Operation | null;
  readonly context: RestoreContext | null;
  readonly phase: RestorationPhase;
  readonly attempts: readonly RestorationAttempt[];
  readonly child: Operation | null;
  readonly relatedChildId: number | null;
  readonly error: string;
  readonly message: string;
  readonly retryUntil: number;
}
