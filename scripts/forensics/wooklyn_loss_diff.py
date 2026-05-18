"""
WOOKLYN loss-diff: which tracks lived in a snapshot but are gone now?

Reads every PlaylistSnapshot for a target playlist, unions and de-dupes
the track URIs, then diffs against the current state of:
  1. The production playlist (live, via Spotify API).
  2. The paired archive playlist (live, via Spotify API + PlaylistPair).

Emits:
  - The full snapshot-union URI set (sorted, unique).
  - The URIs still present in production OR archive (i.e. NOT lost).
  - The URIs missing from BOTH (i.e. lost — candidates for re-injection).
  - Track names/artists for the missing set so the user can sanity-check
    before any re-injection.

Read-only. Does not mutate the database or any Spotify playlist.

Usage:
    # 1) Source DATABASE_URL and Spotify creds from .env, then:
    ./venv/bin/python scripts/forensics/wooklyn_loss_diff.py \\
        --user-id 1 --playlist-id 6IaGaOajCMoxpCMZTCpcru \\
        --output /tmp/wooklyn_loss.json

    # 2) Re-inject the missing URIs (separate manual step, NOT this script):
    #    Use the JSON output + a flask-shell snippet, or the workshop UI.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Set

# Add repo root to sys.path so we can import shuffify modules.
REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from shuffify import create_app  # noqa: E402
from shuffify.models.db import (  # noqa: E402
    db,
    PlaylistSnapshot,
    PlaylistPair,
    User,
)
from shuffify.services.token_service import TokenService  # noqa: E402
from shuffify.spotify.auth import (  # noqa: E402
    SpotifyAuthManager,
    TokenInfo,
)
from shuffify.spotify.api import SpotifyAPI  # noqa: E402
from shuffify.spotify.credentials import (  # noqa: E402
    SpotifyCredentials,
)
from shuffify.shuffle_algorithms.utils import extract_uris  # noqa: E402


def _build_api(user: User, app_config) -> SpotifyAPI:
    """Build a SpotifyAPI client from the user's stored refresh token."""
    if not user.encrypted_refresh_token:
        raise SystemExit(
            f"User {user.id} has no encrypted_refresh_token; can't "
            f"call Spotify."
        )
    TokenService.initialize(app_config["SECRET_KEY"])
    refresh_token = TokenService.decrypt_token(
        user.encrypted_refresh_token
    )
    credentials = SpotifyCredentials.from_flask_config(app_config)
    auth_manager = SpotifyAuthManager(credentials)
    token_info = TokenInfo(
        access_token="expired_placeholder",
        token_type="Bearer",
        expires_at=0,
        refresh_token=refresh_token,
    )
    return SpotifyAPI(token_info, auth_manager, auto_refresh=True)


def _snapshot_union(user_id: int, playlist_id: str) -> Set[str]:
    """Union of all unique track URIs across every snapshot for a
    playlist, regardless of snapshot_type or age."""
    snapshots = (
        PlaylistSnapshot.query.filter_by(
            user_id=user_id, playlist_id=playlist_id
        )
        .order_by(PlaylistSnapshot.created_at.asc())
        .all()
    )
    union: Set[str] = set()
    for snap in snapshots:
        union.update(snap.track_uris or [])
    return union, snapshots


def _live_uris(api: SpotifyAPI, playlist_id: str) -> List[str]:
    """Cache-bypass fetch of current playlist track URIs."""
    items = api.get_playlist_tracks(playlist_id, skip_cache=True)
    return extract_uris(items or [])


def _resolve_archive_id(
    user_id: int, production_playlist_id: str
) -> str:
    """Return the archive playlist id paired with the given
    production playlist, or '' if no pair exists."""
    pair = (
        PlaylistPair.query.filter_by(
            user_id=user_id,
            production_playlist_id=production_playlist_id,
        ).first()
    )
    return pair.archive_playlist_id if pair else ""


