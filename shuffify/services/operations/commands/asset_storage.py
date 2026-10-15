"""Required association receipts and optional activity inside the fenced transaction."""

from copy import deepcopy

from sqlalchemy import select

from shuffify.enums import ActivityType
from shuffify.models.db import PlaylistPair, RaidPlaylistLink
from shuffify.services.operations.bookkeeping import OperationBookkeeping

from .asset_capture import recapture_asset


def commit_asset(storage, user_id, operation_id, command, applied):
    """Flush one owned association; the caller commits its receipt and acceptance together."""
    recapture_asset(storage, user_id, command, lock=True)
    result = deepcopy(applied["result"])
    playlist = result.get("playlist")
    if (
        not isinstance(playlist, dict)
        or not isinstance(playlist.get("id"), str)
        or not playlist["id"]
        or playlist["id"] == command.playlist_id
        or command.create_new
        and result.get("privacy_confirmed") is not True
        or not command.create_new
        and playlist["id"] != command.asset_playlist_id
    ):
        raise ValueError("The asset's verified identity or privacy is unavailable")
    if command.already_linked:
        row = storage.scalar(
            select(RaidPlaylistLink).where(
                RaidPlaylistLink.id == command.existing_binding.id,
                RaidPlaylistLink.user_id == user_id,
                RaidPlaylistLink.target_playlist_id == command.playlist_id,
            )
        )
        result.update(success=True, raid_link=row.to_dict(), message="Raid playlist already linked.")
        return {**applied, "result": result, "receipt": {"association_id": row.id, "activity": None}}
    row = None
    if command.kind == "pair_create":
        row = PlaylistPair(
            user_id=user_id,
            production_playlist_id=command.playlist_id,
            production_playlist_name=command.target_playlist_name,
            archive_playlist_id=playlist["id"],
            archive_playlist_name=playlist.get("name") or command.name,
            auto_archive_on_remove=True,
        )
        storage.add(row)
        storage.flush()
        result.update(pair=row.to_dict(), message="Archive pair created")
        activity_type = ActivityType.PAIR_CREATE
        description = f"Paired with archive '{row.archive_playlist_name}'"
    elif command.kind == "raid_link_create":
        row = RaidPlaylistLink(
            user_id=user_id,
            target_playlist_id=command.playlist_id,
            target_playlist_name=command.target_playlist_name,
            raid_playlist_id=playlist["id"],
            raid_playlist_name=playlist.get("name") or command.name,
            drip_count=command.drip_count,
            drip_enabled=command.drip_enabled,
        )
        storage.add(row)
        storage.flush()
        result.update(raid_link=row.to_dict(), message="Raid playlist linked.")
        activity_type = ActivityType.RAID_LINK_CREATE
        description = f"Created raid link: '{row.raid_playlist_name}'"
    else:
        result["message"] = "Playlist created. Save its settings to link it."
        activity_type = ActivityType.PLAYLIST_SETUP_CREATE
        description = f"Created {command.purpose} playlist '{playlist.get('name') or command.name}' in Spotify"
    book = OperationBookkeeping(storage, user_id, operation_id)
    activity = book.optional(
        lambda: book.activity(
            activity_type,
            description,
            playlist_id=command.playlist_id,
            playlist_name=command.target_playlist_name,
            metadata={
                "operation_id": operation_id,
                "purpose": command.purpose,
                "playlist_id": playlist["id"],
                "privacy_confirmed": result.get("privacy_confirmed"),
            },
        ),
        fallback={"status": "failed", "error_code": "activity_record_failed"},
    )
    result.update(success=True, association_status="complete" if row else "not_required")
    return {**applied, "result": result, "receipt": {"association_id": row.id if row else None, "activity": activity}}
