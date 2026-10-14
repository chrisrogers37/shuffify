"""Signed digests: an HMAC over one canonical fingerprint.

Live review bindings are deliberately unsigned comparisons. A signed
digest proves that this server issued a statement about ``payload`` for one
``purpose`` (the playlist setup acknowledgment, or a routine's activation
intent) and when. It is not encryption, a session or replay protection: the
caller re-derives every fact and verifies the token against them.

Each purpose signs with its own key derived from the app's ``SECRET_KEY``, and
the purpose is also part of the signed fingerprint, so a token issued for one
purpose never verifies for another. Purposes are versioned strings, for
example ``shuffify.setup.ack.v1``.
"""

import hashlib
import hmac
import time

from flask import current_app

from shuffify.services.live_review_bindings import DIGEST, fingerprint

VERSION = 1
# A token may claim to be issued at most this far ahead of this server's clock.
FUTURE_SKEW_S = 60
_TOKEN_KEYS = frozenset(("version", "issued_at", "signature"))


def key(purpose: str) -> bytes:
    """The signing key for one purpose: HMAC-SHA256(SECRET_KEY, "shuffify.signed:" + purpose)."""
    secret = current_app.secret_key
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not secret:
        raise RuntimeError("SECRET_KEY is required to sign a digest")
    return hmac.new(secret, f"shuffify.signed:{purpose}".encode("utf-8"), hashlib.sha256).digest()


def _signature(purpose: str, payload: dict, issued_at: int) -> str:
    message = fingerprint({**payload, "purpose": purpose, "issued_at": issued_at})
    return hmac.new(key(purpose), message.encode("ascii"), hashlib.sha256).hexdigest()


def _now(now) -> int:
    return int(time.time()) if now is None else int(now)


def issue(purpose: str, payload: dict, *, now: int | None = None) -> dict:
    """``{"version": 1, "issued_at": <epoch seconds>, "signature": <64 hex>}`` for payload under purpose."""
    issued_at = _now(now)
    return {"version": VERSION, "issued_at": issued_at, "signature": _signature(purpose, payload, issued_at)}


def verify(purpose: str, payload: dict, token, *, max_age_s: int, now: int | None = None) -> str | None:
    """None when token is this server's signature over payload for purpose, issued within max_age_s.

    Otherwise the reason: ``"malformed"`` (not exactly a version 1 token with an
    int ``issued_at`` at most 60 s ahead and a 64-hex signature), ``"mismatch"``
    (not signed over this purpose and payload) or ``"expired"``.
    """
    if not isinstance(token, dict) or set(token) != _TOKEN_KEYS:
        return "malformed"
    version, issued_at, signature = token["version"], token["issued_at"], token["signature"]
    current = _now(now)
    if (
        type(version) is not int
        or version != VERSION
        or type(issued_at) is not int
        or issued_at > current + FUTURE_SKEW_S
        or not isinstance(signature, str)
        or not DIGEST.fullmatch(signature)
    ):
        return "malformed"
    if not hmac.compare_digest(_signature(purpose, payload, issued_at), signature):
        return "mismatch"
    if current - issued_at > max_age_s:
        return "expired"
    return None
