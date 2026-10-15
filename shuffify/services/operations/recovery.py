"""Reconcile the next possible dispatch against a complete settled observation."""

from .planner import content


def reconcile_resource(resource, batches, observed):
    """Return proposed acknowledgements or a conflict; never dispatch Spotify writes."""
    current = content(observed)
    metadata_only = current.get("scope") == "metadata"
    expected = resource["baseline"]
    snapshot = resource.get("snapshot_id")
    pending = None
    for item in batches:
        if item["state"] == "acknowledged":
            expected = item["after"]
            snapshot = item.get("snapshot_id")
        else:
            pending = item
            break
    compatible = metadata_only or not snapshot or observed.get("snapshot_id") == snapshot
    if pending is None:
        return ([], None) if current == expected and compatible else ([], "external_edit")
    if pending["state"] in ("prepared", "rejected"):
        return ([], None) if current == expected and compatible else ([], "external_edit")
    if pending["state"] != "dispatched":
        return [], "invalid_journal"
    if current == pending["after"]:
        # A no-effect batch is safe to acknowledge without guessing a duplicate-sensitive cursor.
        if current != expected or compatible:
            if current != expected and pending["path"].endswith("/items") and compatible:
                return [], "snapshot_ambiguous"
            return [pending], None
    if current == expected and compatible:
        if observed.get("settled") is True:
            return [], "replay_settled"
        return [], "unsettled_dispatch"
    return [], "external_edit"
