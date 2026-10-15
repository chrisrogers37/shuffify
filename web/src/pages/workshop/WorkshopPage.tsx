import type { ReactNode } from "react";
import { useCallback, useEffect, useRef } from "react";
import { Link, useParams } from "react-router";
import { ApiRequestError } from "../../api/errors";
import { useServices, useSession } from "../../auth/SessionProvider";
import { useAnnouncer } from "../../a11y/Announcer";
import { Button } from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { useTasks, Workspace } from "../../components/tasks/TaskPanel";
import { useConfirm } from "../../components/tasks/ConfirmDialog";
import { useNavigationBlocker } from "../../navigation/NavigationGuard";
import { useClock } from "../library/hooks";
import { receiptNeedsAttention } from "./core/permissions";
import { getWorkshopController } from "./core/controller";
import type { WorkshopController } from "./core/controller";
import type { ReviewedAction, ToolId } from "./core/types";
import { useWorkshop, WorkshopWorkspaceContext } from "./workspace";
import type { WorkshopTool, WorkshopToolContext } from "./workspace";
import { TrackList } from "./TrackList";
import { ReviewPanel, ActorTask } from "./ReviewPanel";
import { NamedDrafts } from "./NamedDrafts";
import { PlaylistPicker } from "./PlaylistPicker";
import { useDraftDecision } from "./DraftDecision";
import "./workshop.css";
const noTools: readonly WorkshopTool[] = [];
export function WorkshopEntry({
  tools = noTools,
}: {
  tools?: readonly WorkshopTool[];
}) {
  const { playlistId } = useParams(),
    { api, session: store } = useServices(),
    session = useSession(),
    last = useRef<string | null>(null);
  if (session.status === "ready" && session.session.signed_in)
    last.current = session.session.user.id;
  return last.current && playlistId ? (
    <WorkshopPage
      key={`${last.current}:${playlistId}`}
      controller={getWorkshopController(api, store, last.current, playlistId)}
      tools={tools}
    />
  ) : null;
}
export function WorkshopHub() {
  const { api } = useServices(),
    s = useSession();
  return (
    <div className="sh-container sh-foundation-page">
      <h1 className="sh-heading">Workshop</h1>
      <p>
        Choose a playlist to arrange its draft and review changes before saving.
      </p>
      {s.status === "ready" && s.session.signed_in && (
        <PlaylistPicker api={api} actor={s.session.user.id} />
      )}
    </div>
  );
}
export function WorkshopPage({
  controller: c,
  tools = noTools,
  children,
}: {
  controller: WorkshopController;
  tools?: readonly WorkshopTool[];
  children?: ReactNode;
}) {
  const s = useWorkshop(c),
    now = useClock(s.retryUntil),
    tasks = useTasks(),
    { confirm } = useConfirm(),
    announce = useAnnouncer(),
    heading = useRef<HTMLHeadingElement>(null),
    reviewOpener = useRef<HTMLElement | null>(null),
    decision = useDraftDecision(),
    notice = useRef(""),
    events = useRef(0),
    session = useSession();
  const handlers = useRef<{
    review: () => Promise<void>;
    openReview: (r?: ReviewedAction) => void;
    ask: typeof decision.ask;
  }>({ review: async () => {}, openReview: () => {}, ask: decision.ask });
  const sameActor =
    session.status === "ready" &&
    session.session.signed_in &&
    session.session.user.id === c.actor;
  useEffect(() => {
    void c.load();
  }, [c]);
  useEffect(() => {
    if (!sameActor) decision.cancel();
  }, [sameActor]);
  function openReview(_review?: ReviewedAction) {
    void tasks.openTask({
      id: `workshop-review-${c.actor}-${c.playlistId}`,
      scrollKey: `${c.actor}:${c.playlistId}:review`,
      title: "Review save",
      trigger: reviewOpener.current ?? (document.activeElement as HTMLElement),
      fallback: heading.current,
      content: <ReviewPanel controller={c} />,
    });
  }
  async function review() {
    try {
      const r = await c.prepareReview();
      openReview(r);
    } catch (e) {
      announce(
        e instanceof Error ? e.message : "The draft could not be reviewed.",
        `workshop-review-error-${++events.current}`,
      );
      openReview();
    }
  }
  handlers.current = { review, openReview, ask: decision.ask };
  const resolveDraft = useCallback(
    async (action: string) => {
      if (!c.hasDraft()) return true;
      const choice = await handlers.current.ask(action);
      if (choice === "review") {
        await handlers.current.review();
        return false;
      }
      return choice === "discard" && c.discardLocal();
    },
    [c],
  );
  const navigateDecision = useCallback(async () => {
    const receipt = c.getSnapshot().receipt;
    const attention =
      receipt &&
      (["submitting", "accepted"].includes(receipt.status) ||
        receiptNeedsAttention(receipt));
    if (attention || c.hasRetainedSourceWork()) {
      const choice = await handlers.current.ask("leave this playlist", true);
      if (choice === "review") handlers.current.openReview();
      return choice === "discard";
    }
    return resolveDraft("leave this playlist");
  }, [c, resolveDraft]);
  const retained =
    c.hasDraft() ||
    c.hasRetainedSourceWork() ||
    (!!s.receipt &&
      (["submitting", "accepted"].includes(s.receipt.status) ||
        receiptNeedsAttention(s.receipt)));
  useNavigationBlocker(
    `workshop-${c.actor}-${c.playlistId}`,
    sameActor && retained,
    "This Workshop has a local draft or an operation needing review.",
    navigateDecision,
  );
  useEffect(() => {
    if (s.notice && notice.current !== s.notice) {
      notice.current = s.notice;
      announce(s.notice, `workshop-notice-${++events.current}`);
    }
  }, [s.notice, announce]);
  const toolContext: WorkshopToolContext = {
    controller: c,
    openReview,
    resolveDraft,
    openTool,
    fallback: heading.current,
  };
  function openTool(
    id: ToolId,
    trigger: HTMLElement,
    mode: "push" | "replace" = "replace",
  ) {
    const tool = tools.find((t) => t.id === id);
    if (!tool) return;
    void tasks.openTask(
      {
        id: `workshop-${c.actor}-${c.playlistId}-${id}`,
        scrollKey: `${c.actor}:${c.playlistId}:${id}`,
        title: tool.title,
        trigger,
        fallback: heading.current,
        content: (
          <ActorTask actor={c.actor}>
            <WorkshopWorkspaceContext value={toolContext}>
              {tool.render(toolContext)}
            </WorkshopWorkspaceContext>
          </ActorTask>
        ),
      },
      mode,
    );
  }
  function openTools(trigger: HTMLElement) {
    void tasks.openTask({
      id: `workshop-${c.actor}-${c.playlistId}-tools`,
      scrollKey: `${c.actor}:${c.playlistId}:tools`,
      title: "Workshop tools",
      trigger,
      fallback: heading.current,
      content: (
        <ActorTask actor={c.actor}>
          <div
            className="sh-workshop-panel-stack"
            aria-label="Choose a Workshop tool"
          >
            {tools.map((tool) => (
              <Button
                key={tool.id}
                variant="secondary"
                onClick={(e) => openTool(tool.id, e.currentTarget, "push")}
              >
                {tool.title}
              </Button>
            ))}
          </div>
        </ActorTask>
      ),
    });
  }
  const ctx = s.context,
    receipt = s.receipt,
    attention = receiptNeedsAttention(receipt),
    busy =
      receipt &&
      (receipt.status === "submitting" || receipt.status === "accepted");
  return (
    <WorkshopWorkspaceContext value={toolContext}>
      <div className="sh-workshop-page">
        <Workspace>
          <div className="sh-container sh-workshop-content">
            {s.phase === "loading" || s.phase === "idle" ? (
              <State heading="h1" kind="loading" title="Loading Workshop…" />
            ) : s.phase === "error" ? (
              <State
                heading="h1"
                kind="error"
                title={
                  s.error instanceof ApiRequestError && s.error.status === 404
                    ? "Playlist unavailable"
                    : "Workshop could not be loaded"
                }
                action={
                  <Button
                    disabled={now < s.retryUntil}
                    onClick={() => void c.load()}
                  >
                    Try again
                  </Button>
                }
              >
                {s.error?.message}
              </State>
            ) : (
              ctx && (
                <>
                  <header className="sh-workshop-header">
                    <div className="sh-workshop-eyebrow">Playlist Workshop</div>
                    <h1 className="sh-heading" ref={heading} tabIndex={-1}>
                      {ctx.playlist.name}
                    </h1>
                    <div className="sh-workshop-action-buttons">
                      <a
                        className="sh-button-secondary"
                        href={`https://open.spotify.com/playlist/${ctx.playlist.id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Open on Spotify
                      </a>
                      <Button
                        variant="secondary"
                        onClick={(e) =>
                          void tasks.openTask({
                            id: `workshop-picker-${c.actor}`,
                            scrollKey: `${c.actor}:workshop:picker`,
                            title: "Switch playlist",
                            trigger: e.currentTarget,
                            fallback: heading.current,
                            content: (
                              <ActorTask actor={c.actor}>
                                <PlaylistPicker
                                  close={() => void tasks.closeTask()}
                                  api={c.api}
                                  actor={c.actor}
                                  current={c.playlistId}
                                />
                              </ActorTask>
                            ),
                          })
                        }
                      >
                        Switch playlist
                      </Button>
                    </div>
                    <div className="sh-workshop-navigation-tools">
                      <nav
                        className="sh-workshop-neighbors"
                        aria-label="Playlist navigation"
                      >
                        {ctx.previous && (
                          <Link
                            className="sh-button-secondary"
                            to={`/workshop/${encodeURIComponent(ctx.previous.id)}`}
                          >
                            Previous: {ctx.previous.name}
                          </Link>
                        )}
                        {ctx.next && (
                          <Link
                            className="sh-button-secondary"
                            to={`/workshop/${encodeURIComponent(ctx.next.id)}`}
                          >
                            Next: {ctx.next.name}
                          </Link>
                        )}
                      </nav>
                      {tools.length > 0 && (
                        <Button
                          variant="secondary"
                          onClick={(e) => openTools(e.currentTarget)}
                        >
                          Tools
                        </Button>
                      )}
                    </div>
                    <div className="sh-workshop-action-buttons">
                      <Button
                        variant="secondary"
                        onClick={(e) =>
                          void tasks.openTask({
                            id: `workshop-drafts-${c.actor}-${c.playlistId}`,
                            scrollKey: `${c.actor}:${c.playlistId}:drafts`,
                            title: "Named drafts",
                            trigger: e.currentTarget,
                            fallback: heading.current,
                            content: <NamedDrafts controller={c} />,
                          })
                        }
                      >
                        Named drafts
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={(e) => {
                          reviewOpener.current = e.currentTarget;
                          openReview();
                        }}
                      >
                        Compare Spotify
                      </Button>
                    </div>
                  </header>
                  {!c.permission("review").allowed && !busy && !attention && (
                    <p role="status" className="sh-workshop-notice">
                      {c.permission("review").reason}
                    </p>
                  )}
                  {s.notice && (
                    <p className="sh-workshop-notice" role="status">
                      {s.notice}
                    </p>
                  )}
                  <TrackList
                    controller={c}
                    renderActions={(row) =>
                      tools.map((tool) =>
                        tool.rowActions ? (
                          <div key={tool.id}>
                            {tool.rowActions(toolContext, row)}
                          </div>
                        ) : null,
                      )
                    }
                  />
                  <footer className="sh-workshop-draft-footer">
                    <p tabIndex={-1}>
                      <strong>
                        {attention
                          ? "Attention"
                          : busy
                            ? "Busy"
                            : c.hasDraft()
                              ? "Draft"
                              : "Ready"}
                      </strong>
                      <span>
                        {c.hasDraft()
                          ? " · Local changes waiting for review"
                          : " · Confirmed baseline"}
                      </span>
                    </p>
                    <div className="sh-workshop-action-buttons">
                      {receipt && (
                        <Button
                          variant="secondary"
                          onClick={(e) => {
                            reviewOpener.current = e.currentTarget;
                            openReview();
                          }}
                        >
                          Review save results
                        </Button>
                      )}
                      <Button
                        variant="secondary"
                        disabled={
                          !c.hasDraft() ||
                          (!!busy && receipt?.review.kind !== "privacy")
                        }
                        onClick={async () => {
                          if (
                            await confirm({
                              title: "Discard local draft changes?",
                              message:
                                "Restore the confirmed baseline and return uncommitted source staging. Retained saved or uncertain results stay available.",
                              confirmLabel: "Discard draft",
                              danger: true,
                            })
                          )
                            c.discardLocal();
                        }}
                        aria-label="Discard draft changes"
                      >
                        Discard draft
                      </Button>
                      <Button
                        busy={s.reviewing}
                        disabled={
                          !c.hasDraft() || !c.permission("review").allowed
                        }
                        onClick={(e) => {
                          reviewOpener.current = e.currentTarget;
                          void review();
                        }}
                      >
                        Review &amp; save
                      </Button>
                    </div>
                  </footer>
                </>
              )
            )}
          </div>
        </Workspace>
        {decision.element}
        {children}
      </div>
    </WorkshopWorkspaceContext>
  );
}
