"""Create harness data through reviewed setup and exact routine HTTP contracts."""

from copy import deepcopy
from uuid import uuid4


def save_setup(client, target, changes):
    """Review one frozen strict change list, then save its signed acknowledgment."""
    context = client.ok("get", f"/playlist/{target}/setup/context")["context"]
    body = {
        "configuration_revision": context["configuration_revision"],
        "section_digests": deepcopy(context["section_digests"]),
        "changes": deepcopy(changes),
    }
    review = client.ok("post", f"/playlist/{target}/setup/impact", json=deepcopy(body))
    return client.ok("put", f"/playlist/{target}/setup/settings", json={
        **body, "acknowledgment": deepcopy(review["acknowledgment"]),
    })


def add_playlist_source(client, target, source):
    """Save one source without starting a routine; return its full source DTO."""
    save_setup(client, target, [{"op": "add_playlist_source", "playlist_id": source}])
    sources = client.ok("get", f"/playlist/{target}/raid-status")["raid_status"]["sources"]
    return next(row for row in sources if row["source_playlist_id"] == source)


def create_routine(client, target, *, job_type="shuffle", algorithm_name=None, algorithm_params=None,
                   timing=None):
    """Explicitly review and activate a new routine using server-normalized fields."""
    definition = {
        "job_type": job_type,
        "timing": deepcopy(timing) if timing is not None else {
            "kind": "interval", "interval": "daily", "timezone": "UTC",
        },
    }
    if algorithm_name is not None or job_type in {"shuffle", "raid_and_shuffle"}:
        definition["algorithm_name"] = algorithm_name or "BasicShuffle"
    if algorithm_params is not None:
        definition["algorithm_params"] = deepcopy(algorithm_params)
    choices = {"create_another": True, "desired_enabled": True}
    key = str(uuid4())
    review = client.ok("post", f"/playlist/{target}/routines/review", json={
        "request_key": key, "definition": definition, **choices,
    })
    accepted = client.ok("post", f"/playlist/{target}/routines/activate", json={
        "request_key": key, "definition": deepcopy(review["definition"]),
        "intent": deepcopy(review["intent"]), "dependency_revision": review["dependency_revision"], **choices,
    })
    return accepted["schedule"]


def list_schedules(client, target):
    """Project exact current routine rows to their stored schedule DTOs for assertions."""
    rows = client.ok("get", f"/playlist/{target}/routines/context")["routines"]
    return [row["schedule"] for row in rows]


def set_routine_enabled(client, schedule_id, enabled):
    """Send desired state with the selected routine's current revision, never toggle."""
    row = next(row for row in client.ok("get", "/routines")["routines"] if row["schedule_id"] == schedule_id)
    return client.ok("put", f"/routines/{schedule_id}/state", json={
        "desired_enabled": enabled, "expected_revision": row["schedule_revision"],
    })
