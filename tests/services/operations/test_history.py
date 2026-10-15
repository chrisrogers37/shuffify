"""Scoped Undo history becomes visible only with a confirmed durable completion."""

from copy import deepcopy

import pytest
from sqlalchemy import create_engine

from shuffify.models.db import db
from shuffify.services.operations.history import HistoryError, HistoryStore, get_history_scope
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.state_service import StaleHistoryError, StateService


@pytest.fixture
def stores(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'history.sqlite'}")
    db.metadata.create_all(engine)
    yield OperationRepository(engine), HistoryStore(engine)
    engine.dispose()


@pytest.fixture
def scope():
    return get_history_scope({})


def entry(*states, index=None):
    return {"states": [list(state) for state in states],
            "current_index": len(states) - 1 if index is None else index}


def accepted(repository, *, target="playlist", key=None, user_id=1):
    operation = repository.enqueue(kind="shuffle", user_id=user_id, targets=[target], payload={}, request_key=key)
    return operation, repository.claim(operation_id=operation.id)


def complete(stores, scope, *, legacy=None, after=("after",), state="succeeded", target="playlist", user_id=1):
    repository, history = stores
    operation, lease = accepted(repository, target=target, user_id=user_id)
    session = history.session_for(user_id, scope, [target], legacy={target: legacy} if legacy is not None else None)
    StateService.record_verified_change(session, target, before_uris=["before"], after_uris=list(after))
    journal = history.stage({"version": 1, "progress": {"batches_done": 1}}, session, state=state, confirmed=True)
    repository.checkpoint(lease, journal)
    repository.finish(lease, state)
    return operation.id, deepcopy(session["playlist_states"][target])


def test_scope_is_opaque_server_generated_and_reused():
    session = {"spotify_token": "private-token"}
    first = get_history_scope(session)
    assert len(first) >= 32
    assert get_history_scope(session) == first
    assert first != get_history_scope({})
    assert "private" not in first
    session["history_scope"] = "caller-selected"
    assert get_history_scope(session) != "caller-selected"


def test_legacy_is_copied_only_before_a_durable_receipt_exists(stores, scope):
    _, history = stores
    legacy = entry(["original"], ["before"])
    loaded, revision = history.load(1, scope, "playlist", legacy=legacy)
    assert loaded == legacy and revision is None
    loaded["states"][0].append("caller mutation")
    assert legacy == entry(["original"], ["before"])
    operation_id, saved = complete(stores, scope, legacy=legacy)
    assert history.load(1, scope, "playlist", legacy=entry(["stale"])) == (saved, operation_id)
    assert history.load(2, scope, "playlist", legacy=legacy) == (legacy, None)
    assert history.load(1, get_history_scope({}), "playlist", legacy=legacy) == (legacy, None)


def test_capture_contains_only_relevant_legacy_imports_and_authoritative_revisions(stores, scope):
    _, history = stores
    operation_id, _ = complete(stores, scope)
    legacy = {"playlist": entry(["stale"]), "new": entry(["initial"]), "other": entry(["unrelated"])}
    captured = history.capture(1, scope, ["new", "playlist"], legacy=legacy)
    assert captured == {"scope": scope, "imports": {"new": entry(["initial"])},
                        "revisions": {"new": None, "playlist": operation_id}}


def test_mapping_preserves_state_service_undo_and_branch_semantics(stores, scope):
    _, history = stores
    initial = entry(["original"], ["before"])
    session = history.session_for(1, scope, ["playlist"], legacy={"playlist": initial})
    assert session.modified is False
    assert StateService.can_undo(session, "playlist") is True
    selected = StateService.peek_undo(session, "playlist")
    assert selected.desired_uris == ("original",)
    StateService.confirm_undo(session, "playlist", selected)
    StateService.record_verified_change(session, "playlist", before_uris=["original"], after_uris=["new"])
    assert session["playlist_states"]["playlist"] == entry(["original"], ["new"])
    assert session.before["playlist"] == initial
    assert session.modified is True


