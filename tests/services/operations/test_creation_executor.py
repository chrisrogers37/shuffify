"""Creation identity is durable before a separately verified privacy intent."""

import importlib
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.operations.journal import NeedsAttention, OperationDeferred
from shuffify.services.operations.repository import LeaseLost
from shuffify.spotify.exceptions import SpotifyForbiddenError, SpotifyWriteUnverifiedError
from tests.services.operations.test_collect_drip_executors import Runtime as BaseRuntime
from tests.services.operations.test_crash_recovery import Crash, World, uri


def module():
    return importlib.import_module("shuffify.services.operations.executors.creation")


class Runtime(BaseRuntime):
    def __init__(self, world):
        super().__init__(world)
        self.journal.read_metadata = self.read_metadata_resource

    def read_metadata_resource(self, resource):
        observed = self.world.read(resource)
        return {key: observed[key] for key in ("metadata", "snapshot_id")}


class CreationWorld(World):
    def __init__(self, *, ignored_private=False):
        super().__init__([uri(1), uri(1)])
        self.ignored_private = ignored_private

    def dispatch(self, method, path, body):
        if path == "/me/playlists" and self.ignored_private:
            result = super().dispatch(method, path, body)
            self.playlists[result["id"]]["metadata"]["public"] = True
            return {**result, "public": True}
        return super().dispatch(method, path, body)


def captured():
    return module().CreationConfig(1, "target", "Chosen name", "Chosen description", "archive")


def run(runtime, config=None):
    with runtime.journal.observe():
        return module().execute_creation(runtime, config or captured(), prepare_key="creation.v1")


@pytest.fixture
def world():
    return CreationWorld(ignored_private=True)


def test_prepare_freezes_authorized_target_and_exact_caller_strings_without_effects(world):
    before = deepcopy(world.playlists)
    prepared = module().prepare_creation(Runtime(world), captured(), prepare_key="creation.v1")
    assert world.playlists == before
    assert world.writes == []
    assert (world.saved or {}).get("plans", []) == []
    assert prepared["before"] == {
        "target": {
            "scope": "metadata",
            "metadata": before["target"]["metadata"],
            "snapshot_id": before["target"]["snapshot_id"],
        }
    }
    assert prepared["choices"] == {
        "name": "Chosen name",
        "description": "Chosen description",
        "public": False,
        "purpose": "archive",
    }
    assert prepared["config"] == captured().document()


def test_known_creation_is_saved_before_actual_privacy_put_and_freshly_confirmed(world):
    runtime = Runtime(world)
    request = runtime.http._session.request

    def assert_known_id(method, url, **kwargs):
        if method == "PUT":
            assert world.saved["plans"][0]["result"]["id"] == "created-1"
            assert world.saved["plans"][0]["batches"][0]["state"] == "acknowledged"
            assert world.saved["runtime"]["prepared"]["creation.v1.privacy"]["metadata"]["public"] is True
        return request(method, url, **kwargs)

    runtime.http._session.request = assert_known_id
    applied = run(runtime)
    assert [(method, path) for method, path, _ in world.writes] == [
        ("POST", "/me/playlists"),
        ("PUT", "/playlists/created-1"),
    ]
    assert world.writes[0][2] == {"name": "Chosen name", "description": "Chosen description", "public": False}
    assert applied["result"]["playlist"] == {"id": "created-1", "name": "Chosen name"}
    assert applied["result"]["outcome"] == "created"
    assert applied["result"]["privacy_confirmed"] is True
    assert applied["after"]["created-1"]["metadata"]["public"] is False
    assert world.saved["resources"]["created-1"]["scope"] == "metadata"
    assert applied["before"]["target"]["metadata"] == world.playlists["target"]["metadata"]
    assert applied["local_actions"] == {}
    assert all("/target" not in path for _, path, _ in world.writes)


def test_already_private_created_playlist_is_freshly_verified_without_redundant_put():
    world = CreationWorld()
    applied = run(Runtime(world))
    assert [method for method, _, _ in world.writes] == ["POST"]
    assert applied["result"]["privacy_confirmed"] is True
    assert [step["kind"] for step in world.saved["plans"]] == ["create", "details"]
    assert world.saved["plans"][1]["batches"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", True),
        ("user_id", 2),
        ("target_playlist_id", "unreserved"),
        ("name", ""),
        ("name", "  "),
        ("description", None),
        ("purpose", ""),
    ],
)
def test_invalid_captured_identity_or_strings_refuse_before_reads(world, field, value):
    with pytest.raises(JobExecutionError):
        run(Runtime(world), replace(captured(), **{field: value}))
    assert world.trace == []


