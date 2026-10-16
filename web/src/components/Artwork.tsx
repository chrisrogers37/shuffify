import { useEffect, useState } from "react";
export function Artwork({
  src,
  name,
  spotifyUrl,
}: {
  src: string | null;
  name: string;
  spotifyUrl: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  const destination = new URL(spotifyUrl);
  if (
    destination.protocol !== "https:" ||
    destination.hostname !== "open.spotify.com"
  )
    throw Error("Artwork requires an official Spotify backlink.");
  return (
    <figure className="sh-artwork" data-spotify-artwork>
      <div className="sh-artwork-frame">
        {src && !failed ? (
          <img
            className="sh-artwork-image"
            src={src}
            alt={name}
            onError={() => setFailed(true)}
          />
        ) : (
          <div
            className="sh-artwork-image sh-artwork-missing"
            role="img"
            aria-label={`No artwork available for ${name}`}
          >
            No artwork
          </div>
        )}
      </div>
      <figcaption>
        <img
          className="sh-spotify-logo"
          src="/images/spotify-logo-white.svg"
          alt="Spotify"
          width="80"
          height="24"
        />
        <a href={destination.href} target="_blank" rel="noopener noreferrer">
          Open {name} on Spotify
        </a>
      </figcaption>
    </figure>
  );
}