def _track_metadata(
    api: SpotifyAPI, uris: List[str]
) -> Dict[str, Dict[str, str]]:
    """Fetch name + first artist for each URI. Spotify's
    /v1/tracks endpoint takes up to 50 ids per call."""
    if not uris:
        return {}
    ids = [u.split(":")[-1] for u in uris]
    out: Dict[str, Dict[str, str]] = {}
    BATCH = 50
    for i in range(0, len(ids), BATCH):
        batch = ids[i : i + BATCH]
        resp = api._http.get(
            "/tracks", params={"ids": ",".join(batch)}
        )
        for track in resp.get("tracks", []):
            if not track:
                continue
            uri = track.get("uri") or (
                f"spotify:track:{track.get('id')}"
            )
            artists = track.get("artists") or []
            out[uri] = {
                "name": track.get("name", "(unknown)"),
                "artist": (
                    artists[0].get("name") if artists else ""
                ),
            }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--user-id", type=int, required=True,
        help="Internal users.id (NOT the Spotify ID)",
    )
    parser.add_argument(
        "--playlist-id", required=True,
        help="Spotify playlist id of the production playlist",
    )
    parser.add_argument(
        "--archive-playlist-id", default="",
        help=(
            "Override the archive playlist id. Defaults to the "
            "PlaylistPair lookup."
        ),
    )
    parser.add_argument(
        "--output", default="",
        help=(
            "If set, write the full diff JSON to this path. "
            "Always also prints a summary to stdout."
        ),
    )
    parser.add_argument(
        "--no-metadata", action="store_true",
        help=(
            "Skip fetching track names/artists for the missing "
            "set (much faster for large losses)."
        ),
    )
    args = parser.parse_args()

    app = create_app("production")
    with app.app_context():
        user = db.session.get(User, args.user_id)
        if not user:
            raise SystemExit(f"User {args.user_id} not found")

        # ----- snapshot union -----
        snapshot_union, snapshots = _snapshot_union(
            args.user_id, args.playlist_id
        )
        if not snapshots:
            raise SystemExit(
                f"No snapshots found for user {args.user_id}, "
                f"playlist {args.playlist_id}. Nothing to diff."
            )

        # ----- live production + archive -----
        api = _build_api(user, app.config)
        production_uris = _live_uris(api, args.playlist_id)
        production_set = set(production_uris)

        archive_id = (
            args.archive_playlist_id
            or _resolve_archive_id(
                args.user_id, args.playlist_id
            )
        )
        archive_uris: List[str] = []
        archive_set: Set[str] = set()
        if archive_id:
            archive_uris = _live_uris(api, archive_id)
            archive_set = set(archive_uris)

        # ----- the diff -----
        present_anywhere = production_set | archive_set
        missing_uris = sorted(snapshot_union - present_anywhere)
        present_in_prod_only = sorted(
            (snapshot_union & production_set) - archive_set
        )
        present_in_archive_only = sorted(
            (snapshot_union & archive_set) - production_set
        )
        present_in_both = sorted(
            snapshot_union & production_set & archive_set
        )

        # ----- enrich missing with names/artists -----
        metadata = (
            {} if args.no_metadata
            else _track_metadata(api, missing_uris)
        )

        # ----- output -----
        result = {
            "generated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "user_id": args.user_id,
            "playlist_id": args.playlist_id,
            "archive_playlist_id": archive_id,
            "snapshot_count": len(snapshots),
            "snapshot_span": {
                "earliest": (
                    snapshots[0].created_at.isoformat()
                    if snapshots else None
                ),
                "latest": (
                    snapshots[-1].created_at.isoformat()
                    if snapshots else None
                ),
            },
            "counts": {
                "snapshot_union": len(snapshot_union),
                "production_live": len(production_set),
                "archive_live": len(archive_set),
                "missing_from_both": len(missing_uris),
                "in_production_only": len(present_in_prod_only),
                "in_archive_only": len(present_in_archive_only),
                "in_both": len(present_in_both),
            },
            "missing_uris": missing_uris,
            "missing_with_metadata": [
                {
                    "uri": u,
                    "name": metadata.get(u, {}).get("name", ""),
                    "artist": (
                        metadata.get(u, {}).get("artist", "")
                    ),
                }
                for u in missing_uris
            ],
            "in_production_only_uris": present_in_prod_only,
            "in_archive_only_uris": present_in_archive_only,
        }

        # ----- summary to stdout -----
        print("=" * 70)
        print(f"WOOKLYN loss-diff for playlist {args.playlist_id}")
        print(f"  snapshots used: {len(snapshots)}")
        if snapshots:
            print(
                f"  snapshot span:  "
                f"{snapshots[0].created_at.isoformat()} → "
                f"{snapshots[-1].created_at.isoformat()}"
            )
        print("=" * 70)
        print(
            f"Snapshot union (de-duped):       "
            f"{len(snapshot_union):>5}"
        )
        print(
            f"Current production live:         "
            f"{len(production_set):>5}"
        )
        print(
            f"Current archive live ({archive_id or '—'}): "
            f"{len(archive_set):>5}"
        )
        print("-" * 70)
        print(
            f"In production AND archive:       "
            f"{len(present_in_both):>5}"
        )
        print(
            f"In production only:              "
            f"{len(present_in_prod_only):>5}"
        )
        print(
            f"In archive only:                 "
            f"{len(present_in_archive_only):>5}"
        )
        print(
            f"MISSING FROM BOTH (potentially lost): "
            f"{len(missing_uris):>5}"
        )
        print("=" * 70)

        if missing_uris and not args.no_metadata:
            print("\nFirst 20 missing tracks:")
            for entry in result["missing_with_metadata"][:20]:
                print(
                    f"  {entry['artist']:<30}  "
                    f"{entry['name']:<40}  {entry['uri']}"
                )
            if len(missing_uris) > 20:
                print(
                    f"  ...and "
                    f"{len(missing_uris) - 20} more "
                    f"(see --output JSON for full list)"
                )

        if args.output:
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2)
            print(f"\nFull diff written to: {args.output}")


if __name__ == "__main__":
    main()