def test_creation_config_is_immutable_and_changed_prepared_intent_cannot_create(world):
    config = captured()
    with pytest.raises(FrozenInstanceError):
        config.name = "Changed"
    runtime = Runtime(world)
    module().prepare_creation(runtime, config, prepare_key="creation.v1")
    with pytest.raises(JobExecutionError, match="configuration changed"):
        run(runtime, replace(config, name="Changed"))
    assert world.writes == []


def test_target_metadata_change_after_prepare_refuses_before_creation(world):
    runtime = Runtime(world)
    module().prepare_creation(runtime, captured(), prepare_key="creation.v1")
    world.playlists["target"]["metadata"]["name"] = "External"
    world.playlists["target"]["snapshot_id"] = "external"
    with pytest.raises(NeedsAttention):
        run(runtime)
    assert world.writes == []


def test_actual_executor_requires_its_outer_observer(world):
    with pytest.raises(JobExecutionError, match="observer"):
        module().execute_creation(Runtime(world), captured(), prepare_key="creation.v1")
    assert world.writes == []


def test_acknowledged_creation_replay_after_lost_helper_output_never_posts_twice(world):
    first = run(Runtime(world))
    writes = deepcopy(world.writes)
    second = run(Runtime(world))
    assert second == first
    assert world.writes == writes


@pytest.mark.parametrize("where", ["creation.ack", "privacy.prepared", "privacy.applied", "privacy.ack"])
def test_crash_after_known_id_resumes_privacy_without_creating_again(world, where):
    runtime = Runtime(world)
    checkpoint = world.checkpoint
    cut = []

    def checkpoint_cut(document):
        saved = checkpoint(document)
        plans = saved.get("plans", [])
        matched = where == "creation.ack" and len(plans) == 1 and plans[0]["batches"][0]["state"] == "acknowledged"
        matched |= where == "privacy.prepared" and len(plans) == 2 and plans[1]["batches"][0]["state"] == "prepared"
        matched |= where == "privacy.ack" and len(plans) == 2 and plans[1]["batches"][0]["state"] == "acknowledged"
        if matched and not cut:
            cut.append(True)
            raise Crash()
        return saved

    point = world.point

    def applied_cut(label):
        point(label)
        if where == "privacy.applied" and label == "spotify.applied" and world.writes[-1][0] == "PUT" and not cut:
            cut.append(True)
            raise Crash()

    world.checkpoint = runtime.journal.checkpoint = checkpoint_cut
    world.point = applied_cut
    with pytest.raises(Crash):
        run(runtime)
    world.checkpoint = checkpoint
    recovered = Runtime(world)
    assert recovered.journal.resume()["state"] == "succeeded"
    applied = run(recovered)
    assert applied["result"]["privacy_confirmed"] is True
    assert sum(method == "POST" for method, _, _ in world.writes) == 1
    assert sum(method == "PUT" for method, _, _ in world.writes) == 1


def test_applied_creation_response_lost_is_unknown_and_never_reposted(world):
    runtime = Runtime(world)
    dispatch = world.dispatch

    def lost(method, path, body):
        result = dispatch(method, path, body)
        if path == "/me/playlists":
            raise SpotifyWriteUnverifiedError("private transport detail")
        return result

    world.dispatch = lost
    with pytest.raises(SpotifyWriteUnverifiedError) as failed:
        run(runtime)
    assert failed.value.result["creation_unknown"] is True
    assert failed.value.result["outcome"] == "unknown"
    recovered = Runtime(world)
    assert recovered.journal.resume()["state"] == "needs_attention"
    with pytest.raises(NeedsAttention) as failed:
        run(recovered)
    assert failed.value.result["creation_unknown"] is True
    assert len(world.writes) == 1
    assert "private transport detail" not in str(failed.value.result)


def test_missing_creation_id_has_unknown_outcome_and_no_privacy_request(world):
    runtime = Runtime(world)
    request = runtime.http._session.request

    def missing_id(method, url, **kwargs):
        request(method, url, **kwargs)
        return SimpleNamespace(status_code=201, content=b"json", json=lambda: {"name": "Chosen name"})

    runtime.http._session.request = missing_id
    with pytest.raises(SpotifyWriteUnverifiedError) as failed:
        run(runtime)
    assert failed.value.result["error_code"] == "creation_unknown"
    with pytest.raises(NeedsAttention):
        run(Runtime(world))
    assert len(world.writes) == 1


