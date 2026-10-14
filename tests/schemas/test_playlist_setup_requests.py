"""Playlist setup request bodies: strict, closed and one change per subject."""

import pytest
from pydantic import ValidationError

from shuffify.schemas import CreateSetupPlaylistRequest, SetupImpactRequest, SetupSaveRequest
from shuffify.schemas.playlist_setup_requests import DRIP_COUNT_RANGE, MAX_CHANGES, RAID_COUNT_RANGE, is_playlist_id
from shuffify.services.playlist_setup_service import LIMITS

REVISION = "a" * 64
DIGESTS = {"sources": "1" * 64, "collection": "2" * 64, "archive": "3" * 64, "routines": "4" * 64}

# One valid example per op, with the subject it changes.
EXAMPLES = [
    ({"op": "add_playlist_source", "playlist_id": "Abc123"}, "source:Abc123"),
    ({"op": "add_search_source", "search_query": "lofi beats"}, "search:lofi beats"),
    ({"op": "remove_source", "source_id": 4}, "source-id:4"),
    ({"op": "set_source_count", "source_id": 5, "raid_count": 100}, "source-id:5"),
    ({"op": "remove_routine_source", "playlist_id": "legacy id?"}, "routine-source:legacy id?"),
    ({"op": "link_collection", "playlist_id": "Col1"}, "collection"),
    ({"op": "replace_collection", "playlist_id": "Col2"}, "collection"),
    ({"op": "unlink_collection"}, "collection"),
    ({"op": "set_drip", "drip_enabled": False}, "drip"),
    ({"op": "pair_archive", "playlist_id": "Arc1"}, "archive"),
    ({"op": "replace_archive", "playlist_id": "Arc2"}, "archive"),
    ({"op": "unpair_archive"}, "archive"),
    ({"op": "set_auto_archive", "value": True}, "auto-archive"),
]


def impact(*changes, **extra):
    return SetupImpactRequest.model_validate({"configuration_revision": REVISION, "changes": list(changes), **extra})


def refused(*changes, **extra):
    with pytest.raises(ValidationError) as error:
        impact(*changes, **extra)
    return str(error.value)


@pytest.mark.parametrize("change, subject", EXAMPLES, ids=[c["op"] for c, _ in EXAMPLES])
def test_every_op_parses_and_names_its_subject(change, subject):
    parsed = impact(change).changes[0]
    assert parsed.op == change["op"]
    assert parsed.subject == subject
    assert parsed.model_dump(exclude_none=True) == change


def test_a_list_can_hold_every_op_once():
    skipped = ("replace_collection", "unlink_collection", "replace_archive", "unpair_archive")
    changes = [change for change, _ in EXAMPLES if change["op"] not in skipped]
    assert [change.op for change in impact(*changes).changes] == [change["op"] for change in changes]


def test_search_text_is_stripped_and_a_blank_name_is_no_name():
    change = impact({"op": "add_search_source", "search_query": "  lofi  ", "source_name": "   "}).changes[0]
    assert (change.search_query, change.source_name, change.subject) == ("lofi", None, "search:lofi")
    named = impact({"op": "add_search_source", "search_query": "q", "source_name": " Chill "}).changes[0]
    assert named.source_name == "Chill"


