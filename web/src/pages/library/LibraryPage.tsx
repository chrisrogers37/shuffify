import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useServices, useSession } from "../../auth/SessionProvider";
import { useAnnouncer } from "../../a11y/Announcer";
import { Button } from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { useConfirm } from "../../components/tasks/ConfirmDialog";
import { useTasks, Workspace } from "../../components/tasks/TaskPanel";
import { useNavigationBlocker } from "../../navigation/NavigationGuard";
import { getLibraryController } from "./controller";
import type { LibraryController } from "./controller";
import { findRow, greeting, rowSection, sectionNames } from "./model";
import { useLibrary, useClock } from "./hooks";
import { ReviewPanel } from "./ReviewPanel";
import { PlaylistRow, OrderList } from "./PlaylistRow";
import "./library.css";
export function LibraryEntry() {
  const session = useSession(),
    { api, session: store } = useServices(),
    last = useRef<string | null>(null);
  if (session.status === "ready" && session.session.signed_in)
    last.current = session.session.user.id;
  return last.current ? (
    <LibraryPage
      key={last.current}
      controller={getLibraryController(api, store, last.current)}
    />
  ) : null;
}
export function LibraryPage({
  controller: c,
}: {
  controller: LibraryController;
}) {
  const s = useLibrary(c),
    announce = useAnnouncer(),
    { confirm } = useConfirm(),
    tasks = useTasks(),
    now = useClock(Math.max(s.retryUntil, s.preferenceRetryUntil)),
    [organizing, setOrganizing] = useState(false),
    [showHidden, setShowHidden] = useState(false),
    [localError, setLocalError] = useState(""),
    heading = useRef<HTMLHeadingElement>(null),
    hiddenToggle = useRef<HTMLButtonElement>(null),
    lastNotice = useRef(""),
    seenStates = useRef(new Set<string>()),
    event = useRef(0);
  useEffect(() => {
    void c.load();
  }, [c]);
  const dirty = [...s.drafts.values()].some((d) => d.revision > 0),
    unknown = [...s.receipts.values()].some((r) => r.status === "unknown");
  useNavigationBlocker(
    "library-retained",
    dirty || s.orderDirty || s.preferenceUnknown || unknown,
    "Your edited shuffle options or unconfirmed Library changes are retained only in this document. Leave this page?",
  );
  useEffect(() => {
    if (s.notice && s.notice !== lastNotice.current) {
      lastNotice.current = s.notice;
      if (s.notice !== "Order saved.")
        announce(s.notice, `library-notice-${++event.current}`);
    }
  }, [s.notice, announce]);
  useEffect(
    () =>
      c.operations.subscribe(() => {
        for (const entry of c.operations.getSnapshot().values()) {
          const op = entry.operation;
          if (!op) continue;
          const key = `${op.id}-${op.state}`;
          if (!seenStates.current.has(key)) {
            seenStates.current.add(key);
            announce(
              `Operation ${op.id}: ${op.state.replaceAll("_", " ")}.`,
              `library-operation-${key}`,
            );
          }
        }
      }),
    [c, announce],
  );
  function openReceipt(id: number, trigger: HTMLElement) {
    void tasks.openTask({
      id: `library-${c.actor}-${id}`,
      title: "Playlist action",
      trigger,
      fallback: heading.current,
      content: <ReviewPanel controller={c} receiptId={id} />,
      beforeClose: async () => {
        c.closeReview(id);
        return true;
      },
    });
  }
  function begin(
    id: string,
    kind: "shuffle" | "undo" | "privacy",
    trigger: HTMLElement,
  ) {
    setLocalError("");
    void c.begin(id, kind).then(
      (rid) => openReceipt(rid, trigger),
      (e) =>
        setLocalError(
          e instanceof Error ? e.message : "The action could not be reviewed.",
        ),
    );
  }
  async function toggle(
    id: string,
    flag: "pinned" | "hidden",
    trigger: HTMLElement,
  ) {
    const section = s.context && rowSection(s.context.sections, id),
      rows = section ? s.context!.sections[section] : [],
      index = rows.findIndex((row) => row.id === id),
      fallbackId = rows[index + 1]?.id ?? rows[index - 1]?.id ?? null;
    const wasFocused = trigger.contains(document.activeElement);
    const succeeded = await c.toggle(id, flag);
    if (
      succeeded &&
      wasFocused &&
      (document.activeElement === trigger ||
        document.activeElement === document.body)
    ) {
      requestAnimationFrame(() => {
        if (
          document.activeElement !== trigger &&
          document.activeElement !== document.body
        )
          return;
        const node = document.querySelector<HTMLElement>(
            `[data-playlist-id="${CSS.escape(id)}"]`,
          ),
          fallback = fallbackId
            ? document.querySelector<HTMLElement>(
                `[data-playlist-id="${CSS.escape(fallbackId)}"]`,
              )
            : null;
        const target = node?.getClientRects().length
          ? node
          : fallback?.getClientRects().length
            ? fallback
            : null;
        (
          target?.querySelector<HTMLElement>(`[data-control="${flag}"]`) ??
          hiddenToggle.current ??
          heading.current
        )?.focus({ preventScroll: true });
      });
    }
  }
  function move(id: string, n: number, trigger: HTMLElement) {
    if (c.move(id, n)) {
      const row = findRow(c.getSnapshot().context!.sections, id),
        section = rowSection(c.getSnapshot().context!.sections, id)!;
      announce(
        `${row?.name} moved to position ${c.getSnapshot().context!.sections[section].findIndex((r) => r.id === id) + 1}.`,
        `library-move-${++event.current}`,
      );
      requestAnimationFrame(
        () => trigger.isConnected && trigger.focus({ preventScroll: true }),
      );
    } else if (!c.getSnapshot().preferenceUnknown)
      announce(c.getSnapshot().notice, `library-move-${++event.current}`);
  }
  const context = s.context,
    seconds = Math.max(0, Math.ceil((s.retryUntil - now) / 1000)),
    welcome = context ? greeting(context.greeting) : null,
    receipts = [...s.receipts.values()].filter(
      (r) => !["cancelled"].includes(r.status),
    );
  return (
    <div className="sh-library-page">
      <Workspace>
        <div className="sh-container">
          <header className="sh-library-header">
            <div className="sh-library-title-row">
              <h1 className="sh-heading" tabIndex={-1} ref={heading}>
                Your playlists
              </h1>
              <div className="sh-library-actions">
                <Button
                  variant="secondary"
                  busy={s.loading}
                  aria-disabled={seconds > 0 || s.preferenceBusy}
                  onClick={() => void c.load(true)}
                >
                  Refresh
                </Button>
                <Button
                  variant="secondary"
                  aria-pressed={organizing}
                  onClick={() => {
                    setOrganizing(!organizing);
                    if (organizing) setShowHidden(false);
                  }}
                >
                  {organizing ? "Done organizing" : "Organize"}
                </Button>
                {receipts.length > 0 && (
                  <Button
                    variant="secondary"
                    onClick={(e) =>
                      openReceipt(receipts.at(-1)!.id, e.currentTarget)
                    }
                  >
                    View action results
                  </Button>
                )}
              </div>
            </div>
            {welcome && (
              <p className="sh-library-intro sh-text-muted">
                <span className="sh-library-greeting">{welcome.welcome}</span>{" "}
                <span>{welcome.detail}</span>
                {welcome.onboarding && (
                  <>
                    {" "}
                    Set up{" "}
                    <Link className="sh-library-link" to="/routines">
                      routines
                    </Link>{" "}
                    to keep your playlists fresh automatically.
                  </>
                )}
              </p>
            )}
          </header>
          {organizing && (
            <section className="sh-library-organize-note">
              <p>
                Changes save immediately in Shuffify. Organizing never changes
                your Spotify playlists. Drag within a section or use the
                keyboard controls.
              </p>
              <Button
                variant="secondary"
                busy={s.preferenceBusy}
                aria-disabled={
                  s.loading ||
                  s.preferenceUnknown ||
                  now < s.preferenceRetryUntil
                }
                onClick={() => {
                  void confirm({
                    title: "Reset Library preferences?",
                    message:
                      "Remove all favorites, hidden playlists and custom order. Your Spotify playlists stay the same.",
                    confirmLabel: "Reset preferences",
                  }).then((yes) => {
                    if (yes) void c.reset();
                  });
                }}
              >
                Reset preferences
              </Button>
            </section>
          )}
          {s.notice && (
            <p className="sh-caption sh-text-muted" role="status">
              {s.notice}
            </p>
          )}
          {(localError || s.preferenceError) && (
            <div className="sh-library-alert" role="alert">
              <p>{localError || s.preferenceError}</p>
              {now < s.preferenceRetryUntil && (
                <p>
                  Wait {Math.ceil((s.preferenceRetryUntil - now) / 1000)}{" "}
                  seconds before saving preferences again.
                </p>
              )}
              {s.preferenceUnknown ? (
                <p>
                  This preference may have saved. Refresh the Library to confirm
                  it before organizing again.
                </p>
              ) : (
                s.orderDirty && (
                  <Button
                    variant="secondary"
                    busy={s.preferenceBusy}
                    aria-disabled={now < s.preferenceRetryUntil}
                    onClick={() => void c.retryOrder()}
                  >
                    Retry order save
                  </Button>
                )
              )}
            </div>
          )}
          {c.blocked() && (
            <p className="sh-library-alert">
              New Spotify changes are paused while a retained request or result
              needs reconciliation. Open View action results.
            </p>
          )}
          {s.loading && !context ? (
            <State kind="loading" title="Loading your playlists…" />
          ) : null}
          {s.error && (
            <div role="alert">
              <State
                kind="error"
                title="Library could not be loaded"
                action={
                  <Button
                    busy={s.loading}
                    aria-disabled={seconds > 0}
                    onClick={() => void c.load(true)}
                  >
                    Try again
                  </Button>
                }
              >
                <p>{s.error}</p>
                {seconds > 0 && <p>Try again in {seconds} seconds.</p>}
              </State>
            </div>
          )}
          {context && (
            <div className="sh-library-sections">
              {(["favorites", "playlists", "hidden"] as const).map(
                (section) => {
                  const rows = context.sections[section];
                  if (section !== "playlists" && !rows.length) return null;
                  return (
                    <section
                      className="sh-library-section"
                      key={section}
                      aria-labelledby={`library-${section}-heading`}
                    >
                      {section === "hidden" ? (
                        <h2 id={`library-${section}-heading`}>
                          <Button
                            variant="secondary"
                            ref={hiddenToggle}
                            aria-expanded={showHidden}
                            aria-controls="library-hidden-content"
                            onClick={() => setShowHidden(!showHidden)}
                          >
                            Hidden{" "}
                            <span className="sh-badge">{rows.length}</span>
                          </Button>
                        </h2>
                      ) : (
                        <h2 id={`library-${section}-heading`}>
                          {sectionNames[section]}{" "}
                          <span className="sh-badge">{rows.length}</span>
                        </h2>
                      )}
                      <div
                        id={
                          section === "hidden"
                            ? "library-hidden-content"
                            : undefined
                        }
                        hidden={section === "hidden" && !showHidden}
                      >
                        {rows.length ? (
                          <OrderList
                            controller={c}
                            section={section}
                            organizing={organizing}
                          >
                            {rows.map((row, index) => (
                              <PlaylistRow
                                key={row.id}
                                controller={c}
                                row={row}
                                section={section}
                                position={index + 1}
                                total={rows.length}
                                organizing={organizing}
                                begin={begin}
                                toggle={(...args) => void toggle(...args)}
                                move={move}
                              />
                            ))}
                          </OrderList>
                        ) : (
                          <State
                            kind="empty"
                            title={
                              context.sections.favorites.length ||
                              context.sections.hidden.length
                                ? "Your other playlists appear here"
                                : "No playlists yet"
                            }
                          >
                            <p>
                              {context.sections.favorites.length ||
                              context.sections.hidden.length
                                ? "Use Organize to show hidden playlists or change your favorites."
                                : "Create a playlist in Spotify, then refresh your Library."}
                            </p>
                          </State>
                        )}
                      </div>
                    </section>
                  );
                },
              )}
            </div>
          )}
          {receipts.length > 1 && (
            <section
              className="sh-library-receipts"
              aria-label="Retained playlist actions"
            >
              <h2>Recent actions in this document</h2>
              <ul>
                {receipts.map((r) => (
                  <li key={r.id}>
                    <Button
                      variant="secondary"
                      onClick={(e) => openReceipt(r.id, e.currentTarget)}
                    >
                      {r.name} · {r.intent.kind} ·{" "}
                      {r.operationId
                        ? `Operation #${r.operationId}`
                        : r.status.replaceAll("_", " ")}
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </Workspace>
    </div>
  );
}