def test_known_privacy_refusal_preserves_creation_identity_and_does_not_claim_confirmation(world):
    runtime = Runtime(world)
    request = runtime.http._session.request

    def refuse(method, url, **kwargs):
        if method == "PUT":
            return SimpleNamespace(
                status_code=403,
                content=b"json",
                headers={},
                text="private provider detail",
                json=lambda: {"error": {"message": "private provider detail"}},
            )
        return request(method, url, **kwargs)

    runtime.http._session.request = refuse
    with pytest.raises(SpotifyForbiddenError) as failed:
        run(runtime)
    assert failed.value.result["outcome"] == "created"
    assert failed.value.result["playlist"]["id"] == "created-1"
    assert failed.value.result["privacy_confirmed"] is False
    assert failed.value.result["creation_unknown"] is False
    assert "private provider detail" not in str(failed.value.result)


@pytest.mark.parametrize("phase", ["create", "privacy"])
def test_durable_rate_limit_preserves_known_or_unknown_identity_and_fences_recovery(world, phase):
    runtime = Runtime(world)
    request = runtime.http._session.request
    calls = []

    def rate_limit(method, url, **kwargs):
        path = urlsplit(url).path.removeprefix("/v1")
        if method == ("POST" if phase == "create" else "PUT"):
            calls.append(path)
            return SimpleNamespace(
                status_code=429,
                content=b"json",
                headers={"Retry-After": "120"},
                json=lambda: {"error": {"message": "slow down"}},
            )
        return request(method, url, **kwargs)

    runtime.http._session.request = rate_limit
    with pytest.raises(OperationDeferred) as deferred:
        run(runtime)
    result = deferred.value.result
    assert result["creation_unknown"] is False
    assert result["outcome"] == ("created" if phase == "privacy" else "rate_limited")
    assert result["privacy_confirmed"] is False
    before = deepcopy(world.trace)
    with pytest.raises(OperationDeferred):
        run(Runtime(world))
    assert world.trace == before
    assert len(calls) == 1


def test_external_edit_to_created_metadata_before_privacy_is_never_overwritten(world):
    runtime = Runtime(world)
    read = runtime.read_metadata_resource

    def external_edit(resource):
        world.playlists[resource]["metadata"]["description"] = "External edit"
        return read(resource)

    runtime.read_metadata_resource = external_edit
    with pytest.raises(NeedsAttention) as failed:
        run(runtime)
    assert failed.value.result["playlist"]["id"] == "created-1"
    assert failed.value.result["privacy_confirmed"] is False
    assert [method for method, _, _ in world.writes] == ["POST"]


def test_lease_loss_before_privacy_propagates_with_known_creation_identity(world):
    runtime = Runtime(world)
    read = runtime.read_metadata_resource

    def lost(resource):
        value = read(resource)
        if resource == "created-1":
            raise LeaseLost("lease lost")
        return value

    runtime.read_metadata_resource = lost
    with pytest.raises(LeaseLost) as failed:
        run(runtime)
    assert failed.value.result["playlist"]["id"] == "created-1"
    assert failed.value.result["privacy_confirmed"] is False
    assert len(world.writes) == 1


def test_target_with_unrepresentable_items_can_create_without_reading_tracks(world):
    runtime = Runtime(world)
    runtime.read_resource = lambda resource: pytest.fail("Creation does not depend on playlist items")
    world.playlists["target"]["track_uris"] = [None, {"type": "episode"}]
    applied = run(runtime)
    assert applied["result"]["privacy_confirmed"] is True
    assert world.saved["resources"]["target"]["scope"] == "metadata"


def test_target_track_edit_after_prepare_does_not_invalidate_metadata_only_authority(world):
    runtime = Runtime(world)
    module().prepare_creation(runtime, captured(), prepare_key="creation.v1")
    world.playlists["target"]["snapshot_id"] = "tracks-only-edit"
    world.playlists["target"]["track_uris"] = ["unsupported"]
    assert run(runtime)["result"]["playlist"]["id"] == "created-1"


