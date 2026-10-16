import { WorkshopSourceHarness } from "../fixtures/WorkshopSourceHarness";
import { useEffect, useRef, useState } from "react";
import { createBrowserRouter, Link, Outlet, useParams } from "react-router";
import type { Services } from "../auth/SessionProvider";
import { AuthGuard, useServices, useSession } from "../auth/SessionProvider";
import { Providers } from "./Providers";
import { publicRoutes } from "./public-routes";
import { settingsRoutes } from "./settings-routes";
import { activityRoutes } from "./activity-routes";
import { libraryRoutes } from "./library-routes";
import { WorkshopToolsHarness } from "../fixtures/WorkshopToolsHarness";
import { workshopToolsRoutes } from "./workshop-tools-routes";
import { routinesRoutes } from "./routines-routes";
import { AppShell, PublicShell } from "../shell/Shell";
import { ErrorPage } from "../pages/errors/ErrorPage";
import {
  Button,
  Field,
  Input,
  Select,
  Toggle,
} from "../components/ui/controls";
import { State } from "../components/ui/States";
import { Toast } from "../components/ui/Toast";
import { Artwork } from "../components/Artwork";
import { useTasks, Workspace } from "../components/tasks/TaskPanel";
import { useConfirm } from "../components/tasks/ConfirmDialog";
import { useNavigationBlocker } from "../navigation/NavigationGuard";
import { useAnnouncer } from "../a11y/Announcer";
import { pollOperation } from "../api/operations";
import { ConflictError, RateLimitError } from "../api/errors";
import type { Operation } from "../api/contracts";
import { TaskScrollHarness } from "../fixtures/TaskScrollHarness";
import { NavigationHandoff } from "../fixtures/NavigationHandoff";
function TaskContents({
  guard,
  delay,
  onRemoveOpener,
}: {
  guard: { current: boolean };
  delay: { current: boolean };
  onRemoveOpener: () => void;
}) {
  const [note, setNote] = useState("A note that stays with this task"),
    [guarded, setGuarded] = useState(guard.current),
    [delayed, setDelayed] = useState(delay.current);
  const tasks = useTasks();
  return (
    <div className="sh-foundation-stack">
      <Field id="task-note" label="Task note">
        {(props) => (
          <Input
            {...props}
            value={note}
            onChange={(event) => setNote(event.currentTarget.value)}
          />
        )}
      </Field>
      <Toggle
        label="Confirm before closing task"
        checked={guarded}
        onChange={(value) => {
          guard.current = value;
          setGuarded(value);
        }}
      />
      <Toggle
        label="Delay close approval"
        checked={delayed}
        onChange={(value) => {
          delay.current = value;
          setDelayed(value);
        }}
      />
      <Button variant="secondary" onClick={onRemoveOpener}>
        Remove task opener
      </Button>
      <Button
        variant="secondary"
        onClick={(event) => {
          void tasks.openTask(
            {
              id: "child",
              title: "Child task",
              trigger: event.currentTarget,
              fallback: document.querySelector("#main-content h1"),
              content: (
                <div className="sh-foundation-stack">
                  <p>The parent task and its note are retained.</p>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      void tasks.closeTask();
                    }}
                  >
                    Back
                  </Button>
                </div>
              ),
            },
            "push",
          );
        }}
      >
        Open child task
      </Button>
    </div>
  );
}
function FoundationReview() {
  const [dirty, setDirty] = useState(false),
    [name, setName] = useState("Evening collection"),
    [toast, setToast] = useState(false),
    [confirmation, setConfirmation] = useState(""),
    [operation, setOperation] = useState<Operation | null>(null),
    [error, setError] = useState(""),
    [busyId, setBusyId] = useState<number | null>(null),
    [queueing, setQueueing] = useState(false),
    [showOpener, setShowOpener] = useState(true),
    [closeDecisions, setCloseDecisions] = useState(0);
  const tasks = useTasks(),
    { confirm } = useConfirm(),
    { api } = useServices(),
    session = useSession(),
    announce = useAnnouncer();
  const fallback = useRef<HTMLHeadingElement>(null),
    guard = useRef(false),
    delay = useRef(false),
    poll = useRef<AbortController | null>(null),
    intent = useRef("foundation-fixture-intent");
  useNavigationBlocker(
    "foundation-draft",
    dirty,
    "Your unsaved changes will be lost.",
  );
  useEffect(() => () => poll.current?.abort(), []);
  useEffect(() => {
    if (operation)
      announce(
        `Operation ${operation.state.replaceAll("_", " ")}.`,
        `operation-${operation.id}-${operation.state}`,
      );
  }, [operation, announce]);
  const observe = async (id: number) => {
    poll.current?.abort();
    poll.current = new AbortController();
    try {
      await pollOperation(api, id, {
        signal: poll.current.signal,
        intervalMs: 150,
        onUpdate: setOperation,
      });
    } catch (problem) {
      if (!poll.current?.signal.aborted)
        setError(
          problem instanceof Error
            ? problem.message
            : "The operation could not be loaded.",
        );
    }
  };
  const queue = async () => {
    if (
      queueing ||
      (operation &&
        ["queued", "running", "compensating"].includes(operation.state))
    )
      return;
    setQueueing(true);
    setError("");
    setBusyId(null);
    try {
      const result = await api.request("/api/fixture/command", {
        method: "POST",
        headers: { "Idempotency-Key": intent.current },
        json: { fixture: true },
      });
      if (result.status === "accepted") {
        setOperation(result.operation);
        void observe(result.operation.id);
      }
    } catch (problem) {
      setError(
        problem instanceof RateLimitError
          ? `Try again after ${Math.ceil((problem.retryAfterMs ?? 1000) / 1000)} seconds.`
          : problem instanceof Error
            ? problem.message
            : "The request failed.",
      );
      if (problem instanceof ConflictError)
        setBusyId(problem.error.operation_id ?? null);
    } finally {
      setQueueing(false);
    }
  };
  const scenario =
    new URLSearchParams(location.search).get("scenario") ?? "happy";
  return (
    <div className="sh-container sh-foundation-page">
      <header className="sh-foundation-heading">
        <div>
          <p className="sh-eyebrow">Fixture build · shared components</p>
          <h1 className="sh-heading" tabIndex={-1} ref={fallback}>
            Foundation review
          </h1>
          <p className="sh-foundation-muted">
            After Hours shells, controls and task behavior.
          </p>
        </div>
        <Link className="sh-button-secondary" to="/__foundation/public">
          Public shell
        </Link>
      </header>
      <Workspace>
        <div className="sh-foundation-stack">
          <section className="sh-foundation-card">
            <div className="sh-foundation-section-head">
              <div>
                <p className="sh-eyebrow">Connected session</p>
                <h2>
                  {session.status === "ready" && session.session.signed_in
                    ? session.session.user.display_name
                    : "Spotify"}
                </h2>
              </div>
              <span className="sh-foundation-badge">Fixture data</span>
            </div>
            <p>
              These controls exercise shared controls, task panels, navigation
              and accessible announcements with fixture data.
            </p>
            <div className="sh-foundation-fields">
              <Field
                id="playlist-name"
                label="Collection name"
                help="A fixture field for testing native controls."
              >
                {(props) => (
                  <Input
                    {...props}
                    value={name}
                    onChange={(event) => setName(event.currentTarget.value)}
                  />
                )}
              </Field>
              <Field id="algorithm" label="Shuffle algorithm">
                {(props) => (
                  <Select {...props}>
                    <option>Basic shuffle</option>
                    <option>Newest first</option>
                  </Select>
                )}
              </Field>
            </div>
            <Toggle
              label="Protect unsaved changes"
              checked={dirty}
              onChange={setDirty}
            />
            <div className="sh-foundation-actions">
              <>
                {showOpener && (
                  <Button
                    onClick={(event) => {
                      guard.current = false;
                      delay.current = false;
                      void tasks.openTask({
                        id: "playlist",
                        title: "Playlist task",
                        trigger: event.currentTarget,
                        fallback: fallback.current,
                        content: (
                          <TaskContents
                            guard={guard}
                            delay={delay}
                            onRemoveOpener={() => setShowOpener(false)}
                          />
                        ),
                        beforeClose: async () => {
                          if (delay.current) {
                            setCloseDecisions((count) => count + 1);
                            await new Promise((resolve) =>
                              setTimeout(resolve, 300),
                            );
                          }
                          return (
                            !guard.current ||
                            confirm({
                              title: "Close this task?",
                              message:
                                "Keep the note by cancelling this close.",
                              confirmLabel: "Close task",
                            })
                          );
                        },
                      });
                    }}
                  >
                    Open task
                  </Button>
                )}
                <output aria-label="Close decisions">
                  Close decisions: {closeDecisions}
                </output>
              </>
              <Button
                variant="secondary"
                onClick={async () =>
                  setConfirmation(
                    (await confirm({
                      title: "Confirm action",
                      message: "Review the action before continuing.",
                      confirmLabel: "Confirm",
                    }))
                      ? "Confirmed."
                      : "Cancelled.",
                  )
                }
              >
                Confirm action
              </Button>
              <Button variant="secondary" onClick={() => setToast(true)}>
                Show toast
              </Button>
            </div>
            <p aria-live="off">{confirmation}</p>
          </section>
          <section className="sh-foundation-card sh-foundation-artwork-layout">
            <Artwork
              src="/images/favicon.svg"
              name="Fictional playlist fixture"
              spotifyUrl="https://open.spotify.com/playlist/0000000000000000000000"
            />
            <div>
              <p className="sh-eyebrow">Artwork component</p>
              <h2>Intact artwork, clear attribution</h2>
              <p>
                This local Shuffify illustration is a fixture. The shared
                component reserves space for Spotify attribution and the
                original link.
              </p>
              <p className="sh-foundation-muted">
                Four-pixel corners on narrow screens; eight pixels on wide
                screens.
              </p>
            </div>
          </section>
          {scenario === "empty" && (
            <State kind="empty" title="No playlists yet">
              Your playlists will appear here after you connect and load your
              library.
            </State>
          )}
          <section className="sh-foundation-card sh-foundation-stack">
            <h2>Queued operation lifecycle</h2>
            <p>
              Acceptance stays distinct from a verified result. Closing a task
              does not stop an accepted operation.
            </p>
            <div className="sh-foundation-actions">
              <Button
                busy={
                  queueing ||
                  (!!operation &&
                    ["queued", "running", "compensating"].includes(
                      operation.state,
                    ))
                }
                onClick={() => {
                  void queue();
                }}
              >
                Queue fixture operation
              </Button>
              {busyId && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    void observe(busyId);
                  }}
                >
                  Inspect operation
                </Button>
              )}
            </div>
            {error && (
              <State kind="error" title="The request needs attention">
                {error}
              </State>
            )}
            {operation && (
              <State
                kind={
                  operation.state === "succeeded"
                    ? "empty"
                    : operation.state === "needs_attention"
                      ? "partial"
                      : operation.state === "failed" ||
                          operation.state === "rolled_back"
                        ? "error"
                        : "loading"
                }
                title={`Operation ${operation.state.replaceAll("_", " ")}`}
              >
                {typeof operation.result?.message === "string"
                  ? operation.result.message
                  : `Operation ${operation.id} is ${operation.state}.`}
              </State>
            )}
            {operation?.recovery.available && (
              <Button
                variant="secondary"
                onClick={(event) => {
                  void tasks.openTask({
                    id: "recovery",
                    title: "Review recovery",
                    trigger: event.currentTarget,
                    fallback: fallback.current,
                    content: (
                      <p>
                        The playlist changed. Review its current contents before
                        restoring the saved order. No automatic write is sent.
                      </p>
                    ),
                  });
                }}
              >
                Review recovery
              </Button>
            )}
          </section>
          <form
            className="sh-foundation-card sh-foundation-stack"
            method="get"
            data-navigation
          >
            <Field id="scenario" label="Fixture scenario">
              {(props) => (
                <Select {...props} name="scenario" defaultValue={scenario}>
                  {[
                    "happy",
                    "signed-out",
                    "signed-out-notice",
                    "session-error",
                    "empty",
                    "error",
                    "partial",
                    "recovery",
                    "rollback",
                    "busy",
                    "rate-limit",
                  ].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Button variant="secondary" type="submit">
              Load scenario
            </Button>
          </form>
        </div>
      </Workspace>
      {toast && (
        <Toast
          id="foundation-toast"
          message="Fixture notification shown."
          onDismiss={() => setToast(false)}
        />
      )}
    </div>
  );
}
function PublicReview() {
  return (
    <div className="sh-container sh-foundation-page sh-foundation-stack">
      <p className="sh-eyebrow">Fixture build · public shell</p>
      <h1 tabIndex={-1} className="sh-heading">
        Public shell review
      </h1>
      <p>
        The public header and legal navigation are available at every width.
      </p>
      <div>
        <Link className="sh-button" to="/__foundation/app">
          Application shell
        </Link>
      </div>
    </div>
  );
}
function ErrorReview() {
  const { code } = useParams();
  return (
    <PublicShell>
      <ErrorPage code={code === "500" ? 500 : code === "503" ? 503 : 404} />
    </PublicShell>
  );
}
export function createFixtureRouter(services: Services) {
  return createBrowserRouter([
    {
      errorElement: (
        <PublicShell>
          <ErrorPage code={500} />
        </PublicShell>
      ),
      element: (
        <Providers services={services}>
          <Outlet />
        </Providers>
      ),
      children: [
        { path: "/__foundation/guard-handoff", element: <NavigationHandoff /> },
        ...publicRoutes(),
        ...settingsRoutes(),
        ...activityRoutes(),
        ...libraryRoutes(),
        ...routinesRoutes(),
        ...workshopToolsRoutes(),
        {
          path: "/__foundation/workshop-sources/:playlistId",
          element: <AppShell><AuthGuard><WorkshopSourceHarness /></AuthGuard></AppShell>,
        },
        {
          path: "/__foundation/workshop-tools/:playlistId",
          element: (
            <AppShell>
              <AuthGuard>
                <WorkshopToolsHarness />
              </AuthGuard>
            </AppShell>
          ),
        },
        {
          path: "/__foundation/task-scroll",
          element: (
            <AppShell>
              <AuthGuard>
                <TaskScrollHarness />
              </AuthGuard>
            </AppShell>
          ),
        },
        {
          path: "/__foundation/app",
          element: (
            <AppShell>
              <AuthGuard>
                <FoundationReview />
              </AuthGuard>
            </AppShell>
          ),
        },
        {
          path: "/__foundation/public",
          element: (
            <PublicShell>
              <PublicReview />
            </PublicShell>
          ),
        },
        { path: "/__foundation/errors/:code", element: <ErrorReview /> },
        {
          path: "*",
          element: (
            <PublicShell>
              <ErrorPage />
            </PublicShell>
          ),
        },
      ],
    },
  ]);
}
