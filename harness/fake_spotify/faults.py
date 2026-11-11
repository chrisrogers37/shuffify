"""Faults a test arms on the fake Spotify: what Spotify answers when it is
busy, out of quota, failing or slow, on demand.

Spotify documents each of these answers, but no account can be made to
produce one when a test needs it:

- ``rate_limit``: 429, with ``Retry-After`` in seconds when the test gives
  one. The rate limit is counted over a rolling 30-second window, and the
  429 "will normally include a Retry-After header with a value in seconds"
  (https://developer.spotify.com/documentation/web-api/concepts/rate-limits).
- ``quota``: 429 whose error object carries ``"reason": "QUOTA_EXCEEDED"``,
  Spotify's answer to a spent quota
  (https://developer.spotify.com/documentation/web-api/references/changes/july-2026).
- ``server_error``: 500, 502 or 503, the server errors Spotify documents for
  every endpoint (https://developer.spotify.com/documentation/web-api/concepts/api-calls),
  answered before the operation runs, so nothing is applied.
- ``slow``: the operation is served normally, ``delay`` seconds late (or as
  soon as the fault is released, below), for a client's timeout. A write is
  still applied, after the client may have given up on it, as a real server
  can.

Three more name the app, the grant or the account that Spotify refuses. They
are answers a person's own setup produces (deleting the app, removing the
app from their account, an account the app does not list), which no test can
make a real account do on demand:

- ``user_not_allowed``: 403 whose message says the user is not registered
  for the app, which Spotify gives any ``/v1`` call of an account the app's
  user list leaves out. Armed for a ``/v1`` operation, like the kinds above.
- ``app_rejected``: the token endpoint answers 400 ``invalid_client``, as it
  does for a client ID it does not know (a deleted app).
- ``grant_revoked``: the token endpoint answers 400 ``invalid_grant``, as it
  does for a refresh token that no longer works (access removed in the
  person's Spotify account).

And one changes a success rather than failing it:

- ``rotate_refresh``: a refresh answers with a new refresh token and ends the
  one it was given, so a later refresh with the old one gets ``invalid_grant``
  (the rotation Spotify documents for PKCE refreshes). It applies to refresh
  grants only, so a code exchange does not use it up. The old token stays
  ended for good, even after the fault has been spent.

The token kinds are armed for ``POST /api/token`` (the ``operation`` may be
left out). They come after the client check, as the others come after the
request checks, and ``skip`` and ``times`` count calls of the token endpoint
(of refresh grants, for ``rotate_refresh``). What Spotify's own answers are,
beyond what its documentation says, is the owner's check against the real
thing (docs-only in behaviours.json until then).

``POST /_fake/faults`` arms one for an operation (``"GET /me"``, as in
shapes.json), optionally only for one playlist. It lets ``skip`` matching
calls through, answers the next ``times`` of them, then the fake serves
normally again. ``GET /_fake/faults`` shows each fault and how often it
fired; ``DELETE /_fake/faults`` and ``/_fake/reset`` clear them.

``POST /_fake/faults/release`` ends the wait of every armed slow fault: the
calls it holds are answered at once, and its later firings are not held at
all (each still counts as fired). A test that needs a call in flight arms a
generous ``delay``, acts while the call is held, then releases it rather than
waiting the delay out. A slow fault armed after a release holds again.
"""

import threading
from dataclasses import asdict, dataclass, fields
from typing import List, Optional

from . import shapes

