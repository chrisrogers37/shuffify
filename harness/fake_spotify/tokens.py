"""Stateless OAuth codes and tokens for the fake Spotify.

Each one is a signed JSON payload (itsdangerous, which Flask already uses),
salted by kind so a refresh token never passes for an access token. Nothing
is stored, so tokens survive a restart of the fake -- the app keeps the
refresh token for scheduled jobs, and one issued before a restart must still
work after it, as a real one would.

The signing key is not a secret. These tokens open nothing but this fake.
"""

import secrets
import time
from typing import Optional

from itsdangerous import BadSignature, URLSafeSerializer

_KEY = "shuffify fake Spotify: signs fake tokens so they survive a restart"


def mint(kind: str, lifetime: Optional[int] = None, **claims) -> str:
    if lifetime is not None:
        claims["exp"] = int(time.time()) + lifetime
    return URLSafeSerializer(_KEY, salt=kind).dumps({"nonce": secrets.token_hex(4), **claims})


def read(token: str, kind: str) -> Optional[dict]:
    """The claims of a genuine token of this kind, or None. Check ``expired()``."""
    try:
        return URLSafeSerializer(_KEY, salt=kind).loads(token or "")
    except BadSignature:
        return None


def expired(claims: dict) -> bool:
    return claims.get("exp", float("inf")) < time.time()
