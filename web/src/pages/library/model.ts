import type {
  Algorithm,
  AlgorithmParameter,
  LibraryContext,
  LibraryGreeting,
  LibraryRow,
  ShuffleDefaults,
} from "../../api/contracts.ts";
export type Sections = LibraryContext["sections"];
export type Section = keyof Sections;
export const sectionNames: Record<Section, string> = {
  favorites: "Favorites",
  playlists: "All playlists",
  hidden: "Hidden",
};
export interface ShuffleDraft {
  algorithm: string;
  values: Record<string, Record<string, string>>;
  revision: number;
}
export type ShuffleForm = { algorithm: string } & Record<string, string>;
function defaultsFor(
  algorithm: Algorithm,
  row: LibraryRow,
  defaults: ShuffleDefaults,
): Record<string, string> {
  const saved =
    defaults.algorithm === algorithm.class_name ? defaults.params : {};
  return Object.fromEntries(
    Object.entries(algorithm.parameters).map(([name, info]) => {
      const candidate = saved[name as keyof typeof saved];
      let value: string | number = info.default;
      if (info.type === "string") {
        if (typeof candidate === "string" && info.options.includes(candidate))
          value = candidate;
      } else if (
        typeof candidate === "number" &&
        Number.isFinite(candidate) &&
        (info.type !== "integer" || Number.isInteger(candidate)) &&
        (info.min === undefined || candidate >= info.min) &&
        (info.max === undefined || candidate <= info.max)
      )
        value = candidate;
      if (name === "keep_first")
        value = Math.min(Number(value), row.track_count);
      return [name, String(value)];
    }),
  );
}
export function createShuffleDraft(
  row: LibraryRow,
  algorithms: Algorithm[],
  defaults: ShuffleDefaults,
): ShuffleDraft {
  const algorithm = algorithms.find(
    (method) => method.class_name === defaults.algorithm,
  );
  return {
    algorithm: algorithm?.class_name ?? "",
    values: algorithm
      ? { [algorithm.class_name]: defaultsFor(algorithm, row, defaults) }
      : {},
    revision: 0,
  };
}
export function selectMethod(
  draft: ShuffleDraft,
  method: string,
  row: LibraryRow,
  algorithms: Algorithm[],
  defaults: ShuffleDefaults,
): ShuffleDraft {
  const algorithm = algorithms.find((value) => value.class_name === method);
  return {
    ...draft,
    algorithm: method,
    values:
      algorithm && !draft.values[method]
        ? { ...draft.values, [method]: defaultsFor(algorithm, row, defaults) }
        : draft.values,
    revision: draft.revision + 1,
  };
}
export function setParameter(
  draft: ShuffleDraft,
  name: string,
  value: string,
): ShuffleDraft {
  return {
    ...draft,
    values: {
      ...draft.values,
      [draft.algorithm]: { ...draft.values[draft.algorithm], [name]: value },
    },
    revision: draft.revision + 1,
  };
}
export function parameterError(
  info: AlgorithmParameter,
  value: string | undefined,
  name: string,
  trackCount: number,
): string | null {
  if (value === undefined || !value.trim()) return "Enter a value.";
  if (info.type === "string")
    return info.options.includes(value)
      ? null
      : "Choose one of the available options.";
  const number = Number(value),
    max = name === "keep_first" ? trackCount : info.max;
  if (
    !Number.isFinite(number) ||
    (info.type === "integer" && !Number.isInteger(number))
  )
    return info.type === "integer"
      ? "Enter a whole number."
      : "Enter a number.";
  if (info.min !== undefined && number < info.min)
    return `Choose ${info.min} or more.`;
  if (max !== undefined && number > max) return `Choose ${max} or less.`;
  return null;
}
export function validateShuffle(
  draft: ShuffleDraft,
  row: LibraryRow,
  algorithms: Algorithm[],
): Record<string, string> {
  const algorithm = algorithms.find(
    (value) => value.class_name === draft.algorithm,
  );
  if (!algorithm) return { algorithm: "Choose a shuffle method." };
  if (row.track_count <= 0)
    return { algorithm: "Add tracks in Workshop before shuffling." };
  return Object.fromEntries(
    Object.entries(algorithm.parameters).flatMap(([name, info]) => {
      const error = parameterError(
        info,
        draft.values[draft.algorithm]?.[name],
        name,
        row.track_count,
      );
      return error ? [[name, error]] : [];
    }),
  );
}
export function shuffleForm(
  draft: ShuffleDraft,
  row: LibraryRow,
  algorithms: Algorithm[],
): ShuffleForm {
  if (Object.keys(validateShuffle(draft, row, algorithms)).length)
    throw new TypeError("Check the shuffle method and its options.");
  const method = algorithms.find(
    (value) => value.class_name === draft.algorithm,
  )!;
  return {
    algorithm: draft.algorithm,
    ...Object.fromEntries(
      Object.entries(method.parameters).map(([name, info]) => {
        const value = draft.values[draft.algorithm]![name]!;
        return [name, info.type === "string" ? value : String(Number(value))];
      }),
    ),
  };
}
export function findRow(
  sections: Sections,
  id: string,
): LibraryRow | undefined {
  return [
    ...sections.favorites,
    ...sections.playlists,
    ...sections.hidden,
  ].find((row) => row.id === id);
}
export function rowSection(sections: Sections, id: string): Section | null {
  return (
    (Object.keys(sections) as Section[]).find((section) =>
      sections[section].some((row) => row.id === id),
    ) ?? null
  );
}
export function orderedIds(sections: Sections): string[] {
  return [...sections.favorites, ...sections.playlists].map((row) => row.id);
}
export function placeRow(
  sections: Sections,
  id: string,
  change: Partial<Pick<LibraryRow, "pinned" | "hidden">>,
): Sections {
  const old = rowSection(sections, id),
    previous = findRow(sections, id);
  if (!previous || !old) return sections;
  const row = { ...previous, ...change },
    destination: Section = row.hidden
      ? "hidden"
      : row.pinned
        ? "favorites"
        : "playlists";
  if (old === destination)
    return {
      ...sections,
      [old]: sections[old].map((value) => (value.id === id ? row : value)),
    };
  const next = {
    ...sections,
    [old]: sections[old].filter((value) => value.id !== id),
  };
  return {
    ...next,
    [destination]:
      destination === "playlists"
        ? [row, ...next[destination]]
        : [...next[destination], row],
  };
}
export function moveRow(
  sections: Sections,
  id: string,
  position: number,
): Sections {
  if (!Number.isFinite(position))
    throw new TypeError("Enter a valid position.");
  const section = rowSection(sections, id);
  if (!section || section === "hidden") return sections;
  const rows = sections[section],
    from = rows.findIndex((row) => row.id === id),
    to = Math.max(0, Math.min(rows.length - 1, Math.trunc(position) - 1));
  if (from === to) return sections;
  const next = [...rows],
    row = next.splice(from, 1)[0]!;
  next.splice(to, 0, row);
  return { ...sections, [section]: next };
}
export function greeting(value: LibraryGreeting): {
  welcome: string;
  detail: string;
  onboarding: boolean;
} {
  const name = value.display_name ? `, ${value.display_name}` : "";
  return value.is_returning_user
    ? {
        welcome: `Welcome back${name}!`,
        detail:
          value.activity_since_last_login_count > 0
            ? `${value.activity_since_last_login_count} thing${value.activity_since_last_login_count === 1 ? "" : "s"} happened since your last visit.`
            : "Everything is up to date.",
        onboarding: false,
      }
    : {
        welcome: `Welcome to Shuffify${name}!`,
        detail:
          "Open a playlist to arrange its tracks, or use its Playlist actions for a quick shuffle.",
        onboarding: true,
      };
}
