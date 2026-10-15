import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import type { ApiClient } from "../../api/client";
import type { PlaylistRef } from "../../api/contracts";
import { Button, Field, Input } from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { loadPicker } from "./core/client";
const cached = new WeakMap<ApiClient, Map<string, Promise<PlaylistRef[]>>>();
function playlists(api: ApiClient, actor: string) {
  let actors = cached.get(api);
  if (!actors) {
    actors = new Map();
    cached.set(api, actors);
  }
  let loaded = actors.get(actor);
  if (!loaded) {
    loaded = loadPicker(api);
    actors.set(actor, loaded);
    void loaded.catch(() => actors!.delete(actor));
  }
  return loaded;
}
export function PlaylistPicker({
  api,
  actor,
  current = null,
  close,
}: {
  api: ApiClient;
  actor: string;
  current?: string | null;
  close?: () => void;
}) {
  const [rows, setRows] = useState<PlaylistRef[] | null>(null),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [attempt, setAttempt] = useState(0),
    search = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true;
    setError("");
    void playlists(api, actor).then(
      (rows) => {
        if (active) setRows(rows);
      },
      (e) => {
        if (active)
          setError(
            e instanceof Error ? e.message : "Playlists could not be loaded.",
          );
      },
    );
    requestAnimationFrame(() => search.current?.focus({ preventScroll: true }));
    return () => {
      active = false;
    };
  }, [api, actor, attempt]);
  const filtered = rows?.filter((p) =>
    p.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  return (
    <div className="sh-workshop-panel-stack">
      <Field id="workshop-playlist-search" label="Find a playlist">
        {(props) => (
          <Input
            {...props}
            ref={search}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
          />
        )}
      </Field>
      {error ? (
        <State
          kind="error"
          title="Playlists could not be loaded"
          action={
            <Button onClick={() => setAttempt((a) => a + 1)}>Try again</Button>
          }
        >
          {error}
        </State>
      ) : !rows ? (
        <State kind="loading" title="Loading playlists…" />
      ) : !filtered?.length ? (
        <p>No playlists match.</p>
      ) : (
        <ul className="sh-workshop-picker">
          {filtered.map((p) => (
            <li key={p.id}>
              {p.id === current ? (
                <span>{p.name} · Current playlist</span>
              ) : (
                <Link
                  className="sh-button-secondary"
                  to={`/workshop/${encodeURIComponent(p.id)}`}
                  onClick={() => close?.()}
                >
                  {p.name}
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
