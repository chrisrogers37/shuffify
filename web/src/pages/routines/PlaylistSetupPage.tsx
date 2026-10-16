import {
  getAdmissionRegistry,
  spotifyResources,
  admissionUnknown,
} from "../../operations/admissions";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Link, useLocation, useParams } from "react-router";
import { ApiRequestError } from "../../api/errors";
import { useServices, useSession } from "../../auth/SessionProvider";
import { useAnnouncer } from "../../a11y/Announcer";
import {
  Button,
  Field,
  Input,
  Select,
  Toggle,
} from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { useConfirm } from "../../components/tasks/ConfirmDialog";
import { useNavigationBlocker } from "../../navigation/NavigationGuard";
import { getOperationStore } from "../../operations/store";
import { unconfirmedMutation } from "./model";
import { createRoutineClient } from "./client";
import {
  parseSetup,
  playlistPath,
  readContinuation,
  record,
  stageChange,
} from "./model";
import type { SetupChange, SetupContext, SetupReview } from "./model";
import { CONTINUATION_KEY } from "./RoutineSetupPage";
import { OperationView } from "./OperationView";
import "./routines.css";
type Purpose = "collection" | "archive";
type Creation = {
  name: string;
  key: string;
  operationId: number | null;
  unknown: boolean;
  refused: boolean;
  retryAt: number | null;
  playlist: { id: string; name: string } | null;
  privacy: boolean | null;
  actor: string;
};
const creations = new WeakMap<object, Map<string, Creation>>();
const names: Record<string, string> = {
  add_playlist_source: "Add playlist source",
  add_search_source: "Add search source",
  remove_source: "Remove saved source",
  set_source_count: "Change source collection count",
  remove_routine_source: "Remove routine-only source",
  link_collection: "Link collection",
  replace_collection: "Replace collection",
  unlink_collection: "Unlink collection",
  set_drip: "Change adding settings",
  pair_archive: "Pair archive",
  replace_archive: "Replace archive",
  unpair_archive: "Unpair archive",
  set_auto_archive: "Change automatic archiving",
};
function sourceId(value: string) {
  const text = value.trim(),
    uri = /^spotify:playlist:([A-Za-z0-9]+)$/u.exec(text);
  if (uri) return uri[1]!;
  if (/^[A-Za-z0-9]+$/u.test(text)) return text;
  try {
    const url = new URL(text);
    if (url.protocol === "https:" && url.hostname === "open.spotify.com") {
      const match = /^\/playlist\/([A-Za-z0-9]+)\/?$/u.exec(url.pathname);
      if (match) return match[1]!;
    }
  } catch {
    /* Not a Spotify URL. */
  }
  throw Error("Use a Spotify playlist link, URI or ID.");
}
export function PlaylistSetupPage() {
  const { playlistId = "" } = useParams(),
    location = useLocation(),
    { api } = useServices(),
    session = useSession(),
    actor =
      session.status === "ready" && session.session.signed_in
        ? session.session.user.id
        : "",
    client = useMemo(() => createRoutineClient(api), [api]),
    { confirm } = useConfirm(),
    announce = useAnnouncer(),
    store = getOperationStore(api, actor),
    admissions = getAdmissionRegistry(api, actor, store),
    observed = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useSyncExternalStore(admissions.subscribe, admissions.getSnapshot);
  const loadedActor = useRef(actor);
  const returnTarget =
      new URLSearchParams(location.search).get("return_target") ?? "playlist",
    validReturn = ["playlist", "routines", "routine", "library"].includes(
      returnTarget,
    );
  let memories = creations.get(api);
  if (!memories) {
    memories = new Map();
    creations.set(api, memories);
  }
  const remembered = memories;
  const [context, setContext] = useState<SetupContext | null>(null),
    [changes, setChanges] = useState<SetupChange[]>([]),
    [source, setSource] = useState(""),
    [search, setSearch] = useState(""),
    [owned, setOwned] = useState<
      { id: string; name: string; owned: boolean }[]
    >([]),
    [selected, setSelected] = useState<Record<Purpose, string>>({
      collection: "",
      archive: "",
    }),
    [newNames, setNewNames] = useState<Record<Purpose, string>>({
      collection: "",
      archive: "",
    }),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [review, setReview] = useState<SetupReview | null>(null),
    [ack, setAck] = useState(false),
    [busy, setBusy] = useState(false),
    [creationBusy, setCreationBusy] = useState<Purpose | null>(null),
    [unknownSave, setUnknownSave] = useState(false),
    [, renderCreations] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null),
    read = useRef<Promise<SetupContext> | null>(null),
    inflight = useRef(false),
    creationLock = useRef(false),
    reviewedBody = useRef<{
      configuration_revision: string;
      changes: SetupChange[];
      section_digests: Record<string, string>;
    } | null>(null),
    existingDigest = useRef<string | null>(null);
  const unlinkedCreations = context
    ? (["collection", "archive"] as const).flatMap((purpose) => {
        const playlist = remembered.get(`${actor}:${playlistId}:${purpose}`)?.playlist;
        return playlist && context[purpose]?.playlist_id !== playlist.id ? [playlist] : [];
      })
    : [];
  const leaveMessage = [
    ...(changes.length ? ["Your staged playlist settings are not saved."] : []),
    ...Array.from(new Map(unlinkedCreations.map((playlist) => [playlist.id, playlist])).values())
      .map((playlist) => `${playlist.name} stays in your Spotify library.`),
  ].join(" ");
  useNavigationBlocker(
    `setup-${playlistId}`,
    changes.length > 0 || unlinkedCreations.length > 0,
    leaveMessage,
  );
  useEffect(() => {
    if (!validReturn) return;
    let active = true;
    read.current ??= client.setup(playlistId, returnTarget);
    void read.current.then(
      (value) => {
        if (active) setContext(value);
      },
      (problem) => {
        if (active) setError(problem.message);
      },
    );
    return () => {
      active = false;
    };
  }, [client, playlistId, returnTarget, validReturn]);
  useEffect(() => {
    document.title = "Playlist setup · Shuffify";
    heading.current?.focus();
  }, []);
  const memory = (purpose: Purpose) =>
    remembered.get(`${actor}:${playlistId}:${purpose}`);
  useEffect(() => {
    let changed = false;
    for (const purpose of ["collection", "archive"] as const) {
      const current = memory(purpose);
      if (!current?.operationId) continue;
      const entry = observed.get(current.operationId),
        result = entry?.operation?.result;
      if (
        admissions.getSnapshot().get(current.key)?.relation === "original" &&
        entry?.operation?.kind === "setup_playlist_create" &&
        record(result) &&
        record(result.playlist) &&
        typeof result.playlist.id === "string" &&
        result.playlist.id &&
        typeof result.playlist.name === "string"
      ) {
        if (current.playlist?.id !== result.playlist.id) {
          current.playlist = {
            id: result.playlist.id,
            name: result.playlist.name,
          };
          current.privacy = result.privacy_confirmed === true;
          current.unknown = false;
          changed = true;
        }
      } else if (
        entry?.operation &&
        ["failed", "rolled_back", "needs_attention"].includes(
          entry.operation.state,
        ) &&
        !current.unknown
      ) {
        current.refused =
          record(result) &&
          ["refused", "rate_limited"].includes(String(result.outcome));
        current.unknown = !current.refused;
        changed = true;
      }
    }
    if (changed) renderCreations((value) => value + 1);
  }, [observed, actor, playlistId]);
  useEffect(() => {
    const deadlines = (["collection", "archive"] as const)
      .map((purpose) => memory(purpose)?.retryAt)
      .filter(
        (value): value is number =>
          typeof value === "number" && value > Date.now(),
      );
    if (!deadlines.length) return;
    const timer = setTimeout(
      () => renderCreations((value) => value + 1),
      Math.min(...deadlines) - Date.now() + 1,
    );
    return () => clearTimeout(timer);
  }, [creationBusy, observed, actor, playlistId]);
  function stage(change: SetupChange) {
    setChanges((old) => stageChange(old, change));
    setReview(null);
    setAck(false);
    setMessage("Changes staged. Review and save to apply them.");
    setError("");
  }
  async function refreshOwned() {
    try {
      const reply = await client.owned(playlistId, true);
      setOwned(
        reply.playlists.filter(
          (value) => value.owned && value.id !== playlistId,
        ),
      );
      setMessage(
        "Owned playlists refreshed. Choose an exact playlist; a name match is not adopted automatically.",
      );
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "Playlists could not be refreshed.",
      );
    }
  }
  async function reload() {
    if (inflight.current) return;
    inflight.current = true;
    setBusy(true);
    try {
      const fresh = await client.setup(playlistId, returnTarget);
      setContext(fresh);
      if (unknownSave && existingDigest.current === fresh.state_digest) {
        setChanges([]);
        setMessage("The reviewed settings are confirmed saved.");
      } else
        setMessage(
          "Saved settings reloaded. Your staged choices remain; review them against this version.",
        );
      setUnknownSave(false);
      setReview(null);
      setAck(false);
      setError("");
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "Saved settings could not be loaded.",
      );
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }
  async function doReview() {
    if (inflight.current || !context || !changes.length || unknownSave) return;
    inflight.current = true;
    setBusy(true);
    setError("");
    const body = {
      configuration_revision: context.configuration_revision,
      changes: structuredClone(changes),
      section_digests: structuredClone(context.section_digests),
    };
    try {
      const answer = await client.impact(playlistId, body);
      if (
        !record(answer.acknowledgment) ||
        answer.configuration_revision !== body.configuration_revision
      )
        throw Error(
          "The settings review is incomplete. Reload and review again.",
        );
      reviewedBody.current = body;
      setReview(answer);
      setAck(false);
      announce(
        "Playlist settings reviewed. Nothing has been saved.",
        crypto.randomUUID(),
      );
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "Review could not be completed.",
      );
      setReview(null);
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }
  async function save() {
    if (inflight.current || !review || !reviewedBody.current || !ack) return;
    inflight.current = true;
    setBusy(true);
    setError("");
    try {
      const reply = await client.save(playlistId, {
        ...reviewedBody.current,
        acknowledgment: structuredClone(review.acknowledgment),
      });
      if (reply.saved !== true) throw Error("The save was not confirmed.");
      setChanges([]);
      setReview(null);
      setAck(false);
      setMessage(
        "Playlist settings saved. These settings apply to existing routines on their next run.",
      );
      if (reply.context) setContext(parseSetup(reply.context));
      else setMessage("Settings saved. Reload to confirm their current state.");
    } catch (problem) {
      existingDigest.current = review.expected_state_digest;
      setUnknownSave(unconfirmedMutation(problem));
      setReview(null);
      setError(
        problem instanceof Error
          ? problem.message
          : "Saving could not be confirmed.",
      );
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }
  async function create(purpose: Purpose, exactRetry = false) {
    if (
      creationLock.current ||
      !context ||
      session.status !== "ready" ||
      !session.session.signed_in ||
      session.session.user.id !== loadedActor.current
    )
      return;
    const prior = memory(purpose);
    if (
      exactRetry
        ? !prior?.unknown || !!prior.operationId
        : prior && (!prior.refused || (prior.retryAt ?? 0) > Date.now())
    )
      return;
    const name = exactRetry ? prior!.name : newNames[purpose].trim();
    if (!name || name.length > 100) {
      setError("Enter a playlist name from 1 to 100 characters.");
      return;
    }
    creationLock.current = true;
    setCreationBusy(purpose);
    setError("");
    const value: Creation = exactRetry
      ? prior!
      : {
          name,
          key: crypto.randomUUID(),
          operationId: null,
          unknown: false,
          refused: false,
          retryAt: null,
          playlist: null,
          privacy: null,
          actor,
        };
    remembered.set(`${actor}:${playlistId}:${purpose}`, value);
    renderCreations((n) => n + 1);
    try {
      const reply = exactRetry
        ? await admissions.retry(value.key, actor)
        : await admissions.submit(
            {
              key: value.key,
              feature: "setup",
              label: `Create ${purpose} “${name}” · ${context.target.name}`,
              resources: [
                ...spotifyResources(playlistId),
                { resource: "collection:spotify-playlists", access: "write" },
              ],
              path: `/api${playlistPath(playlistId)}/setup/spotify-playlists`,
              body: { encoding: "json", value: { purpose, name } },
              expected: { kind: "setup_playlist_create", target: playlistId },
            },
            actor,
          );
      if (reply.status !== "accepted")
        throw Error("Creation did not identify an operation.");
      value.operationId = reply.operation.id;
      value.unknown = false;
      value.refused = false;
      store.accept(reply.operation);
    } catch (problem) {
      value.unknown = admissionUnknown(admissions, value.key, problem);
      const retained = admissions.getSnapshot().get(value.key);
      value.operationId = retained?.operationId ?? null;
      value.refused = !value.unknown && retained?.relation !== "original";
      if (problem instanceof ApiRequestError && problem.retryAfterMs !== null)
        value.retryAt = Date.now() + problem.retryAfterMs;
      setError(
        problem instanceof Error
          ? problem.message
          : "Creation could not be confirmed.",
      );
    } finally {
      creationLock.current = false;
      setCreationBusy(null);
      renderCreations((n) => n + 1);
    }
  }
  function choose(purpose: Purpose, id: string) {
    if (!id || !context) return;
    stage({
      op:
        purpose === "collection"
          ? context.collection
            ? "replace_collection"
            : "link_collection"
          : context.archive
            ? "replace_archive"
            : "pair_archive",
      playlist_id: id,
    });
  }
  let back = "/library";
  if (returnTarget === "routines") back = "/routines";
  if (returnTarget === "playlist")
    back = `/workshop/${encodeURIComponent(playlistId)}`;
  if (returnTarget === "routine") {
    let continuation = null;
    try {
      continuation = readContinuation(
        sessionStorage.getItem(CONTINUATION_KEY),
        actor,
        playlistId,
      );
    } catch {
      /* Optional storage. */
    }
    back = `${playlistPath(playlistId)}/routines/new?origin=${continuation?.origin ?? "playlist"}${continuation?.scheduleId ? `&schedule_id=${continuation.scheduleId}` : ""}`;
  }
  useEffect(() => {
    if (error) announce(error, crypto.randomUUID());
  }, [error, announce]);
  if (actor !== loadedActor.current)
    return (
      <State kind="error" title="Your account changed">
        <a href={location.pathname + location.search}>
          Reload setup for your current account
        </a>
      </State>
    );
  if (!validReturn)
    return (
      <div className="sh-container sh-setup-page">
        <h1>Playlist setup link unavailable</h1>
        <Link to="/routines">Back to routines</Link>
      </div>
    );
  return (
    <div className="sh-container sh-setup-page">
      <header className="sh-routine-task-header">
        <Link className="sh-button-secondary" to={back}>
          {returnTarget === "routine"
            ? "Return to routine draft"
            : returnTarget === "playlist"
              ? "Back to playlist"
              : `Back to ${returnTarget}`}
        </Link>
        <p className="sh-eyebrow">Shared connections</p>
        <h1 className="sh-heading" tabIndex={-1} ref={heading}>
          Playlist setup · {context?.target.name ?? "Loading…"}
        </h1>
        <p>
          Sources, collection and archive. Save applies immediately to this
          playlist’s existing routines.
        </p>
        <nav aria-label="Setup sections" className="sh-routine-actions">
          <a href="#setup-sources" className="sh-button-secondary">
            Sources
          </a>
          <a href="#setup-collection" className="sh-button-secondary">
            Collection
          </a>
          <a href="#setup-archive" className="sh-button-secondary">
            Archive
          </a>
        </nav>
      </header>
      {error && (
        <State kind="error" title="Setup needs attention">
          {error}
          <Button variant="secondary" busy={busy} onClick={() => void reload()}>
            Reload saved settings
          </Button>
        </State>
      )}
      {message && (
        <p role="status" className="sh-routine-feedback">
          {message}
        </p>
      )}
      {context && (
        <div className="sh-setup-layout">
          <fieldset
            disabled={busy || unknownSave}
            className="sh-setup-sections"
          >
            <legend className="sr-only">Playlist settings</legend>
            <section className="sh-setup-section sh-surface" id="setup-sources">
              <h2 className="sh-heading">Sources</h2>
              <p>
                Collecting routines check these playlists and searches for new
                tracks.
              </p>
              {context.sources.length === 0 && <p>No saved sources yet.</p>}
              <ul className="sh-setup-list">
                {context.sources.map((value) => (
                  <li className="sh-setup-source" key={value.id}>
                    <h3>{value.name}</h3>
                    <p>
                      {value.type === "search_query"
                        ? value.search_query
                        : "Playlist source"}
                    </p>
                    <Field
                      id={`source-${value.id}-count`}
                      label={`Tracks to collect from ${value.name}`}
                    >
                      {(props) => (
                        <Input
                          {...props}
                          type="number"
                          min={1}
                          max={100}
                          value={String(
                            changes.find(
                              (change) =>
                                change.op === "set_source_count" &&
                                change.source_id === value.id,
                            )?.raid_count ?? value.raid_count,
                          )}
                          onChange={(event) =>
                            stage({
                              op: "set_source_count",
                              source_id: value.id,
                              raid_count: Number(event.currentTarget.value),
                            })
                          }
                        />
                      )}
                    </Field>
                    <Button
                      variant="secondary"
                      onClick={() =>
                        stage({ op: "remove_source", source_id: value.id })
                      }
                    >
                      Remove {value.name}
                    </Button>
                  </li>
                ))}
              </ul>
              {context.routine_only_sources.length > 0 && (
                <section>
                  <h3>Used only by routines</h3>
                  {context.routine_only_sources.map((value) => (
                    <p key={value.playlist_id}>
                      {value.playlist_id} ·{" "}
                      {value.routine_ids.map((id) => `#${id}`).join(", ")}{" "}
                      <Button
                        variant="secondary"
                        onClick={() =>
                          stage({
                            op: "remove_routine_source",
                            playlist_id: value.playlist_id,
                          })
                        }
                      >
                        Remove routine-only source {value.playlist_id}
                      </Button>
                    </p>
                  ))}
                </section>
              )}
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  try {
                    stage({
                      op: "add_playlist_source",
                      playlist_id: sourceId(source),
                    });
                    setSource("");
                  } catch (problem) {
                    setError((problem as Error).message);
                  }
                }}
              >
                <Field id="setup-add-source" label="Add by playlist link or ID">
                  {(props) => (
                    <Input
                      {...props}
                      value={source}
                      onChange={(event) => setSource(event.currentTarget.value)}
                    />
                  )}
                </Field>
                <Button variant="secondary" type="submit">
                  Add source
                </Button>
              </form>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  if (search.trim()) {
                    stage({
                      op: "add_search_source",
                      search_query: search.trim(),
                    });
                    setSearch("");
                  }
                }}
              >
                <Field id="setup-add-search" label="Add by search words">
                  {(props) => (
                    <Input
                      {...props}
                      maxLength={500}
                      value={search}
                      onChange={(event) => setSearch(event.currentTarget.value)}
                    />
                  )}
                </Field>
                <Button variant="secondary" type="submit">
                  Add search source
                </Button>
              </form>
            </section>
            {(["collection", "archive"] as const).map((purpose) => {
              const saved = context[purpose],
                association = changes.find((change) =>
                  (purpose === "collection"
                    ? [
                        "link_collection",
                        "replace_collection",
                        "unlink_collection",
                      ]
                    : ["pair_archive", "replace_archive", "unpair_archive"]
                  ).includes(change.op),
                ),
                hasAssociation = association
                  ? association.op !== "unlink_collection" &&
                    association.op !== "unpair_archive" &&
                    typeof association.playlist_id === "string" &&
                    association.playlist_id.length > 0 &&
                    association.playlist_id !== playlistId
                  : !!saved,
                creation = memory(purpose),
                creationClaim = admissions.conflict([
                  ...spotifyResources(playlistId),
                  { resource: "collection:spotify-playlists", access: "write" },
                ]),
                entry = creation?.operationId
                  ? observed.get(creation.operationId)
                  : undefined,
                active =
                  entry?.operation &&
                  ["queued", "running", "compensating"].includes(
                    entry.operation.state,
                  );
              return (
                <section
                  className="sh-setup-section sh-surface"
                  id={`setup-${purpose}`}
                  key={purpose}
                >
                  <h2 className="sh-heading">
                    {purpose === "collection" ? "Collection" : "Archive"}
                  </h2>
                  <p>
                    {purpose === "collection"
                      ? "Collected tracks wait here before they are added to your playlist."
                      : "Music set aside by Workshop and exchanged during rotation."}
                  </p>
                  <p>Saved: {saved?.name ?? "Not linked"}</p>
                  {saved && (
                    <Button
                      variant="secondary"
                      onClick={() =>
                        stage({
                          op:
                            purpose === "collection"
                              ? "unlink_collection"
                              : "unpair_archive",
                        })
                      }
                    >
                      Unlink {purpose}
                    </Button>
                  )}
                  <Field
                    id={`setup-${purpose}-select`}
                    label={`Your playlists for ${purpose}`}
                  >
                    {(props) => (
                      <Select
                        {...props}
                        value={selected[purpose]}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          setSelected((old) => ({ ...old, [purpose]: value }));
                        }}
                      >
                        <option value="">Choose a playlist</option>
                        {owned.map((value) => (
                          <option key={value.id} value={value.id}>
                            {value.name}
                          </option>
                        ))}
                      </Select>
                    )}
                  </Field>
                  <div className="sh-routine-actions">
                    <Button
                      variant="secondary"
                      disabled={!selected[purpose]}
                      onClick={() => choose(purpose, selected[purpose])}
                    >
                      Use existing {purpose}
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => void refreshOwned()}
                    >
                      Refresh my playlists for {purpose}
                    </Button>
                  </div>
                  <Field
                    id={`setup-${purpose}-name`}
                    label={`Name a new ${purpose} playlist`}
                    help="Creates a Spotify playlist immediately. It is used here only after you choose it and save settings."
                  >
                    {(props) => (
                      <Input
                        {...props}
                        maxLength={100}
                        value={newNames[purpose]}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          setNewNames((old) => ({ ...old, [purpose]: value }));
                        }}
                      />
                    )}
                  </Field>
                  <Button
                    variant="secondary"
                    disabled={
                      !!saved ||
                      !!creationClaim ||
                      (!!creation &&
                        (!creation.refused ||
                          (creation.retryAt ?? 0) > Date.now()))
                    }
                    busy={creationBusy === purpose || !!active}
                    onClick={() => void create(purpose)}
                  >
                    Create {purpose} playlist in Spotify
                  </Button>
                  {creationClaim &&
                    creationClaim.request.key !== creation?.key && (
                      <p role="status">
                        {creationClaim.request.label} is awaiting
                        reconciliation. Open its original review.
                      </p>
                    )}
                  {creation?.refused && (
                    <p role="status">
                      The creation was refused. No playlist was confirmed.{" "}
                      {creation.retryAt && creation.retryAt > Date.now()
                        ? "Wait for Spotify’s retry delay before another request."
                        : creationClaim
                          ? "The pending action must settle before another creation request."
                          : "You can submit another creation request."}
                    </p>
                  )}
                  {creation?.unknown && (
                    <div className="sh-routine-warning">
                      <p>
                        Creation could not be confirmed. The earlier request may
                        have created a playlist. Refresh, then choose the exact
                        playlist yourself.
                      </p>
                      <Button
                        variant="secondary"
                        onClick={() => void refreshOwned()}
                      >
                        Refresh my playlists
                      </Button>
                      {!creation.operationId && (
                        <>
                          <p>
                            Original creation: {creation.name}. Retry uses the
                            same name, purpose and request key.
                          </p>
                          <Button
                            variant="secondary"
                            busy={creationBusy === purpose}
                            disabled={(creation.retryAt ?? 0) > Date.now()}
                            onClick={() => void create(purpose, true)}
                          >
                            Retry this exact creation request
                          </Button>
                        </>
                      )}
                    </div>
                  )}
                  {creation?.playlist && (
                    <div className="sh-routine-feedback">
                      <p>Created: {creation.playlist.name}</p>
                      <p>
                        {creation.privacy
                          ? "Private visibility confirmed."
                          : "Privacy could not be confirmed. Check this playlist in Spotify."}
                      </p>
                      <a
                        href={`https://open.spotify.com/playlist/${encodeURIComponent(creation.playlist.id)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Open created playlist in Spotify
                      </a>
                      <Button
                        variant="secondary"
                        onClick={() => choose(purpose, creation.playlist!.id)}
                      >
                        Use created {purpose}
                      </Button>
                    </div>
                  )}
                  {entry?.operation && (
                    <OperationView operation={entry.operation} />
                  )}{" "}
                  {entry?.observation === "unavailable" && (
                    <Button
                      variant="secondary"
                      onClick={() => store.observe(entry.id)}
                    >
                      Check creation operation #{entry.id}
                    </Button>
                  )}
                  {purpose === "collection" && hasAssociation && (
                    <>
                      <Toggle
                        label="Enable adding collected tracks"
                        checked={Boolean(
                          changes.find((value) => value.op === "set_drip")
                            ?.drip_enabled ??
                            context.collection?.drip_enabled ??
                            false,
                        )}
                        onChange={(value) =>
                          stage({ op: "set_drip", drip_enabled: value })
                        }
                      />
                      <Field id="setup-drip-count" label="Tracks added per run">
                        {(props) => (
                          <Input
                            {...props}
                            type="number"
                            min={1}
                            max={50}
                            value={String(
                              changes.find((value) => value.op === "set_drip")
                                ?.drip_count ??
                                context.collection?.drip_count ??
                                3,
                            )}
                            onChange={(event) =>
                              stage({
                                op: "set_drip",
                                drip_count: Number(event.currentTarget.value),
                              })
                            }
                          />
                        )}
                      </Field>
                    </>
                  )}
                  {purpose === "archive" && hasAssociation && (
                    <Toggle
                      label="Automatically archive tracks removed in Workshop"
                      checked={Boolean(
                        changes.find((value) => value.op === "set_auto_archive")
                          ?.value ??
                        context.archive?.auto_archive_on_remove ??
                        true,
                      )}
                      onChange={(value) =>
                        stage({ op: "set_auto_archive", value })
                      }
                    />
                  )}
                </section>
              );
            })}
          </fieldset>
          <aside className="sh-setup-summary sh-surface">
            <h2 className="sh-routine-title">Staged changes</h2>
            <p>{changes.length} of 20 changes staged</p>
            {changes.length ? (
              <ol>
                {changes.map((change, index) => (
                  <li key={index}>
                    {names[change.op] ?? "Playlist setting"}
                    {typeof change.playlist_id === "string"
                      ? ` · ${change.playlist_id}`
                      : ""}
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setChanges((old) => old.filter((_, i) => i !== index));
                        setReview(null);
                        setAck(false);
                      }}
                    >
                      Undo {names[change.op] ?? "change"}
                    </Button>
                  </li>
                ))}
              </ol>
            ) : (
              <p>No unsaved settings.</p>
            )}
            <Button
              variant="secondary"
              disabled={!changes.length || busy || unknownSave}
              onClick={() => {
                setChanges((old) => old.slice(0, -1));
                setReview(null);
                setAck(false);
              }}
            >
              Undo last change
            </Button>
            <Button
              busy={busy}
              disabled={!changes.length || changes.length > 20 || unknownSave}
              onClick={() => void doReview()}
            >
              Review playlist settings
            </Button>
            {review && (
              <section className="sh-routine-group">
                <h3>Review shared impact</h3>
                {review.notes.map((value) => (
                  <p key={value}>{value}</p>
                ))}
                <p>
                  {review.affected_routines.length} existing routines affected.
                </p>
                {review.affected_routines.map((value, index) =>
                  record(value) ? (
                    <div key={index}>
                      <p>
                        Routine #{String(value.id)} · {String(value.operation)}
                      </p>
                      {Array.isArray(value.effects) && (
                        <ul>
                          {value.effects.map((effect, i) => (
                            <li key={i}>{String(effect)}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ) : null,
                )}
                <Toggle
                  label="I understand these settings affect existing routines"
                  checked={ack}
                  onChange={setAck}
                />
                <Button busy={busy} disabled={!ack} onClick={() => void save()}>
                  Save playlist settings
                </Button>
              </section>
            )}
            {unknownSave && (
              <>
                <p>
                  The save may already be complete. Read the saved settings
                  before trying another save.
                </p>
                <Button
                  variant="secondary"
                  busy={busy}
                  onClick={() => void reload()}
                >
                  Check saved settings
                </Button>
              </>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
