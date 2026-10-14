"""Signed digests: issue and verify one purpose's HMAC over a canonical payload."""

import hmac
from unittest.mock import patch

import pytest
from flask import Flask

from shuffify.services import signed_digest

PURPOSE = "shuffify.setup.ack.v1"
PAYLOAD = {"user_id": 7, "target_id": "target", "configuration_revision": "a" * 64, "change_hash": "b" * 64}
NOW = 1_800_000_000


@pytest.fixture
def app_ctx():
    app = Flask(__name__)
    app.secret_key = "signed-digest-test-key"
    with app.app_context():
        yield app


def test_a_token_round_trips_with_exactly_three_keys(app_ctx):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    assert set(token) == {"version", "issued_at", "signature"}
    assert token["version"] == 1 and token["issued_at"] == NOW
    assert len(token["signature"]) == 64 and int(token["signature"], 16) >= 0
    assert signed_digest.verify(PURPOSE, PAYLOAD, token, max_age_s=60, now=NOW + 60) is None


def test_issue_defaults_to_the_current_time(app_ctx):
    with patch("shuffify.services.signed_digest.time.time", return_value=NOW + 0.9):
        token = signed_digest.issue(PURPOSE, PAYLOAD)
        assert token["issued_at"] == NOW
        assert signed_digest.verify(PURPOSE, PAYLOAD, token, max_age_s=1) is None


def test_payload_key_order_does_not_matter(app_ctx):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    reordered = dict(reversed(list(PAYLOAD.items())))
    assert signed_digest.verify(PURPOSE, reordered, token, max_age_s=60, now=NOW) is None


def test_a_token_for_one_purpose_never_verifies_for_another(app_ctx):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    assert signed_digest.verify("shuffify.routine.activate.v1", PAYLOAD, token, max_age_s=60, now=NOW) == "mismatch"
    assert signed_digest.key(PURPOSE) != signed_digest.key("shuffify.routine.activate.v1")


@pytest.mark.parametrize("field, value", [
    ("user_id", 8), ("target_id", "other"), ("configuration_revision", "c" * 64), ("change_hash", "d" * 64),
])
def test_any_changed_payload_value_is_a_mismatch(app_ctx, field, value):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    assert signed_digest.verify(PURPOSE, {**PAYLOAD, field: value}, token, max_age_s=60, now=NOW) == "mismatch"


def test_a_different_secret_key_is_a_mismatch(app_ctx):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    app_ctx.secret_key = "another-key"
    assert signed_digest.verify(PURPOSE, PAYLOAD, token, max_age_s=60, now=NOW) == "mismatch"


def test_a_tampered_signature_or_issue_time_is_a_mismatch(app_ctx):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    flipped = ("0" if token["signature"][0] != "0" else "1") + token["signature"][1:]
    assert signed_digest.verify(PURPOSE, PAYLOAD, {**token, "signature": flipped}, max_age_s=60, now=NOW) == "mismatch"
    assert signed_digest.verify(
        PURPOSE, PAYLOAD, {**token, "issued_at": NOW + 1}, max_age_s=60, now=NOW + 1
    ) == "mismatch"


def test_a_token_older_than_its_maximum_age_is_expired(app_ctx):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    assert signed_digest.verify(PURPOSE, PAYLOAD, token, max_age_s=1800, now=NOW + 1800) is None
    assert signed_digest.verify(PURPOSE, PAYLOAD, token, max_age_s=1800, now=NOW + 1801) == "expired"


def test_an_issue_time_more_than_sixty_seconds_ahead_is_malformed(app_ctx):
    ahead = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW + 60)
    assert signed_digest.verify(PURPOSE, PAYLOAD, ahead, max_age_s=60, now=NOW) is None
    too_far = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW + 61)
    assert signed_digest.verify(PURPOSE, PAYLOAD, too_far, max_age_s=60, now=NOW) == "malformed"


@pytest.mark.parametrize("mutate", [
    lambda t: None,
    lambda t: "token",
    lambda t: [t["version"], t["issued_at"], t["signature"]],
    lambda t: {k: v for k, v in t.items() if k != "signature"},
    lambda t: {**t, "extra": 1},
    lambda t: {**t, "version": 2},
    lambda t: {**t, "version": True},
    lambda t: {**t, "version": "1"},
    lambda t: {**t, "issued_at": float(t["issued_at"])},
    lambda t: {**t, "issued_at": str(t["issued_at"])},
    lambda t: {**t, "issued_at": True},
    lambda t: {**t, "signature": t["signature"].upper()},
    lambda t: {**t, "signature": t["signature"][:63]},
    lambda t: {**t, "signature": t["signature"] + "0"},
    lambda t: {**t, "signature": "g" * 64},
    lambda t: {**t, "signature": None},
])
def test_a_malformed_token_is_refused_as_malformed(app_ctx, mutate):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    assert signed_digest.verify(PURPOSE, PAYLOAD, mutate(token), max_age_s=60, now=NOW) == "malformed"


def test_signatures_are_compared_in_constant_time(app_ctx):
    token = signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
    with patch("shuffify.services.signed_digest.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
        assert signed_digest.verify(PURPOSE, PAYLOAD, token, max_age_s=60, now=NOW) is None
    compare.assert_called_once_with(token["signature"], token["signature"])


def test_signing_requires_a_secret_key():
    app = Flask(__name__)
    with app.app_context(), pytest.raises(RuntimeError):
        signed_digest.issue(PURPOSE, PAYLOAD, now=NOW)
