"""
Shuffle routes: execute shuffle and undo operations.
"""

import logging

from flask import session, jsonify

from shuffify.routes import main, require_auth, json_error, json_success
from shuffify.services import (
    PlaylistService,
    ShuffleService,
    StateService,
    PlaylistUpdateError,
)
from shuffify.schemas import parse_shuffle_request
from flask import request

logger = logging.getLogger(__name__)


@main.route("/shuffle/<playlist_id>", methods=["POST"])
def shuffle(playlist_id):
    """Shuffle a playlist using the selected algorithm."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    shuffle_request = parse_shuffle_request(
        request.form.to_dict()
    )

    algorithm = ShuffleService.get_algorithm(
        shuffle_request.algorithm
    )
    params = shuffle_request.get_algorithm_params()

    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(
        playlist_id, include_features=False
    )
    playlist_service.validate_playlist_has_tracks(playlist)

    current_uris = [track["uri"] for track in playlist.tracks]

    StateService.ensure_playlist_initialized(
        session, playlist_id, current_uris
    )

    uris_to_shuffle = (
        StateService.get_current_uris(session, playlist_id)
        or current_uris
    )

    tracks_to_shuffle = (
        ShuffleService.prepare_tracks_for_shuffle(
            playlist.tracks, uris_to_shuffle
        )
    )

    shuffled_uris = ShuffleService.execute(
        shuffle_request.algorithm,
        tracks_to_shuffle,
        params,
        spotify_client=client,
    )

    if not ShuffleService.shuffle_changed_order(
        uris_to_shuffle, shuffled_uris
    ):
        return jsonify({
            "success": False,
            "message": (
                "Shuffle did not change the playlist order."
            ),
            "category": "info",
        })

    playlist_service.update_playlist_tracks(
        playlist_id, shuffled_uris
    )

    updated_state = StateService.record_new_state(
        session, playlist_id, shuffled_uris
    )

    updated_playlist = playlist_service.get_playlist(
        playlist_id, include_features=False
    )

    logger.info(
        f"Shuffled playlist {playlist_id} with "
        f"{shuffle_request.algorithm}"
    )

    return json_success(
        f"Playlist shuffled with {algorithm.name}.",
        playlist=updated_playlist.to_dict(),
        playlist_state=updated_state.to_dict(),
    )


@main.route("/undo/<playlist_id>", methods=["POST"])
def undo(playlist_id):
    """Undo the last shuffle for a playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    restore_uris = StateService.undo(session, playlist_id)

    logger.info(
        f"Restoring playlist {playlist_id} with "
        f"{len(restore_uris)} tracks"
    )

    playlist_service = PlaylistService(client)
    try:
        playlist_service.update_playlist_tracks(
            playlist_id, restore_uris
        )
    except PlaylistUpdateError:
        StateService.revert_undo(session, playlist_id)
        raise

    restored_playlist = playlist_service.get_playlist(
        playlist_id, include_features=False
    )
    state_info = StateService.get_state_info(
        session, playlist_id
    )

    logger.info(
        f"Successfully restored playlist {playlist_id}"
    )

    return json_success(
        "Playlist restored successfully.",
        playlist=restored_playlist.to_dict(),
        playlist_state=state_info,
    )
