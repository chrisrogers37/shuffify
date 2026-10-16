import { useParams } from "react-router";
import { useServices, useSession } from "../auth/SessionProvider";
import { Button } from "../components/ui/controls";
import { WorkshopPage } from "../pages/workshop/WorkshopPage";
import { getWorkshopController } from "../pages/workshop/core/controller";
import type { WorkshopController } from "../pages/workshop/core/controller";
import type {
  SourceCoordinator,
  SourceStagingSnapshot,
} from "../pages/workshop/core/types";
import type {
  SourceRetainedWork,
  SourceTargetConfirmation,
} from "../pages/workshop/core/source-types";
import { getAdmissionRegistry } from "../operations/admissions";
import { useWorkshopWorkspace } from "../pages/workshop/workspace";
const peers = new WeakMap<WorkshopController, ReturnType<typeof createPeer>>();
/** Fixture peer verifies the public coordinator contract, with real HTTP transport. */
function createPeer(c: WorkshopController) {
  let staging: readonly SourceStagingSnapshot[] = [],
    retained: SourceRetainedWork[] = [];
  const callbacks = new Set<() => void>(),
    registry = getAdmissionRegistry(c.api, c.actor, c.operations);
  const notify = () => callbacks.forEach((fn) => fn());
  let handoff: SourceTargetConfirmation | null = null;
  const seen = new Set<number>();
  const update = (
    status: SourceRetainedWork["status"],
    operationId: number | null = null,
  ) => {
    retained = [
      {
        id: "source-fixture",
        kind: "raid_promotion",
        label: "Finish pending tracks",
        source: { id: "collection", name: "Holding room" },
        operationId,
        status,
      },
    ];
    notify();
  };
  c.operations.subscribe(() => {
    const id = retained[0]?.operationId;
    if (!id) return;
    const op = c.operations.getSnapshot().get(id)?.operation;
    if (!op) return;
    update(
      op.state === "succeeded" && op.result?.success === true
        ? "complete"
        : ["failed", "rolled_back", "needs_attention"].includes(op.state)
          ? "failed"
          : "accepted",
      id,
    );
  });
  async function run(retry = false) {
    if (!handoff || !c.permission("pause").allowed) return;
    const key = handoff.sourceReview.tasks[0]!.key;
    update("submitting");
    try {
      const reply = retry
        ? await registry.retry(key, c.actor)
        : await registry.submitContinuation(key, c.actor);
      if (reply.status !== "accepted") return;
      update("accepted", reply.operation.id);
      c.operations.accept(reply.operation);
    } catch {
      update(
        registry.getSnapshot().get(key)?.status === "unknown"
          ? "unknown"
          : "failed",
      );
    }
  }
  const coordinator: SourceCoordinator = {
    snapshot: () => staging,
    discard() {
      staging = [];
      notify();
    },
    subscribe(fn) {
      callbacks.add(fn);
      return () => callbacks.delete(fn);
    },
    retainedWork: () => retained,
    prepareReview(input) {
      return {
        tasks: input.staging.map((s, i) => {
          if (s.kind !== "raid_promotion")
            throw Error("Unsupported fixture staging");
          return {
            id: `source-fixture-${i}`,
            key: `source-fixture-key-${i}`,
            kind: "raid_promotion",
            label: "Finish pending tracks",
            source: input.sourceContext.raid,
            dependsOn: [],
            selectedCount: s.pending_rows.length,
            body: {
              source_playlist_id: s.source_playlist_id,
              target_track_uris: input.desired,
              track_ids: s.pending_rows.map((r) => r.id),
            },
          };
        }),
      };
    },
    targetConfirmed(proof) {
      if (seen.has(proof.operationId)) return;
      seen.add(proof.operationId);
      handoff = proof;
      staging = [];
      update("ready");
      queueMicrotask(() => {
        void run();
      });
    },
  };
  c.registerSourceCoordinator(coordinator);
  return {
    stage() {
      staging = [
        {
          kind: "raid_promotion",
          source_playlist_id: "collection",
          pending_rows: [
            { id: 11, track_uri: c.getSnapshot().working[0]!.uri },
          ],
        },
      ];
      notify();
    },
    retry: () => run(true),
  };
}
function ChildContextProof() {
  const value = useWorkshopWorkspace();
  return (
    <p data-testid="source-child-context">
      Fixture child shares target {value.controller.playlistId}.
    </p>
  );
}
export function WorkshopSourceHarness() {
  const { playlistId } = useParams(),
    services = useServices(),
    session = useSession();
  if (!playlistId || session.status !== "ready" || !session.session.signed_in)
    return null;
  const c = getWorkshopController(
    services.api,
    services.session,
    session.session.user.id,
    playlistId,
  );
  let peer = peers.get(c);
  if (!peer) {
    peer = createPeer(c);
    peers.set(c, peer);
  }
  return (
    <WorkshopPage
      controller={c}
      tools={[
        {
          id: "sources",
          title: "Source fixture",
          render: () => (
            <div className="sh-workshop-panel-stack">
              <Button onClick={() => peer!.stage()}>
                Stage pending fixture track
              </Button>
              <Button variant="secondary" onClick={() => void peer!.retry()}>
                Retry exact source fixture request
              </Button>
            </div>
          ),
        },
      ]}
    >
      <ChildContextProof />
    </WorkshopPage>
  );
}
