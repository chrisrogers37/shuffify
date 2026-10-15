export const uri = (n) => `spotify:track:${String(n).padStart(22, "0")}`;
export const track = (n, added = "2026-01-01T00:00:00Z") => ({
  id: String(n).padStart(22, "0"),
  uri: uri(n),
  name: `Track ${n}`,
  artists: ["Example Artist"],
  artist_urls: [],
  duration_ms: 180000,
  is_local: false,
  added_at: added,
  album_name: "Example Album",
  album_image_url: null,
  track_url: `https://open.spotify.com/track/${String(n).padStart(22, "0")}`,
});
export const context = () => ({
  success: true,
  playlist: {
    id: "target",
    name: "Evening Collection",
    owner_id: "alice",
    description: "An example playlist",
    total_tracks: 3,
    public: false,
    audio_features: {},
  },
  tracks: [track(1, "first"), track(2), track(1, "second")],
  algorithms: [],
  upstream_sources: {},
  previous: null,
  next: null,
  recently_loaded: [],
});
export const signed = (actor) => ({
  success: true,
  signed_in: true,
  user: { id: actor, display_name: actor, image_url: null },
  csrf_token: "csrf",
  notice: null,
});
export const operation = (id = 2001, state = "queued", result = null) => ({
  id,
  kind: "workshop_commit",
  state,
  targets: ["target"],
  started_at: null,
  completed_at: null,
  progress: {},
  result,
  recovery: { available: false, action: null, reason: null },
});
export const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
