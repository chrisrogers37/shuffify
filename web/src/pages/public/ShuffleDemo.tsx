import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Button } from "../../components/ui/controls";
import { useAnnouncer } from "../../a11y/Announcer";
import { coverPalette } from "./showcase";
import { motionMilliseconds } from "../../design/motion";
const tracks = [
  {
    name: "Midnight Drive",
    artist: "Luna Wave",
    duration: "3:42",
    initial: "M",
    cover: 1,
    destination: 3,
    position: 753,
  },
  {
    name: "Golden Hour",
    artist: "Amber Skies",
    duration: "3:28",
    initial: "G",
    cover: 3,
    destination: 1,
    position: 754,
  },
  {
    name: "Electric Dreams",
    artist: "Neon Pulse",
    duration: "4:15",
    initial: "E",
    cover: 2,
    destination: 5,
    position: 755,
  },
  {
    name: "Ocean Waves",
    artist: "Deep Current",
    duration: "4:33",
    initial: "O",
    cover: 6,
    destination: 2,
    position: 756,
  },
  {
    name: "Cosmic Drift",
    artist: "Star Wanderer",
    duration: "3:55",
    initial: "C",
    cover: 5,
    destination: 4,
    position: 757,
  },
] as const;
export function ShuffleIcon() {
  return (
    <svg className="sh-landing-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="m16 3 4 4-4 4M4 17h3c4 0 6-10 10-10h3M16 13l4 4-4 4M4 7h3c1.4 0 2.6 1.3 3.7 3M13 14c1.3 1.8 2.5 3 4 3h3" />
    </svg>
  );
}
export function ShuffleDemo() {
  const [enhanced, setEnhanced] = useState(false),
    [shuffled, setShuffled] = useState(false),
    [busy, setBusy] = useState(false);
  const list = useRef<HTMLOListElement>(null),
    rows = useRef(new Map<string, HTMLLIElement>()),
    before = useRef(new Map<string, DOMRect>()),
    animations = useRef<Animation[]>([]),
    run = useRef(0),
    changing = useRef(false);
  const announce = useAnnouncer(),
    eventId = useId();
  useEffect(() => {
    setEnhanced(true);
    return () => {
      run.current++;
      changing.current = false;
      animations.current.forEach((animation) => animation.cancel());
    };
  }, []);
  const complete = (expected: number) => {
    if (run.current !== expected || !changing.current) return;
    animations.current.forEach((animation) => animation.cancel());
    animations.current = [];
    changing.current = false;
    setBusy(false);
    announce(
      shuffled
        ? "Sample playlist reordered. Positions 1–5. Undo is available."
        : "Original sample order restored. Positions 753–757.",
      `${eventId}-landing-demo-${expected}`,
    );
  };
  useLayoutEffect(() => {
    if (!changing.current) return;
    const expected = run.current,
      preference = matchMedia("(prefers-reduced-motion: reduce)");
    const reduce = () => {
      if (preference.matches) complete(expected);
    };
    preference.addEventListener("change", reduce);
    if (!preference.matches && list.current) {
      const style = getComputedStyle(list.current),
        timing = style.getPropertyValue("--sh-motion-reorder").trim(),
        easing = style.getPropertyValue("--sh-motion-ease").trim();
      const duration = motionMilliseconds(timing);
      if (duration > 0 && easing)
        for (const [name, row] of rows.current) {
          const first = before.current.get(name),
            last = row.getBoundingClientRect();
          if (first && (first.x !== last.x || first.y !== last.y)) {
            const animation = row.animate(
              [
                {
                  transform: `translate(${first.x - last.x}px, ${first.y - last.y}px)`,
                },
                { transform: "translate(0, 0)" },
              ],
              { duration, easing },
            );
            animations.current.push(animation);
          }
        }
    }
    if (animations.current.length)
      void Promise.allSettled(
        animations.current.map((animation) => animation.finished),
      ).then(() => complete(expected));
    else complete(expected);
    return () => {
      preference.removeEventListener("change", reduce);
    };
  }, [shuffled]);
  const reorder = (next: boolean) => {
    if (changing.current || next === shuffled) return;
    before.current = new Map(
      [...rows.current].map(([name, row]) => [
        name,
        row.getBoundingClientRect(),
      ]),
    );
    changing.current = true;
    run.current++;
    setBusy(true);
    setShuffled(next);
  };
  const ordered = shuffled
    ? [...tracks].sort((left, right) => left.destination - right.destination)
    : tracks;
  return (
    <div className="sh-landing-stage">
      <p className="sh-landing-demo-label">
        <span>Bring buried favorites back to the top</span>
        <span>Sample playlist</span>
      </p>
      <div
        id="shuffle-demo"
        className="sh-surface sh-landing-demo shuffle-demo-card"
        role="region"
        aria-labelledby="shuffle-demo-title"
        aria-describedby="shuffle-demo-description"
        data-demo-state={shuffled ? "shuffled" : "original"}
      >
        <div className="sh-landing-demo-head">
          <span className="sh-landing-demo-icon" aria-hidden="true">
            <ShuffleIcon />
          </span>
          <div>
            <h2 id="shuffle-demo-title">Everyday mix</h2>
            <p>800-track example · Made for a fresh listen</p>
          </div>
          <span id="shuffle-demo-badge" className="sh-landing-demo-badge">
            {shuffled ? "Reordered" : "Original order"}
          </span>
        </div>
        <div className="sh-landing-demo-method">
          <span>Shuffle method</span>
          <strong>Basic Shuffle</strong>
        </div>
        <p id="shuffle-demo-caption" className="sh-landing-demo-position">
          {shuffled
            ? "A fresh order · positions 1–5"
            : "Buried favorites · positions 753–757"}
        </p>
        <ol
          id="shuffle-track-list"
          ref={list}
          className="sh-landing-demo-rows"
          start={shuffled ? 1 : 753}
          aria-label="Sample tracks in playlist order"
          aria-busy={busy}
        >
          {ordered.map((track, index) => (
            <li
              key={track.name}
              ref={(node) => {
                if (node) rows.current.set(track.name, node);
                else rows.current.delete(track.name);
              }}
              className="sh-landing-demo-row"
              data-demo-track
              data-demo-original-position={track.position}
              data-demo-shuffled-position={track.destination}
            >
              <span className="sh-landing-row-number">
                {shuffled ? String(index + 1).padStart(2, "0") : track.position}
              </span>
              <span
                className={`sh-landing-cover ${coverPalette(track.cover)}`}
                aria-hidden="true"
              >
                {track.initial}
              </span>
              <div className="sh-landing-track">
                <p className="sh-landing-track-name">{track.name}</p>
                <p className="sh-landing-track-artist">{track.artist}</p>
                <p className="sh-landing-track-origin">
                  {shuffled ? "Previously" : "Originally"} #{track.position}
                </p>
              </div>
              <span className="sh-landing-track-duration">
                {track.duration}
              </span>
            </li>
          ))}
        </ol>
        <div
          id="shuffle-demo-controls"
          className="sh-landing-demo-controls"
          hidden={!enhanced}
        >
          <Button
            id="demo-shuffle"
            aria-controls="shuffle-track-list"
            busy={busy}
            aria-disabled={busy || shuffled}
            onClick={() => reorder(true)}
          >
            Preview Shuffle
          </Button>
          <Button
            id="demo-undo"
            variant="secondary"
            aria-controls="shuffle-track-list"
            busy={busy}
            aria-disabled={busy || !shuffled}
            onClick={() => reorder(false)}
          >
            Undo Changes
          </Button>
        </div>
        <p id="shuffle-demo-description" className="sh-landing-demo-foot">
          Fictional tracks · One possible shuffle. Preview a new order, then
          undo.
        </p>
      </div>
      <div className="sh-landing-attribution">
        <p>A new sequence. Always an undo away.</p>
        <a
          href="https://www.spotify.com/"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Visit Spotify"
        >
          <img
            src="/images/spotify-logo-white.svg"
            alt="Spotify"
            width="76"
            height="23"
          />
        </a>
      </div>
    </div>
  );
}
