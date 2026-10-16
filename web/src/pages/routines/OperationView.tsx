import type { JsonRecord, Operation } from "../../api/contracts";
import { ExecutionDetails } from "../../components/results/ExecutionDetails";
import { safeAvailableDetail } from "./model";
export function OperationView({ operation }: { operation: Operation }) {
  const result = operation.result,
    detail = detailOf(result, operation.id);
  return (
    <section
      className="sh-routine-feedback"
      aria-label={`Operation ${operation.id}`}
    >
      <h3>
        Operation #{operation.id} · {operation.state.replaceAll("_", " ")}
      </h3>
      <p>
        {typeof result?.message === "string"
          ? result.message
          : operation.state === "queued"
            ? "Waiting for a worker."
            : "The operation is being observed."}
      </p>
      {operation.state === "needs_attention" && (
        <p>
          Review the current playlist before another run. No automatic retry is
          sent.
        </p>
      )}
      {detail && <ExecutionDetails detail={detail} />}
    </section>
  );
}
export function detailOf(result: JsonRecord | null, id: number) {
  return safeAvailableDetail(result?.result_detail, id);
}
