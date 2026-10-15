import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import Sortable from "sortablejs";
import type { LibraryRow } from "../../api/contracts";
import { Artwork } from "../../components/Artwork";
import { Button, Field, Input, Select } from "../../components/ui/controls";
import type { LibraryController } from "./controller";
import { sectionNames, validateShuffle } from "./model";
import type { Section } from "./model";
import { useLibrary, useClock, escapeDisclosure } from "./hooks";
export function OrderList({
  controller: c,
  section,
  organizing,
  children,
}: {
  controller: LibraryController;
  section: Section;
  organizing: boolean;
  children: ReactNode;
}) {
  const list = useRef<HTMLUListElement>(null),
    state = useLibrary(c);
  useEffect(() => {
    const node = list.current;
    if (!node || !organizing || section === "hidden") return;
    let original: Element[] = [];
    const sortable = Sortable.create(node, {
      animation: 0,
      forceFallback: false,
      handle: ".sh-library-drag",
      draggable: ".sh-library-row",
      group: { name: section, pull: false, put: false },
      onStart: () => {
        original = Array.from(node.children);
      },
      onEnd: (event) => {
        const moved = event.item.dataset.playlistId;
        for (const row of original) node.append(row);
        if (moved && event.newDraggableIndex !== undefined)
          c.move(moved, event.newDraggableIndex + 1);
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
  }, [c, section, organizing]);
  return (
    <ul
      ref={list}
      className="sh-library-list"
      data-section={section}
      aria-label={`${sectionNames[section]} playlists`}
      aria-busy={state.preferenceBusy || undefined}
    >
      {children}
    </ul>
  );
}
export function PlaylistRow({
  controller: c,
  row,
  section,
  position,
  total,
  organizing,
  begin,
  toggle,
  move,
}: {
  controller: LibraryController;
  row: LibraryRow;
  section: Section;
  position: number;
  total: number;
  organizing: boolean;
  begin: (
    id: string,
    kind: "shuffle" | "undo" | "privacy",
    trigger: HTMLElement,
  ) => void;
  toggle: (id: string, flag: "pinned" | "hidden", trigger: HTMLElement) => void;
  move: (id: string, n: number, trigger: HTMLElement) => void;
}) {
  const s = useLibrary(c),
    now = useClock(s.preferenceRetryUntil),
    preferenceDisabled =
      s.loading || s.preferenceUnknown || now < s.preferenceRetryUntil,
    draft = s.drafts.get(row.id)!,
    method = s.context!.algorithms.find(
      (a) => a.class_name === draft.algorithm,
    ),
    [errors, setErrors] = useState<Record<string, string>>({}),
    [positionValue, setPositionValue] = useState(String(position)),
    [positionError, setPositionError] = useState(""),
    options = useRef<HTMLDetailsElement>(null),
    select = useRef<HTMLSelectElement>(null);
  useEffect(() => setPositionValue(String(position)), [position]);
  const choose = (trigger: HTMLElement) => {
    if (!draft.algorithm) {
      if (options.current) options.current.open = true;
      select.current?.focus();
      return;
    }
    const found = validateShuffle(draft, row, s.context!.algorithms);
    setErrors(found);
    if (Object.keys(found).length) {
      if (options.current) options.current.open = true;
      requestAnimationFrame(() =>
        document
          .getElementById(`library-${row.id}-${Object.keys(found)[0]}`)
          ?.focus(),
      );
      return;
    }
    begin(row.id, "shuffle", trigger);
  };
  return (
    <li className="sh-library-row" data-playlist-id={row.id}>
      <Artwork
        src={row.image_url}
        name={row.name}
        spotifyUrl={row.spotify_url}
      />
      <div className="sh-library-row-main">
        <h3 className="sh-library-title">{row.name}</h3>
        <p className="sh-caption sh-text-muted">
          {row.track_count} {row.track_count === 1 ? "track" : "tracks"} ·{" "}
          {row.public === null
            ? "Visibility unknown"
            : row.public
              ? "Public"
              : "Private"}
        </p>
        <Link
          className="sh-library-link"
          to={`/workshop/${encodeURIComponent(row.id)}`}
        >
          Open playlist <span className="sh-sr-only">{row.name}</span>
        </Link>
      </div>
      <details
        className="sh-library-playlist-actions"
        onKeyDown={escapeDisclosure}
      >
        <summary>
          Playlist actions <span className="sh-sr-only">for {row.name}</span>
        </summary>
        <div className="sh-library-action-body">
          <Button
            aria-disabled={row.track_count === 0 || s.loading || c.blocked()}
            onClick={(e) => choose(e.currentTarget)}
          >
            {draft.algorithm ? "Shuffle now" : "Choose shuffle"}
            <span className="sh-sr-only"> {row.name}</span>
          </Button>
          <details ref={options} onKeyDown={escapeDisclosure}>
            <summary>
              {method?.name ?? "Shuffle options"} · Change{" "}
              <span className="sh-sr-only">for {row.name}</span>
            </summary>
            <form
              noValidate
              onSubmit={(e) => e.preventDefault()}
              className="sh-library-options"
            >
              <Field
                id={`library-${row.id}-algorithm`}
                label="Shuffle method"
                error={errors.algorithm ?? ""}
              >
                {(props) => (
                  <Select
                    {...props}
                    ref={select}
                    value={draft.algorithm}
                    onChange={(e) => {
                      c.editMethod(row.id, e.currentTarget.value);
                      setErrors({});
                    }}
                  >
                    <option value="">Choose a method</option>
                    {s.context!.algorithms.map((a) => (
                      <option key={a.class_name} value={a.class_name}>
                        {a.name}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              {method && (
                <>
                  <p className="sh-caption sh-text-muted">
                    {method.description}
                  </p>
                  {Object.entries(method.parameters).map(([name, info]) => (
                    <Field
                      key={`${method.class_name}-${name}`}
                      id={`library-${row.id}-${name}`}
                      label={
                        name === "keep_first"
                          ? "Keep first tracks"
                          : info.description
                      }
                      error={errors[name] ?? ""}
                    >
                      {(props) =>
                        info.type === "string" ? (
                          <Select
                            {...props}
                            value={draft.values[draft.algorithm]?.[name] ?? ""}
                            onChange={(e) =>
                              c.editParameter(
                                row.id,
                                name,
                                e.currentTarget.value,
                              )
                            }
                          >
                            {info.options.map((value) => (
                              <option key={value}>{value}</option>
                            ))}
                          </Select>
                        ) : (
                          <Input
                            {...props}
                            required
                            type="number"
                            step={info.type === "integer" ? 1 : "any"}
                            min={info.min}
                            max={
                              name === "keep_first" ? row.track_count : info.max
                            }
                            value={draft.values[draft.algorithm]?.[name] ?? ""}
                            onChange={(e) => {
                              c.editParameter(
                                row.id,
                                name,
                                e.currentTarget.value,
                              );
                              setErrors({});
                            }}
                          />
                        )
                      }
                    </Field>
                  ))}
                </>
              )}
              <Button
                variant="secondary"
                onClick={() => {
                  if (options.current) {
                    options.current.open = false;
                    options.current.querySelector("summary")?.focus();
                  }
                }}
              >
                Done <span className="sh-sr-only">with shuffle options</span>
              </Button>
            </form>
          </details>
          {row.can_undo && (
            <Button
              variant="secondary"
              aria-disabled={s.loading || c.blocked()}
              onClick={(e) => begin(row.id, "undo", e.currentTarget)}
            >
              Restore previous saved version now
            </Button>
          )}
          <Button
            variant="secondary"
            aria-disabled={s.loading || c.blocked()}
            onClick={(e) => begin(row.id, "privacy", e.currentTarget)}
          >
            {row.public ? "Make private now" : "Make public now"}
          </Button>
        </div>
      </details>
      {organizing && (
        <div
          className="sh-library-organize-controls"
          aria-label={`Organize ${row.name}`}
        >
          <Button
            variant="secondary"
            data-control="pinned"
            busy={s.preferenceBusy}
            aria-disabled={preferenceDisabled}
            onClick={(e) => toggle(row.id, "pinned", e.currentTarget)}
          >
            {row.pinned ? "Remove favorite" : "Add favorite"}
            <span className="sh-sr-only"> {row.name}</span>
          </Button>
          <Button
            variant="secondary"
            data-control="hidden"
            busy={s.preferenceBusy}
            aria-disabled={preferenceDisabled}
            onClick={(e) => toggle(row.id, "hidden", e.currentTarget)}
          >
            {row.hidden ? "Show playlist" : "Hide playlist"}
            <span className="sh-sr-only"> {row.name}</span>
          </Button>
          {section !== "hidden" && (
            <>
              <button
                type="button"
                className="sh-button-secondary sh-library-drag"
                aria-label={`Drag to reorder ${row.name}`}
                aria-disabled={preferenceDisabled}
                title="Drag within this section; or use Move up, Move down and Move to position"
              >
                ↕ <span className="sh-sr-only">Drag to reorder</span>
              </button>
              <Button
                variant="secondary"
                aria-disabled={position <= 1 || preferenceDisabled}
                onClick={(e) => move(row.id, position - 1, e.currentTarget)}
              >
                Move up<span className="sh-sr-only"> {row.name}</span>
              </Button>
              <Button
                variant="secondary"
                aria-disabled={position >= total || preferenceDisabled}
                onClick={(e) => move(row.id, position + 1, e.currentTarget)}
              >
                Move down<span className="sh-sr-only"> {row.name}</span>
              </Button>
              <form
                className="sh-library-position"
                noValidate
                onSubmit={(e) => {
                  e.preventDefault();
                  const control =
                    e.currentTarget.querySelector<HTMLInputElement>("input")!;
                  if (
                    !positionValue.trim() ||
                    !Number.isFinite(Number(positionValue))
                  ) {
                    setPositionError(`Enter a position from 1 to ${total}.`);
                    control.focus();
                    return;
                  }
                  setPositionError("");
                  const bounded = Math.max(
                    1,
                    Math.min(total, Math.trunc(Number(positionValue))),
                  );
                  setPositionValue(String(bounded));
                  move(row.id, bounded, control);
                }}
              >
                <label htmlFor={`position-${row.id}`}>
                  Move to position{" "}
                  <span className="sh-sr-only">for {row.name}</span>
                </label>
                <Input
                  id={`position-${row.id}`}
                  type="number"
                  min={1}
                  max={total}
                  step={1}
                  value={positionValue}
                  aria-invalid={!!positionError || undefined}
                  aria-describedby={
                    positionError ? `position-error-${row.id}` : undefined
                  }
                  onChange={(e) => {
                    setPositionValue(e.currentTarget.value);
                    setPositionError("");
                  }}
                />
                <Button
                  variant="secondary"
                  type="submit"
                  aria-disabled={preferenceDisabled}
                >
                  Move<span className="sh-sr-only"> {row.name}</span>
                </Button>
                {positionError && (
                  <p id={`position-error-${row.id}`} role="alert">
                    {positionError}
                  </p>
                )}
              </form>
            </>
          )}
        </div>
      )}
    </li>
  );
}