def test_running_checkpoint_is_not_visible_before_same_row_finishes(stores, scope):
    repository, history = stores
    operation, lease = accepted(repository)
    session = history.session_for(1, scope, ["playlist"])
    StateService.record_verified_change(session, "playlist", before_uris=["before"], after_uris=["after"])
    document = history.stage({"choices": {"kept": True}}, session, state="succeeded", confirmed=True)
    repository.checkpoint(lease, document)
    assert history.load(1, scope, "playlist") == (None, None)
    repository.finish(lease, "succeeded")
    assert history.load(1, scope, "playlist") == (entry(["before"], ["after"]), operation.id)
    assert repository.get(operation.id).journal["choices"] == {"kept": True}


@pytest.mark.parametrize("state,confirmed", [("needs_attention", True), ("failed", True), ("succeeded", False)])
def test_unconfirmed_or_unsafe_outcomes_cannot_stage_history(stores, scope, state, confirmed):
    _, history = stores
    session = history.session_for(1, scope, ["playlist"])
    StateService.record_verified_change(session, "playlist", before_uris=["before"], after_uris=["after"])
    with pytest.raises(HistoryError):
        history.stage({}, session, state=state, confirmed=confirmed)


@pytest.mark.parametrize("journal", [
    {"status": "needs_attention"},
    {"status": "running", "plans": []},
    {"phase": "compensating", "status": "succeeded", "plans": []},
    {"phase": "forward", "status": "succeeded", "plans": [{"batches": [{"state": "dispatched"}]}]},
    {"phase": "forward", "status": "succeeded", "plans": [{"batches": [{"state": "prepared"}]}]},
])
def test_unfinished_write_evidence_cannot_be_overridden_by_a_confirmation_flag(stores, scope, journal):
    _, history = stores
    session = history.session_for(1, scope, ["playlist"])
    StateService.record_verified_change(session, "playlist", before_uris=["before"], after_uris=["after"])
    with pytest.raises(HistoryError):
        history.stage(journal, session, state="succeeded", confirmed=True)


def test_rollback_requires_confirmed_compensation_but_can_have_uncertain_forward_batch(stores, scope):
    _, history = stores
    session = history.session_for(1, scope, ["playlist"])
    StateService.record_verified_change(session, "playlist", before_uris=["before"], after_uris=["after"])
    journal = {"phase": "compensating", "status": "rolled_back",
               "plans": [{"batches": [{"state": "dispatched"}]}],
               "compensation": [{"batches": [{"state": "acknowledged"}]}]}
    staged = history.stage(journal, session, state="rolled_back", confirmed=True)
    assert staged["history"]["entries"]["playlist"]["entry"] is None
    journal["compensation"][0]["batches"][0]["state"] = "dispatched"
    with pytest.raises(HistoryError):
        history.stage(journal, session, state="rolled_back", confirmed=True)


def test_needs_attention_and_mismatched_completion_never_replace_confirmed_history(stores, scope):
    repository, history = stores
    old_id, old = complete(stores, scope)
    operation, lease = accepted(repository)
    session = history.session_for(1, scope, ["playlist"])
    StateService.record_verified_change(session, "playlist", before_uris=["after"], after_uris=["unsafe"])
    repository.checkpoint(lease, history.stage({}, session, state="succeeded", confirmed=True))
    repository.finish(lease, "needs_attention")
    assert history.load(1, scope, "playlist") == (old, old_id)
    assert operation.id != old_id


def test_rolled_back_uses_frozen_before_history_instead_of_advanced_mapping(stores, scope):
    repository, history = stores
    old_id, old = complete(stores, scope)
    operation, lease = accepted(repository)
    session = history.session_for(1, scope, ["playlist"])
    StateService.record_verified_change(session, "playlist", before_uris=["after"], after_uris=["not-kept"])
    repository.checkpoint(lease, history.stage({}, session, state="rolled_back", confirmed=True))
    repository.finish(lease, "rolled_back")
    assert history.load(1, scope, "playlist") == (old, operation.id)
    assert old_id != operation.id


