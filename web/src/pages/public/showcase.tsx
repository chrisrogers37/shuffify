import type { ReactNode } from "react";
export function Showcase({
  id,
  eyebrow,
  title,
  description,
  steps,
  numbered = true,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  description: string;
  steps: readonly (readonly [string, string])[];
  numbered?: boolean;
  children: ReactNode;
}) {
  const List = numbered ? "ol" : "ul";
  return (
    <section
      id={id}
      tabIndex={-1}
      className="sh-container sh-showcase"
      aria-labelledby={`${id}-title`}
    >
      <div className="sh-showcase-copy">
        <p className="sh-eyebrow">
          <span aria-hidden="true" />
          {eyebrow}
        </p>
        <h2 id={`${id}-title`} className="sh-heading sh-showcase-title">
          {title}
        </h2>
        <p className="sh-showcase-description">{description}</p>
        <List
          className={`sh-showcase-steps${numbered ? "" : " sh-showcase-options"}`}
          role={numbered ? undefined : "list"}
        >
          {steps.map(([heading, detail]) => (
            <li key={heading}>
              <strong>{heading}</strong>
              <p>{detail}</p>
            </li>
          ))}
        </List>
      </div>
      {children}
    </section>
  );
}
export function Example({
  title,
  caption,
  label,
  children,
}: {
  title: string;
  caption: string;
  label?: string;
  children: ReactNode;
}) {
  return (
    <figure className="sh-surface sh-example">
      <div className="sh-example-head">
        {label && <p className="sh-example-label">{label}</p>}
        <h3>{title}</h3>
      </div>
      <div className="sh-example-body">{children}</div>
      <figcaption className="sh-example-caption">
        {caption && <p>{caption}</p>}
        <p className="sh-example-sample-note">Sample playlists and songs.</p>
      </figcaption>
    </figure>
  );
}
export function PlaylistRow({
  name,
  detail,
  metric,
}: {
  name: string;
  detail: string;
  metric?: string;
}) {
  return (
    <li className="sh-example-row">
      <div>
        <p className="sh-example-name">{name}</p>
        <p className="sh-example-detail">{detail}</p>
      </div>
      {metric && <span className="sh-example-badge">{metric}</span>}
    </li>
  );
}
export function Flow({ children }: { children: ReactNode }) {
  return (
    <p className="sh-example-flow">
      <span aria-hidden="true">↓</span>
      <span>{children}</span>
    </p>
  );
}
const palettes = [
  "sh-illustration-cover",
  "sh-illustration-cover-2",
  "sh-illustration-cover-3",
  "sh-illustration-cover-4",
  "sh-illustration-cover-5",
  "sh-illustration-cover-6",
  "sh-illustration-cover-7",
  "sh-illustration-cover-8",
] as const;
export const coverPalette = (cover: number) =>
  palettes[cover - 1] ?? palettes[0];
export function MiniPlaylist() {
  const tracks = [
    ["Velvet Signals", 6, "New"],
    ["Blue Static", 2, "New"],
    ["Night Window", 8, "New"],
    ["Midnight Drive", 1, ""],
    ["Golden Hour", 3, ""],
  ] as const;
  return (
    <div className="sh-mini-playlist">
      <h4 className="sh-mini-playlist-title">Everyday mix</h4>
      <ul
        className="sh-mini-playlist-tracks"
        role="list"
        aria-label="After adding 3 songs: Everyday mix"
      >
        {tracks.map(([name, cover, tag]) => (
          <li
            key={name}
            className={`sh-song-tile${tag ? " sh-song-tile-highlight" : ""}`}
          >
            <span
              className={`sh-song-cover ${coverPalette(cover)}`}
              aria-hidden="true"
            >
              {name[0]}
            </span>
            <div className="sh-song-copy">
              <p className="sh-song-name">{name}</p>
              {tag && (
                <span className="sh-song-tag sh-song-tag-highlight">{tag}</span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