@pytest.mark.parametrize("change", [
    {"op": "add_playlist_source", "playlist_id": ""},
    {"op": "add_playlist_source", "playlist_id": "has space"},
    {"op": "add_playlist_source", "playlist_id": "https://open.spotify.com/playlist/abc"},
    {"op": "add_playlist_source", "playlist_id": "a" * 256},
    {"op": "add_playlist_source", "playlist_id": 123},
    {"op": "add_search_source", "search_query": ""},
    {"op": "add_search_source", "search_query": "   "},
    {"op": "add_search_source", "search_query": "q" * 501},
    {"op": "add_search_source", "search_query": "q", "source_name": "n" * 256},
    {"op": "remove_source", "source_id": 0},
    {"op": "remove_source", "source_id": -1},
    {"op": "remove_source", "source_id": "4"},
    {"op": "remove_source", "source_id": 4.0},
    {"op": "remove_source", "source_id": True},
    {"op": "set_source_count", "source_id": 1, "raid_count": 0},
    {"op": "set_source_count", "source_id": 1, "raid_count": 101},
    {"op": "set_source_count", "source_id": 1, "raid_count": "5"},
    {"op": "set_source_count", "source_id": 1},
    {"op": "remove_routine_source", "playlist_id": ""},
    {"op": "remove_routine_source", "playlist_id": "x" * 256},
    {"op": "link_collection"},
    {"op": "replace_collection", "playlist_id": "bad/id"},
    {"op": "set_drip"},
    {"op": "set_drip", "drip_enabled": None, "drip_count": None},
    {"op": "set_drip", "drip_count": 0},
    {"op": "set_drip", "drip_count": 51},
    {"op": "set_drip", "drip_enabled": 1},
    {"op": "set_drip", "drip_enabled": "true"},
    {"op": "pair_archive", "playlist_id": None},
    {"op": "set_auto_archive"},
    {"op": "set_auto_archive", "value": 0},
    {"op": "rotate_now"},
    {"playlist_id": "Abc"},
    {"op": None},
    "add_playlist_source",
])
def test_out_of_bounds_or_loose_changes_are_refused(change):
    refused(change)


@pytest.mark.parametrize("change", [
    {"op": "add_playlist_source", "playlist_id": "Abc", "name": "Mine"},
    {"op": "unlink_collection", "playlist_id": "Abc"},
    {"op": "set_drip", "drip_enabled": True, "force": True},
    {"op": "set_auto_archive", "value": True, "playlist_id": "Abc"},
])
def test_unknown_change_fields_are_refused(change):
    assert "Extra inputs are not permitted" in refused(change)


def test_unknown_body_fields_are_refused():
    assert "Extra inputs are not permitted" in refused(EXAMPLES[0][0], dry_run=True)


@pytest.mark.parametrize("pair", [
    [{"op": "add_playlist_source", "playlist_id": "Abc"}, {"op": "add_playlist_source", "playlist_id": "Abc"}],
    [{"op": "add_search_source", "search_query": "lofi"}, {"op": "add_search_source", "search_query": " lofi "}],
    [{"op": "remove_source", "source_id": 3}, {"op": "set_source_count", "source_id": 3, "raid_count": 2}],
    [{"op": "link_collection", "playlist_id": "A"}, {"op": "unlink_collection"}],
    [{"op": "replace_archive", "playlist_id": "A"}, {"op": "pair_archive", "playlist_id": "B"}],
    [{"op": "set_drip", "drip_count": 2}, {"op": "set_drip", "drip_enabled": True}],
    [{"op": "remove_routine_source", "playlist_id": "x"}, {"op": "remove_routine_source", "playlist_id": "x"}],
])
def test_two_changes_to_one_subject_are_refused(pair):
    assert "both change" in refused(*pair)


def test_distinct_subjects_for_the_same_playlist_are_allowed():
    request = impact(
        {"op": "remove_source", "source_id": 3},
        {"op": "add_playlist_source", "playlist_id": "Abc"},
        {"op": "remove_routine_source", "playlist_id": "Abc"},
        {"op": "set_source_count", "source_id": 4, "raid_count": 7},
    )
    assert len({change.subject for change in request.changes}) == 4


def test_the_change_list_holds_one_to_twenty_changes():
    refused()
    many = [{"op": "add_playlist_source", "playlist_id": f"P{i}"} for i in range(MAX_CHANGES + 1)]
    assert len(impact(*many[:MAX_CHANGES]).changes) == MAX_CHANGES
    refused(*many)
    with pytest.raises(ValidationError):
        SetupImpactRequest.model_validate({"configuration_revision": REVISION})
    with pytest.raises(ValidationError):
        SetupImpactRequest.model_validate({"configuration_revision": REVISION, "changes": {"op": "unpair_archive"}})