TOKEN_OPERATION = "POST /api/token"  # not a /v1 operation, so not in shapes.json
TOKEN_KINDS = ("app_rejected", "grant_revoked", "rotate_refresh")
KINDS = ("rate_limit", "quota", "server_error", "slow", "user_not_allowed") + TOKEN_KINDS
# The grant a kind applies to; a kind not listed applies to every call.
_KIND_GRANTS = {"rotate_refresh": "refresh_token"}
SERVER_ERRORS = {500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable"}
# What the token endpoint answers for each failing token kind: (error, error_description).
TOKEN_ERRORS = {
    "app_rejected": ("invalid_client", "Invalid client"),
    "grant_revoked": ("invalid_grant", "Refresh token revoked"),
}
# Spotify's message for a /v1 call of a user the app's user list leaves out.
USER_NOT_ALLOWED_MESSAGE = "Check settings on developer.spotify.com/dashboard, the user may not be registered."
MAX_DELAY = 60  # seconds: a slow answer, not a hung test
# The fields only one kind takes.
_KIND_FIELDS = {"retry_after": "rate_limit", "status": "server_error", "delay": "slow"}


@dataclass
class Fault:
    operation: str
    kind: str
    times: int = 1
    skip: int = 0
    playlist_id: Optional[str] = None
    retry_after: Optional[int] = None  # rate_limit: seconds; None sends no header
    status: int = 503  # server_error
    delay: float = 0.0  # slow: seconds before answering
    seen: int = 0  # matching calls so far
    fired: int = 0  # of which answered with the fault

    def __post_init__(self):
        # slow: Faults.release() sets it to end this fault's holds, now and later. An attribute, not a field, so
        # asdict() and the JSON the control routes send leave it out.
        self.released = threading.Event()


_SETTABLE = {f.name for f in fields(Fault)} - {"seen", "fired"}


def _whole(name: str, value, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be a whole number of at least {minimum}")
    return value


def parse(spec) -> Fault:
    """A Fault from a test's JSON, or ValueError saying what is wrong with it."""
    if not isinstance(spec, dict):
        raise ValueError("a fault is a JSON object")
    unknown = set(spec) - _SETTABLE
    if unknown:
        raise ValueError(f"unknown fault fields: {', '.join(sorted(unknown))}")
    operation, kind = spec.get("operation"), spec.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if kind in TOKEN_KINDS:
        operation = TOKEN_OPERATION if operation is None else operation
        if operation != TOKEN_OPERATION:
            raise ValueError(f"the {kind} fault applies only to {TOKEN_OPERATION}")
    elif operation not in shapes.SHAPES["operations"]:
        raise ValueError(f"operation {operation!r} is not one the fake serves")
    for name, owner in _KIND_FIELDS.items():
        if name in spec and kind != owner:
            raise ValueError(f"{name} applies only to a {owner} fault")
    fault = Fault(operation=operation, kind=kind, times=_whole("times", spec.get("times", 1), 1),
                  skip=_whole("skip", spec.get("skip", 0), 0))
    if spec.get("playlist_id") is not None:
        if "{playlist_id}" not in operation or not isinstance(spec["playlist_id"], str):
            raise ValueError("playlist_id applies only to a playlist operation, as a string")
        fault.playlist_id = spec["playlist_id"]
    if spec.get("retry_after") is not None:
        fault.retry_after = _whole("retry_after", spec["retry_after"], 0)
    if "status" in spec:
        if spec["status"] not in SERVER_ERRORS:
            raise ValueError(f"status must be one of {sorted(SERVER_ERRORS)}")
        fault.status = spec["status"]
    if kind == "slow":
        delay = spec.get("delay")
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 < delay <= MAX_DELAY:
            raise ValueError(f"a slow fault needs a delay in seconds, above 0 and at most {MAX_DELAY}")
        fault.delay = float(delay)
    return fault


class Faults:
    """The armed faults. Thread-safe: the fake serves on several threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._armed: List[Fault] = []

    def arm(self, spec) -> Fault:
        fault = parse(spec)
        with self._lock:
            self._armed.append(fault)
        return fault

    def clear(self) -> None:
        with self._lock:
            self._armed.clear()

    def release(self) -> None:
        """End the wait of every armed slow fault: its held calls are answered, and its later ones are not held."""
        with self._lock:
            for fault in self._armed:
                fault.released.set()

    def take(self, operation: str, playlist_id: Optional[str], grant: Optional[str] = None) -> Optional[Fault]:
        """The fault this call is answered with, if any, counted as fired.

        ``grant`` is the token endpoint's ``grant_type``: a fault that applies
        to one grant (rotate_refresh) ignores calls for any other."""
        with self._lock:
            for fault in self._armed:
                if fault.operation != operation or fault.fired >= fault.times:
                    continue
                if _KIND_GRANTS.get(fault.kind, grant) != grant:
                    continue
                if fault.playlist_id is not None and fault.playlist_id != playlist_id:
                    continue
                fault.seen += 1
                if fault.seen > fault.skip:
                    fault.fired += 1
                    return fault
        return None

    def state(self) -> List[dict]:
        with self._lock:
            return [asdict(fault) for fault in self._armed]