def test_created_id_cannot_alias_read_only_target_before_privacy(world):
    runtime = Runtime(world)
    request = runtime.http._session.request

    def alias(method, url, **kwargs):
        response = request(method, url, **kwargs)
        return SimpleNamespace(status_code=201, content=b"json", json=lambda: {**response.json(), "id": "target"})

    runtime.http._session.request = alias
    with pytest.raises(NeedsAttention) as failed:
        run(runtime)
    assert failed.value.result["playlist"]["id"] == "target"
    assert failed.value.result["privacy_confirmed"] is False
    assert [method for method, _, _ in world.writes] == ["POST"]


@pytest.mark.parametrize("phase", ["create", "privacy"])
def test_lease_loss_at_actual_http_fence_stays_typed_through_sdk_wrapper(world, phase):
    runtime = Runtime(world)
    checkpoint = world.checkpoint

    def lost(document):
        plans = document.get("plans", [])
        index = 0 if phase == "create" else 1
        if len(plans) > index and any(batch["attempts"] for batch in plans[index]["batches"]):
            raise LeaseLost("private fence detail")
        return checkpoint(document)

    runtime.journal.checkpoint = lost
    with pytest.raises(LeaseLost) as failed:
        run(runtime)
    assert len(world.writes) == (1 if phase == "privacy" else 0)
    if phase == "privacy":
        assert failed.value.result["playlist"]["id"] == "created-1"
        assert failed.value.result["privacy_confirmed"] is False
    assert "private fence detail" not in str(failed.value.result)


@pytest.mark.parametrize("resource", ["target", "created-1"])
def test_recovery_checks_both_read_only_target_and_known_creation_before_any_privacy_write(world, resource):
    runtime = Runtime(world)
    checkpoint = world.checkpoint
    cut = []

    def prepared_cut(document):
        saved = checkpoint(document)
        if len(saved.get("plans", [])) == 2 and not cut:
            cut.append(True)
            raise Crash()
        return saved

    runtime.journal.checkpoint = prepared_cut
    with pytest.raises(Crash):
        run(runtime)
    world.playlists[resource]["metadata"]["name"] = "External name"
    before, writes = deepcopy(world.playlists), deepcopy(world.writes)
    recovered = Runtime(world)
    assert recovered.journal.resume()["state"] == "needs_attention"
    with pytest.raises(NeedsAttention) as failed:
        run(recovered)
    assert failed.value.result["playlist"]["id"] == "created-1"
    assert failed.value.result["privacy_confirmed"] is False
    assert world.playlists == before
    assert world.writes == writes


def test_success_uses_returned_name_or_requested_fallback_without_renaming_playlist(world):
    runtime = Runtime(world)
    request = runtime.http._session.request

    def absent_name(method, url, **kwargs):
        response = request(method, url, **kwargs)
        if method == "POST":
            result = {key: value for key, value in response.json().items() if key != "name"}
            return SimpleNamespace(status_code=201, content=b"json", json=lambda: result)
        return response

    runtime.http._session.request = absent_name
    assert run(runtime)["result"]["playlist"]["name"] == "Chosen name"
    assert world.writes[-1] == ("PUT", "/playlists/created-1", {"public": False})


def test_ordinary_provider_read_error_propagates_with_known_identity(world):
    runtime = Runtime(world)
    read = runtime.read_metadata_resource

    def unavailable(resource):
        if resource == "created-1":
            raise ValueError("private provider detail")
        return read(resource)

    runtime.read_metadata_resource = unavailable
    with pytest.raises(ValueError) as failed:
        run(runtime)
    assert failed.value.result["playlist"]["id"] == "created-1"
    assert failed.value.result["privacy_confirmed"] is False
    assert "private provider detail" not in str(failed.value.result)
    assert len(world.writes) == 1


def test_existing_abort_reason_and_authored_result_survive_known_identity_enrichment(world):
    from shuffify.services.operations.journal import OperationAborted

    runtime = Runtime(world)
    read = runtime.read_metadata_resource
    stopped = OperationAborted("quota_exceeded", result={"error_code": "write_rejected", "message": "Quota exhausted."})

    def abort(resource):
        if resource == "created-1":
            raise stopped
        return read(resource)

    runtime.read_metadata_resource = abort
    with pytest.raises(OperationAborted) as failed:
        run(runtime)
    assert failed.value is stopped
    assert failed.value.result["error_code"] == "write_rejected"
    assert failed.value.result["message"] == "Quota exhausted."
    assert failed.value.result["playlist"]["id"] == "created-1"
    assert failed.value.result["privacy_confirmed"] is False
