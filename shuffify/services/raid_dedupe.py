"""
Chain-wide deduplication for the raid system.

Builds an exclusion set from the entire linked playlist chain:
target + raid playlist + archive + dismissed tracks.
"""

import logging

from shuffify.models.db import (
    PlaylistPair,
    RaidPlaylistLink,
    PendingRaidTrack,
)
from shuffify.enums import PendingRaidStatus

logger = logging.getLogger(__name__)


def build_full_exclusion_set(api, target_id, user_id):
    """Build exclusion set across the full playlist chain.

    Checks: target + raid playlist + archive + dismissed.

    Args:
        api: SpotifyAPI instance.
        target_id: Target playlist Spotify ID.
        user_id: Internal database user ID.

    Returns:
        Tuple of (exclusion_set, target_track_count).
        target_track_count allows callers to avoid a
        redundant API call for the response total.
    """
    exclusion = set()
    target_track_count = 0

    try:
        target_tracks = api.get_playlist_tracks(target_id)
        target_track_count = len(target_tracks)
        exclusion |= {
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        }
    except Exception as e:
        logger.warning(
            "Could not fetch target tracks for "
            "dedupe: %s", e
        )

    try:
        link = RaidPlaylistLink.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_id,
        ).first()
        if link:
            raid_tracks = api.get_playlist_tracks(
                link.raid_playlist_id
            )
            exclusion |= {
                t.get("uri")
                for t in raid_tracks
                if t.get("uri")
            }
    except Exception as e:
        logger.warning(
            "Could not fetch raid playlist tracks "
            "for dedupe: %s", e
        )

    try:
        pair = PlaylistPair.query.filter_by(
            user_id=user_id,
            production_playlist_id=target_id,
        ).first()
        if pair:
            archive_tracks = api.get_playlist_tracks(
                pair.archive_playlist_id
            )
            exclusion |= {
                t.get("uri")
                for t in archive_tracks
                if t.get("uri")
            }
    except Exception as e:
        logger.warning(
            "Could not fetch archive tracks for "
            "dedupe: %s", e
        )

    try:
        dismissed = PendingRaidTrack.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_id,
            status=PendingRaidStatus.DISMISSED,
        ).all()
        exclusion |= {t.track_uri for t in dismissed}
    except Exception as e:
        logger.warning(
            "Could not fetch dismissed tracks for "
            "dedupe: %s", e
        )

    return exclusion, target_track_count