@pytest.mark.parametrize("revision", [None, "", "A" * 64, "a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n", 123])
def test_the_revision_is_a_64_hex_digest(revision):
    with pytest.raises(ValidationError):
        SetupImpactRequest.model_validate({"configuration_revision": revision, "changes": [EXAMPLES[0][0]]})


@pytest.mark.parametrize("limit, change, bounds", [
    ("raid_count", lambda count: {"op": "set_source_count", "source_id": 1, "raid_count": count}, RAID_COUNT_RANGE),
    ("drip_count", lambda count: {"op": "set_drip", "drip_count": count}, DRIP_COUNT_RANGE),
])
def test_counts_are_bounded_by_the_ranges_the_context_reports(limit, change, bounds):
    low, high = bounds
    for count in (low, high):
        impact(change(count))
    for count in (low - 1, high + 1):
        refused(change(count))
    assert LIMITS[limit] == [low, high]


@pytest.mark.parametrize("value, expected", [
    ("Abc123", True), ("a" * 255, True),
    ("", False), ("a" * 256, False), ("has space", False), ("bad/id", False), ("abc\n", False), ("ab-c", False),
    (123, False), (None, False),
])
def test_a_playlist_id_is_checked_as_the_change_bodies_check_it(value, expected):
    assert is_playlist_id(value) is expected
    try:
        impact({"op": "link_collection", "playlist_id": value})
    except ValidationError:
        accepted = False
    else:
        accepted = True
    assert accepted is expected


@pytest.mark.parametrize("body", [[], [EXAMPLES[0][0]], "changes", 7, None, True])
def test_non_object_bodies_are_refused(body):
    for schema in (SetupImpactRequest, SetupSaveRequest, CreateSetupPlaylistRequest):
        with pytest.raises(ValidationError):
            schema.model_validate(body)


def test_section_digests_are_optional_closed_and_strict():
    assert impact(EXAMPLES[0][0]).section_digests is None
    assert impact(EXAMPLES[0][0], section_digests=DIGESTS).section_digests.routines == "4" * 64
    refused(EXAMPLES[0][0], section_digests={**DIGESTS, "extra": "5" * 64})
    refused(EXAMPLES[0][0], section_digests={k: v for k, v in DIGESTS.items() if k != "archive"})
    refused(EXAMPLES[0][0], section_digests={**DIGESTS, "sources": "x"})


def test_save_requires_an_acknowledgment_of_any_shape():
    body = {"configuration_revision": REVISION, "changes": [EXAMPLES[0][0]]}
    with pytest.raises(ValidationError):
        SetupSaveRequest.model_validate(body)
    for token in ({"version": 1, "issued_at": 1, "signature": "f" * 64}, "token", None, []):
        assert SetupSaveRequest.model_validate({**body, "acknowledgment": token}).acknowledgment == token
    with pytest.raises(ValidationError):
        SetupSaveRequest.model_validate({**body, "acknowledgment": {}, "changes": []})


def test_creation_names_a_purpose_and_a_trimmed_name():
    request = CreateSetupPlaylistRequest.model_validate({"purpose": "archive", "name": "  Old Mix  "})
    assert (request.purpose, request.name) == ("archive", "Old Mix")
    assert CreateSetupPlaylistRequest.model_validate({"purpose": "collection", "name": "n" * 100}).name == "n" * 100


@pytest.mark.parametrize("body", [
    {"purpose": "collection"},
    {"name": "Mix"},
    {"purpose": "inbox", "name": "Mix"},
    {"purpose": "collection", "name": ""},
    {"purpose": "collection", "name": "   "},
    {"purpose": "collection", "name": "n" * 101},
    {"purpose": "collection", "name": 5},
    {"purpose": "collection", "name": "Mix", "public": True},
])
def test_creation_bodies_are_strict(body):
    with pytest.raises(ValidationError):
        CreateSetupPlaylistRequest.model_validate(body)