def test_explicit_clear_is_a_receipt_and_never_resurrects_stale_legacy(stores, scope):
    repository, history = stores
    complete(stores, scope)
    operation, lease = accepted(repository)
    session = history.session_for(1, scope, ["playlist"])
    session["playlist_states"].pop("playlist")
    repository.checkpoint(lease, history.stage({}, session, state="succeeded", confirmed=True))
    repository.finish(lease, "succeeded")
    assert history.load(1, scope, "playlist", legacy=entry(["stale"])) == (None, operation.id)


def test_stale_undo_revision_is_rejected_and_stale_session_cannot_stage(stores, scope):
    _, history = stores
    first_id, _ = complete(stores, scope)
    stale = history.session_for(1, scope, ["playlist"], expected_revisions={"playlist": first_id})
    second_id, saved = complete(stores, scope, after=("newer",))
    with pytest.raises(StaleHistoryError):
        history.assert_revision(1, scope, "playlist", first_id)
    with pytest.raises(StaleHistoryError):
        history.session_for(1, scope, ["playlist"], expected_revisions={"playlist": first_id})
    StateService.record_verified_change(stale, "playlist", before_uris=["after"], after_uris=["stale"])
    with pytest.raises(StaleHistoryError):
        history.stage({}, stale, state="succeeded", confirmed=True)
    assert history.load(1, scope, "playlist") == (saved, second_id)


def test_retention_protects_latest_receipt_for_each_scoped_playlist_including_clear(stores, scope):
    repository, history = stores
    first_id, _ = complete(stores, scope)
    second_id, _ = complete(stores, scope, after=("newer",))
    other_id, _ = complete(stores, scope, target="other")
    separate_id, _ = complete(stores, get_history_scope({}))
    operation, lease = accepted(repository)
    session = history.session_for(1, scope, ["playlist"])
    session["playlist_states"].clear()
    repository.checkpoint(lease, history.stage({}, session, state="succeeded", confirmed=True))
    repository.finish(lease, "succeeded")
    assert history.protected_operation_ids() == {operation.id, other_id, separate_id}
    assert second_id not in history.protected_operation_ids()
    assert first_id not in history.protected_operation_ids()


def test_ordered_duplicate_occurrences_and_empty_playlist_survive_round_trip(stores, scope):
    repository, history = stores
    operation, lease = accepted(repository)
    session = history.session_for(1, scope, ["playlist"])
    StateService.record_verified_change(session, "playlist", before_uris=["repeat", "other", "repeat"], after_uris=[])
    repository.checkpoint(lease, history.stage({}, session, state="succeeded", confirmed=True))
    repository.finish(lease, "succeeded")
    assert history.load(1, scope, "playlist") == (entry(["repeat", "other", "repeat"], []), operation.id)


def test_cross_target_or_credentials_are_not_copied_into_captured_history(stores, scope):
    _, history = stores
    legacy = {"playlist": {**entry(["before"]), "access_token": "private-token"},
              "spotify_token": "private-token", "other": entry(["not-selected"])}
    captured = history.capture(1, scope, ["playlist"], legacy=legacy)
    assert captured["imports"] == {"playlist": entry(["before"])}
    session = history.session_for(1, scope, ["playlist"], legacy=captured["imports"])
    session["playlist_states"]["other"] = entry(["not-selected"])
    with pytest.raises(HistoryError):
        history.stage({}, session, state="succeeded", confirmed=True)


@pytest.mark.parametrize("legacy", [{"states": [], "current_index": 0},
                                   {"states": [["one"]], "current_index": True},
                                   {"states": [["one"]], "current_index": 2},
                                   {"states": [[123]], "current_index": 0}])
def test_invalid_history_is_rejected_before_payload_capture(stores, scope, legacy):
    _, history = stores
    with pytest.raises(HistoryError):
        history.capture(1, scope, ["playlist"], legacy={"playlist": legacy})
