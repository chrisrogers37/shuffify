"""Immutable source task fields are explicit and preserve occurrences."""

import pytest
from pydantic import ValidationError

from shuffify.schemas.workshop_finalize_requests import (
    FinalizeArchiveRequest,
    FinalizeRaidRequest,
    FinalizeRestoreRequest,
)

A = "spotify:track:" + "a" * 22


@pytest.mark.parametrize(
    "schema", [FinalizeArchiveRequest, FinalizeRaidRequest, FinalizeRestoreRequest]
)
@pytest.mark.parametrize("missing", ["source_playlist_id", "target_track_uris"])
def test_captured_identity_and_target_are_required(schema, missing):
    payload = dict(
        source_playlist_id="source123",
        target_track_uris=[],
        track_ids=[1],
        track_uris=[A],
        baseline_track_uris=[],
    )
    del payload[missing]
    with pytest.raises(ValidationError):
        schema(**payload)


def test_only_raid_can_have_explicitly_absent_source():
    assert FinalizeRaidRequest(
        source_playlist_id=None, target_track_uris=[], track_ids=[1]
    )
    for schema in (FinalizeArchiveRequest, FinalizeRestoreRequest):
        with pytest.raises(ValidationError):
            schema(
                source_playlist_id=None,
                target_track_uris=[],
                track_uris=[A],
                baseline_track_uris=[],
            )


def test_append_preserves_all_occurrences_in_order():
    payload = dict(
        source_playlist_id="archive123",
        target_track_uris=[A, A],
        track_uris=[A, A],
        baseline_track_uris=[A, A],
    )
    assert FinalizeArchiveRequest(**payload).model_dump() == payload | {
        "inspect_only": False
    }


def test_append_requires_baseline_even_if_empty():
    with pytest.raises(ValidationError):
        FinalizeArchiveRequest(
            source_playlist_id="archive123", target_track_uris=[], track_uris=[A]
        )


@pytest.mark.parametrize("track_ids", [[], [0], [-1], [True], ["1"]])
def test_raid_ids_are_positive_integers(track_ids):
    with pytest.raises(ValidationError):
        FinalizeRaidRequest(
            source_playlist_id=None, target_track_uris=[], track_ids=track_ids
        )


@pytest.mark.parametrize(
    "field", ["target_track_uris", "track_uris", "baseline_track_uris"]
)
def test_every_uri_payload_is_validated(field):
    payload = dict(
        source_playlist_id="archive123",
        target_track_uris=[],
        track_uris=[A],
        baseline_track_uris=[],
    )
    payload[field] = ["spotify:track:invalid"]
    with pytest.raises(ValidationError):
        FinalizeArchiveRequest(**payload)


def test_inspect_cannot_be_coerced_from_a_truthy_string():
    with pytest.raises(ValidationError):
        FinalizeRaidRequest(
            source_playlist_id=None,
            target_track_uris=[],
            track_ids=[1],
            inspect_only="false",
        )


def test_raid_finalize_accepts_the_entire_captured_inbox():
    ids = list(range(1, 202))
    request = FinalizeRaidRequest(
        source_playlist_id=None, target_track_uris=[], track_ids=ids
    )
    assert request.track_ids == ids
