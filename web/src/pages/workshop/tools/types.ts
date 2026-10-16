import type { WorkshopTrack } from "../../../api/contracts.ts";
export interface TrackLock {
  id: number;
  user_id: number;
  spotify_playlist_id: string;
  track_uri: string;
  position: number;
  lock_tier: "standard" | "super";
  created_at: string | null;
  expires_at: string | null;
}
export interface Snapshot {
  id: number;
  user_id: number;
  playlist_id: string;
  playlist_name: string;
  track_uris: string[];
  track_count: number;
  snapshot_type: string;
  trigger_description: string | null;
  created_at: string | null;
}
export interface ExternalPlaylist {
  id: string;
  name: string;
  owner_id: string;
  description: string | null;
  track_count: number | null;
}
export interface PlaylistChoice {
  id: string;
  name: string;
  owner_display_name: string;
  image_url: string | null;
  total_tracks: number | null;
}
export type ExternalIntent = { url: string } | { query: string };
export type ExternalResult =
  | { mode: "tracks"; playlist: ExternalPlaylist; tracks: WorkshopTrack[] }
  | {
      mode: "restricted";
      playlist: ExternalPlaylist;
      tracks: [];
      message: string;
      suggested_search: string;
    }
  | { mode: "search"; playlists: PlaylistChoice[] };
export interface SearchPage {
  tracks: WorkshopTrack[];
  query: string;
  offset: number;
  limit: number;
}
export interface PreviewResult {
  uris: string[];
  algorithm: string;
}
export interface PreviewBody {
  algorithm: string;
  tracks: readonly unknown[];
  locked_positions?: Readonly<Record<number, string>>;
  [key: string]: unknown;
}
