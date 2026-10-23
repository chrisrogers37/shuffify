"""Reconcile the next possible dispatch against a complete settled observation.

Spotify's uncached reads can lag a write we already know succeeded: right after
an acknowledged write they may return the new track list with the previous
snapshot_id, or (briefly) the previous track list entirely. Snapshot strings are
opaque and unique per revision, so this module judges an observation by its
complete ordered content, using snapshots only to recognise a stale read of a
point this run already passed through.
"""

from .planner import content

STALE_READ = "stale_read"
CURRENT, STALE, CHANGED = "current", "stale", "changed"


def _remember(points, state, snapshot):
    if isinstance(snapshot, str) and snapshot:
        points.append((state, snapshot))


def walk(resource, batches):
    """Return the expected state and snapshot, the earlier known points, and the first unacknowledged batch."""
    expected, snapshot = resource["baseline"], resource.get("snapshot_id")
    earlier, pending = [], None
    for item in batches:
        if item["state"] == "acknowledged":
            _remember(earlier, expected, snapshot)
            _remember(earlier, expected, item.get("snapshot_before"))
            expected, snapshot = item["after"], item.get("snapshot_id")
        else:
            pending = item
            break
    return expected, snapshot, earlier, pending


def observation_verdict(observed, expected, snapshot, earlier):
    """Judge one complete observation against the expected state of a write chain.

    CURRENT: the ordered content equals the expected state and the snapshot is
    the expected one or one this run already passed through (a lagging read of
    our own write returns the previous revision). A snapshot this run has never
    seen, even over identical content, belongs to another writer: the fence
    stays for unknown snapshots, and Spotify has been seen lagging only to
    revisions the run already knows.
    STALE: content and snapshot together equal a point this run already passed.
    A snapshot is never reused by a later revision, so this is a lagging read
    of an older state, not an edit.
    CHANGED: anything else.
    """
    current = content(observed)
    observed_snapshot = observed.get("snapshot_id")
    if current == expected:
        if (current.get("scope") == "metadata" or not snapshot or observed_snapshot == snapshot
                or any(observed_snapshot == known for _, known in earlier)):
            return CURRENT
        return CHANGED
    if any(current == state and observed_snapshot == known for state, known in earlier):
        return STALE
    return CHANGED


def reconcile_resource(resource, batches, observed):
    """Return proposed acknowledgements or a conflict; never dispatch Spotify writes."""
    current = content(observed)
    metadata_only = current.get("scope") == "metadata"
    expected, snapshot, earlier, pending = walk(resource, batches)
    verdict = observation_verdict(observed, expected, snapshot, earlier)
    mismatch = STALE_READ if verdict == STALE else "external_edit"
    if pending is None or pending["state"] in ("prepared", "rejected"):
        return ([], None) if verdict == CURRENT else ([], mismatch)
    if pending["state"] != "dispatched":
        return [], "invalid_journal"
    # A dispatched batch may or may not have landed, so here the snapshot is
    # evidence: a landed write always moves it to a revision we have not seen.
    # A snapshot this run already passed is the same signature as the pre-dispatch one: a lagging read.
    compatible = (metadata_only or not snapshot or observed.get("snapshot_id") == snapshot
                  or any(observed.get("snapshot_id") == known for _, known in earlier))
    if current == pending["after"]:
        # A no-effect batch is safe to acknowledge without guessing a duplicate-sensitive cursor.
        if current != expected or compatible:
            if current != expected and pending["path"].endswith("/items") and compatible:
                # The new content under the pre-dispatch snapshot is the signature of a
                # lagging read, but it cannot be told from a foreign identical edit, so
                # a dispatched batch is neither acknowledged nor replayed.
                return [], "snapshot_ambiguous"
            return [pending], None
    if current == expected and compatible:
        if observed.get("settled") is True:
            return [], "replay_settled"
        return [], "unsettled_dispatch"
    return [], mismatch
