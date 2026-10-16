"""Local-only capture of owned playlist asset configuration through an explicit session."""

from pydantic import ValidationError
from sqlalchemy import select

from shuffify.models.db import PlaylistPair, RaidPlaylistLink, User
from shuffify.services.playlist_setup_service import ALREADY, DESCRIPTIONS

from .asset_schemas import AssetCommand


class AssetCaptureRejected(ValueError):
    """An authored rejection that does not disclose private storage details."""

    def __init__(self, code, message, http_status=409, **details):
        self.code, self.message, self.http_status = code, message, http_status
        self.details = details
        super().__init__(message)


def _association(storage, user_id, target_id, purpose, *, lock=False):
    model, target, asset = (
        (PlaylistPair, PlaylistPair.production_playlist_id, "archive_playlist_id")
        if purpose == "archive"
        else (RaidPlaylistLink, RaidPlaylistLink.target_playlist_id, "raid_playlist_id")
    )
    query = select(model.__table__).where(model.user_id == user_id, target == target_id).order_by(model.id)
    if lock:
        query = query.with_for_update()
    row = storage.execute(query).mappings().one_or_none()
    return ({"id": row["id"], "playlist_id": row[asset]} if row else None), row


def _owner(storage, user_id):
    if type(user_id) is not int or user_id < 1 or storage.scalar(select(User.id).where(User.id == user_id)) is None:
        raise AssetCaptureRejected("user_not_found", "The authenticated user is unavailable.", 404)


def _changed():
    return AssetCaptureRejected("association_changed", "The linked playlist changed. Review its settings again.")


def capture_asset(
    storage,
    user_id,
    playlist_id,
    *,
    kind,
    purpose=None,
    create_new=True,
    name=None,
    target_playlist_name=None,
    asset_playlist_id=None,
    asset_playlist_name=None,
    drip_count=3,
    drip_enabled=False,
):
    """Freeze the requested asset and the exact owned existing-link identity without autoflush."""
    with storage.no_autoflush:
        _owner(storage, user_id)
        purpose = purpose or ("collection" if kind == "raid_link_create" else "archive")
        if purpose not in {"archive", "collection"}:
            raise AssetCaptureRejected("invalid_request", "Choose an archive or collection.", 400)
        binding, row = _association(storage, user_id, playlist_id, purpose)
        if binding is not None and kind != "raid_link_create":
            raise AssetCaptureRejected(
                "already_paired" if purpose == "archive" else "already_linked",
                ALREADY[purpose][1],
                playlist={
                    "id": binding["playlist_id"],
                    "name": row["archive_playlist_name" if purpose == "archive" else "raid_playlist_name"]
                    or binding["playlist_id"],
                },
            )
        if binding is not None:
            create_new, asset_playlist_id = False, binding["playlist_id"]
            asset_playlist_name = row["raid_playlist_name"]
        if kind == "pair_create":
            authored_name = f"{target_playlist_name or 'Playlist'} [Archive]"
            description = "Archive playlist for removed tracks"
        elif kind == "raid_link_create":
            authored_name = f"{target_playlist_name or playlist_id} [Raids]"
            description = "Raid staging playlist for incoming tracks"
        else:
            authored_name, description = name, DESCRIPTIONS[purpose]
        if not create_new:
            authored_name = asset_playlist_name or asset_playlist_id
        try:
            return AssetCommand(
                kind=kind,
                playlist_id=playlist_id,
                purpose=purpose,
                create_new=create_new,
                name=authored_name,
                description=description,
                target_playlist_name=target_playlist_name,
                asset_playlist_id=asset_playlist_id,
                asset_playlist_name=asset_playlist_name,
                existing_binding=binding,
                drip_count=drip_count,
                drip_enabled=drip_enabled,
            )
        except ValidationError:
            raise AssetCaptureRejected("invalid_request", "Review the playlist creation settings.", 400) from None


def recapture_asset(storage, user_id, command, *, lock=False):
    """Refuse association identity drift before provider work or the fenced local receipt."""
    with storage.no_autoflush:
        _owner(storage, user_id)
        current, _row = _association(storage, user_id, command.playlist_id, command.purpose, lock=lock)
        expected = command.existing_binding.model_dump(mode="json") if command.existing_binding else None
        if current != expected:
            raise _changed()
    return command
