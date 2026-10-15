"""Archive moves bind the owned pair identity and exact requested occurrences."""

import pytest
from sqlalchemy.orm import Session

from shuffify.models.db import PlaylistPair, User, db

A = "spotify:track:" + "a" * 22


@pytest.fixture
def pair(db_app):
    user = User(spotify_id="example-owner")
    db.session.add(user)
    db.session.flush()
    row = PlaylistPair(user_id=user.id, production_playlist_id="target", archive_playlist_id="archive")
    db.session.add(row)
    db.session.commit()
    return user, row


def test_capture_is_local_strict_and_retains_duplicate_occurrences(pair, monkeypatch):
    from shuffify.services.operations.commands.archive_move import capture_unarchive

    def forbidden(*args, **kwargs):
        pytest.fail("Local archive move capture must not construct credentials or contact Spotify")

    monkeypatch.setattr("shuffify.spotify.api.SpotifyAPI.__init__", forbidden)
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    user, row = pair
    with Session(db.engine) as storage:
        command = capture_unarchive(storage, user.id, "target", [A, A])
    assert command.track_uris == [A, A]
    assert command.reserved_targets == ["archive", "target"]
    assert command.pair_id == row.id
    assert command.kind == "unarchive"


def test_relinked_pair_refuses_captured_identity(pair):
    from shuffify.services.operations.commands.archive_move import ArchiveMoveRejected, capture_unarchive, recapture

    user, row = pair
    with Session(db.engine) as storage:
        captured = capture_unarchive(storage, user.id, "target", [A])
    row.archive_playlist_id = "replacement"
    db.session.commit()
    with Session(db.engine) as storage, pytest.raises(ArchiveMoveRejected, match="changed"):
        recapture(storage, user.id, captured)


@pytest.mark.parametrize(
    "changes",
    [
        {"pair_id": True},
        {"track_uris": []},
        {"track_uris": ["invalid"]},
        {"archive_playlist_id": "target"},
        {"playlist_id": "bad/path"},
        {"extra": "value"},
    ],
)
def test_private_archive_move_schema_rejects_malformed_intent(changes):
    from pydantic import ValidationError

    from shuffify.services.operations.commands.archive_move import UnarchiveCommand

    values = {"pair_id": 1, "playlist_id": "target", "archive_playlist_id": "archive", "track_uris": [A]}
    values.update(changes)
    with pytest.raises(ValidationError):
        UnarchiveCommand.model_validate(values)


@pytest.mark.parametrize("owner,target", [(999, "target"), (1, "other")])
def test_foreign_or_missing_owned_pair_never_grants_access(pair, owner, target):
    from shuffify.services.operations.commands.archive_move import ArchiveMoveRejected, capture_unarchive

    with Session(db.engine) as storage, pytest.raises(ArchiveMoveRejected):
        capture_unarchive(storage, owner, target, [A])
