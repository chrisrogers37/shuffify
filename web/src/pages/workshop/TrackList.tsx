import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import Sortable from "sortablejs";
import { Artwork } from "../../components/Artwork";
import { Button, Field, Input } from "../../components/ui/controls";
import { useAnnouncer } from "../../a11y/Announcer";
import { useWorkshop } from "./workspace";
import type { WorkshopController } from "./core/controller";
import type { Occurrence } from "./core/types";
export function TrackList({
  controller: c,
  renderActions,
}: {
  controller: WorkshopController;
  renderActions?: (row: Occurrence) => ReactNode;
}) {
  const s = useWorkshop(c),
    list = useRef<HTMLOListElement>(null),
    heading = useRef<HTMLHeadingElement>(null),
    [active, setActive] = useState<string | null>(null),
    announce = useAnnouncer(),
    events = useRef(0);
  const focus = (key: string | null) =>
    requestAnimationFrame(() => {
      const row = key
        ? list.current?.querySelector<HTMLElement>(
            `[data-occurrence="${CSS.escape(key)}"]`,
          )
        : null;
      (
        row?.querySelector<HTMLElement>("[data-row-actions]") ?? heading.current
      )?.focus({ preventScroll: true });
    });
  function move(key: string, n: number) {
    const row = c.getSnapshot().working.find((r) => r.key === key);
    if (c.moveOccurrence(key, n)) {
      announce(
        `${row?.track.name} moved to position ${c.getSnapshot().working.findIndex((r) => r.key === key) + 1}.`,
        `workshop-move-${++events.current}`,
      );
      requestAnimationFrame(() => {
        const panel = list.current?.querySelector<HTMLElement>(
          `[data-occurrence="${CSS.escape(key)}"] [data-action-first]`,
        );
        const first = panel
          ?.closest("[role=region]")
          ?.querySelector<HTMLElement>("button:not(:disabled)");
        first?.focus({ preventScroll: true });
      });
    } else
      announce(c.getSnapshot().notice, `workshop-refused-${++events.current}`);
  }
  function remove(key: string) {
    const rows = c.getSnapshot().working,
      index = rows.findIndex((r) => r.key === key),
      next = rows[index + 1]?.key ?? rows[index - 1]?.key ?? null,
      name = rows[index]?.track.name;
    if (c.removeOccurrence(key)) {
      setActive(null);
      focus(next);
      announce(
        `${name} removed from the local draft.`,
        `workshop-remove-${++events.current}`,
      );
    }
  }
  useEffect(() => {
    const node = list.current;
    if (!node) return;
    let dragRevision = 0;
    const sortable = Sortable.create(node, {
      animation: 0,
      forceFallback: false,
      handle: ".sh-workshop-drag",
      draggable: "[data-occurrence]",
      onStart: () => {
        dragRevision = c.getSnapshot().revision;
      },
      onEnd: (event) => {
        const key = event.item.dataset.occurrence;
        const current = c.getSnapshot();
        const shown = new Map(
          Array.from(node.children).map((row) => [
            (row as HTMLElement).dataset.occurrence,
            row,
          ]),
        );
        current.working.forEach((row) => {
          const element = shown.get(row.key);
          if (element) node.append(element);
        });
        if (
          current.revision === dragRevision &&
          key &&
          event.newDraggableIndex !== undefined
        )
          move(key, event.newDraggableIndex + 1);
        requestAnimationFrame(() => {
          node.querySelectorAll<HTMLElement>("[style]").forEach((el) => {
            if (!el.getAttribute("style")?.trim()) el.removeAttribute("style");
          });
          if (!node.getAttribute("style")?.trim())
            node.removeAttribute("style");
        });
      },
    });
    return () => sortable.destroy();
  }, [c]);
  return (
    <section aria-labelledby="workshop-tracks-heading">
      <div className="sh-workshop-tracks-heading">
        <h2 id="workshop-tracks-heading" ref={heading} tabIndex={-1}>
          Tracks
        </h2>
        <span className="sh-caption">
          {s.working.length} {s.working.length === 1 ? "track" : "tracks"}
        </span>
      </div>
      <p className="sh-caption sh-text-muted">
        Reorder tracks with the drag handle or Actions. Each repeated track is a
        separate occurrence.
      </p>
      {!s.working.length && (
        <p className="sh-empty">
          This draft is empty. Saving it removes every track from this playlist.
        </p>
      )}
      <ol
        ref={list}
        className="sh-workshop-track-list"
        aria-label="Draft tracks"
      >
        {s.working.map((row, i) => (
          <TrackRow
            key={row.key}
            extraActions={renderActions?.(row)}
            row={row}
            position={i + 1}
            total={s.working.length}
            held={s.locks[row.uri] === true}
            active={active === row.key}
            permitted={c.permission("draft").allowed}
            onOpen={() => setActive(active === row.key ? null : row.key)}
            onClose={() => {
              setActive(null);
              focus(row.key);
            }}
            onDismiss={() => setActive(null)}
            onMove={(n) => move(row.key, n)}
            onRemove={() => remove(row.key)}
          />
        ))}
      </ol>
    </section>
  );
}
function TrackRow({
  row,
  position,
  total,
  held,
  active,
  permitted,
  onOpen,
  onClose,
  onDismiss,
  onMove,
  onRemove,
  extraActions,
}: {
  extraActions?: ReactNode;
  row: Occurrence;
  position: number;
  total: number;
  held: boolean;
  active: boolean;
  permitted: boolean;
  onOpen: () => void;
  onClose: () => void;
  onDismiss: () => void;
  onMove: (n: number) => void;
  onRemove: () => void;
}) {
  const [value, setValue] = useState(String(position)),
    [error, setError] = useState(""),
    region = useRef<HTMLDivElement>(null),
    first = useRef<HTMLButtonElement>(null),
    previous = useRef(false);
  useEffect(() => setValue(String(position)), [position]);
  useEffect(() => {
    if (active && !previous.current)
      (
        region.current?.querySelector<HTMLElement>("button:not(:disabled)") ??
        first.current
      )?.focus({ preventScroll: true });
    previous.current = active;
  }, [active]);
  const title = row.track.name,
    trackURL = `https://open.spotify.com/track/${row.track.id}`;
  return (
    <li
      data-occurrence={row.key}
      className="sh-workshop-track"
      onKeyDown={(e) => {
        if (e.key === "Escape" && active) {
          e.preventDefault();
          onClose();
        }
      }}
    >
      <div className="sh-workshop-track-main">
        <span
          className="sh-workshop-position"
          aria-label={`Position ${position}`}
        >
          {position}
        </span>
        <Artwork
          src={row.track.album_image_url}
          name={title}
          spotifyUrl={trackURL}
        />
        <div className="sh-workshop-track-copy">
          <h3>{title}</h3>
          <p>
            {row.track.artists.filter(Boolean).join(", ") ||
              "Artist unavailable"}
          </p>
          <p className="sh-caption sh-text-muted">
            {row.track.album_name ?? "Album unavailable"}
            {held ? " · Held" : ""}
            {row.track.duration_ms !== null
              ? ` · ${Math.floor(row.track.duration_ms / 60000)}:${String(Math.floor(row.track.duration_ms / 1000) % 60).padStart(2, "0")}`
              : ""}
          </p>
        </div>
        <div className="sh-workshop-row-controls">
          <Button
            className="sh-workshop-drag"
            variant="secondary"
            aria-label={`Drag ${title} occurrence ${position}`}
            disabled={!permitted || held}
            title="Use Actions for keyboard reordering"
          >
            Drag
          </Button>
          <Button
            data-row-actions
            variant="secondary"
            aria-expanded={active}
            aria-controls={`actions-${row.key}`}
            aria-label={`Actions for ${title} occurrence ${position}`}
            onClick={onOpen}
          >
            Actions
          </Button>
        </div>
      </div>
      {active && (
        <div
          ref={region}
          className="sh-workshop-row-actions"
          id={`actions-${row.key}`}
          role="region"
          aria-label={`${title} occurrence ${position} actions`}
          onBlur={(e) => {
            if (
              e.relatedTarget instanceof Node &&
              !e.currentTarget
                .closest("[data-occurrence]")
                ?.contains(e.relatedTarget)
            )
              onDismiss();
          }}
        >
          <div className="sh-workshop-action-buttons">
            <Button
              ref={first}
              data-action-first
              variant="secondary"
              disabled={!permitted || held || position === 1}
              onClick={() => onMove(position - 1)}
            >
              Move up
            </Button>
            <Button
              variant="secondary"
              disabled={!permitted || held || position === total}
              onClick={() => onMove(position + 1)}
            >
              Move down
            </Button>
            <Button variant="danger" disabled={!permitted} onClick={onRemove}>
              Remove from draft
            </Button>
            <Button variant="secondary" onClick={onClose}>
              Close actions
            </Button>
          </div>
          {extraActions}
          <form
            noValidate
            className="sh-workshop-move-form"
            onSubmit={(e) => {
              e.preventDefault();
              const n = Number(value);
              if (!value.trim() || !Number.isFinite(n)) {
                setError(`Enter a position from 1 to ${total}.`);
                return;
              }
              setError("");
              onMove(Math.max(1, Math.min(total, Math.trunc(n))));
            }}
          >
            <Field
              id={`position-${row.key}`}
              label="Move to position"
              error={error}
            >
              {(props) => (
                <Input
                  {...props}
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={value}
                  onChange={(e) => setValue(e.currentTarget.value)}
                />
              )}
            </Field>
            <Button
              variant="secondary"
              type="submit"
              disabled={!permitted || held}
            >
              Move
            </Button>
          </form>
        </div>
      )}
    </li>
  );
}
