"""Tests for Sentry initialization in the Flask app factory (F5)."""

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from shuffify import (
    _SENTRY_PII_DENYLIST,
    _SENTRY_PII_EXACT_KEYS,
    _SENTRY_SECRET_CONFIG_ATTRS,
    _SENTRY_SECRET_URL_CONFIG_ATTRS,
    _init_sentry,
    _register_sentry_secret_values,
    _sentry_key_is_sensitive,
    _sentry_sensitive_key_pattern,
    _strip_pii,
)


class _FakeConfig:
    """Stand-in for a config class with knobs for each test."""

    SENTRY_DSN = ""
    SENTRY_ENVIRONMENT = "production"
    SENTRY_TRACES_SAMPLE_RATE = 0.0
    SENTRY_PROFILES_SAMPLE_RATE = 0.0
    SENTRY_RELEASE = ""


@pytest.fixture(autouse=True)
def _clear_registered_secrets():
    """Keep the known-secret registry out of neighbouring tests.

    The registry is module-global and create_app registers into it, so any
    test that builds an app would otherwise leave TestConfig's SECRET_KEY
    armed for the rest of the session. _FakeConfig declares no secrets, so
    registering it empties the registry.
    """
    _register_sentry_secret_values(_FakeConfig)
    yield
    _register_sentry_secret_values(_FakeConfig)


# ---------------------------------------------------------------------------
# _init_sentry
# ---------------------------------------------------------------------------


class TestInitSentry:
    def test_empty_dsn_is_no_op(self):
        """No DSN → sentry_sdk.init is never called."""
        cfg = _FakeConfig
        with patch("sentry_sdk.init") as mock_init:
            assert _init_sentry(cfg) is False
            mock_init.assert_not_called()

    def test_dsn_set_invokes_init_with_kwargs(self):
        """DSN set → init is called with the expected kwargs."""

        class Cfg(_FakeConfig):
            SENTRY_DSN = "https://abc@o0.ingest.sentry.io/0"
            SENTRY_ENVIRONMENT = "staging"
            SENTRY_TRACES_SAMPLE_RATE = 0.1
            SENTRY_PROFILES_SAMPLE_RATE = 0.05
            SENTRY_RELEASE = "deadbeef"

        with patch("sentry_sdk.init") as mock_init:
            assert _init_sentry(Cfg) is True
            mock_init.assert_called_once()
            kwargs = mock_init.call_args.kwargs
            assert kwargs["dsn"] == Cfg.SENTRY_DSN
            assert kwargs["environment"] == "staging"
            assert kwargs["traces_sample_rate"] == 0.1
            assert kwargs["profiles_sample_rate"] == 0.05
            assert kwargs["release"] == "deadbeef"
            assert kwargs["send_default_pii"] is False
            # Frame locals carry OAuth codes and token dicts on the token
            # paths; the before_send walk is a backstop, not a substitute.
            assert kwargs["include_local_variables"] is False
            assert kwargs["before_send"] is _strip_pii
            # Three integrations: Flask, SQLAlchemy, Logging.
            assert len(kwargs["integrations"]) == 3

    def test_release_falls_back_to_none_when_empty(self):
        class Cfg(_FakeConfig):
            SENTRY_DSN = "https://x@y/1"
            SENTRY_RELEASE = ""

        with patch("sentry_sdk.init") as mock_init:
            _init_sentry(Cfg)
            assert mock_init.call_args.kwargs["release"] is None


# ---------------------------------------------------------------------------
# _strip_pii
# ---------------------------------------------------------------------------


class TestStripPii:
    def test_redacts_known_sensitive_keys(self):
        event = {
            "request": {
                "headers": {
                    "Authorization": "Bearer secret-token",
                    "Cookie": "session=abc",
                    "User-Agent": "pytest",
                },
                "data": {"refresh_token": "shh", "playlist_id": "p1"},
            },
            "extra": {
                "encrypted_refresh_token": "0xdeadbeef",
                "user_email": "user@example.com",
                "schedule_id": 11,
            },
        }
        cleaned = _strip_pii(event, hint=None)

        headers = cleaned["request"]["headers"]
        assert headers["Authorization"] == "[Filtered]"
        assert headers["Cookie"] == "[Filtered]"
        assert headers["User-Agent"] == "pytest"

        data = cleaned["request"]["data"]
        assert data["refresh_token"] == "[Filtered]"
        assert data["playlist_id"] == "p1"

        extra = cleaned["extra"]
        assert extra["encrypted_refresh_token"] == "[Filtered]"
        assert extra["user_email"] == "[Filtered]"
        assert extra["schedule_id"] == 11

    def test_passes_clean_events_through(self):
        event = {
            "request": {"headers": {"User-Agent": "pytest"}},
            "extra": {"schedule_id": 7, "tracks_total": 241},
        }
        cleaned = _strip_pii(event, hint=None)
        assert cleaned["request"]["headers"]["User-Agent"] == "pytest"
        assert cleaned["extra"] == {
            "schedule_id": 7,
            "tracks_total": 241,
        }

    def test_handles_nested_lists_and_dicts(self):
        event = {
            "extra": {
                "items": [
                    {"refresh_token": "x", "name": "ok"},
                    {"playlist_id": "p"},
                ]
            }
        }
        cleaned = _strip_pii(event, hint=None)
        items = cleaned["extra"]["items"]
        assert items[0]["refresh_token"] == "[Filtered]"
        assert items[0]["name"] == "ok"
        assert items[1]["playlist_id"] == "p"

    def test_non_dict_event_returns_unchanged(self):
        assert _strip_pii("not a dict", hint=None) == "not a dict"
        assert _strip_pii(None, hint=None) is None


# ---------------------------------------------------------------------------
# _tag_sentry_scope (executor-side tagging)
# ---------------------------------------------------------------------------


class TestTagSentryScope:
    def test_tags_schedule_context(self):
        from shuffify.services.executors.base_executor import (
            _tag_sentry_scope,
        )

        schedule = MagicMock(
            job_type="rotate",
            target_playlist_id="wooklyn-id",
            user_id=42,
        )

        with patch("sentry_sdk.get_current_scope") as mock_scope:
            scope = MagicMock()
            mock_scope.return_value = scope

            _tag_sentry_scope(schedule, schedule_id=11)

            scope.set_tag.assert_any_call("schedule_id", 11)
            scope.set_tag.assert_any_call("job_type", "rotate")
            scope.set_tag.assert_any_call("playlist_id", "wooklyn-id")
            scope.set_user.assert_called_once_with({"id": "42"})

    def test_handles_missing_schedule(self):
        from shuffify.services.executors.base_executor import (
            _tag_sentry_scope,
        )

        with patch("sentry_sdk.get_current_scope") as mock_scope:
            scope = MagicMock()
            mock_scope.return_value = scope

            _tag_sentry_scope(None, schedule_id=99)

            scope.set_tag.assert_called_once_with("schedule_id", 99)
            scope.set_user.assert_not_called()

    def test_silent_when_scope_call_raises(self):
        """Tagging must never break job execution."""
        from shuffify.services.executors.base_executor import (
            _tag_sentry_scope,
        )

        schedule = MagicMock(
            job_type="rotate",
            target_playlist_id="p",
            user_id=1,
        )
        with patch(
            "sentry_sdk.get_current_scope",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise.
            _tag_sentry_scope(schedule, schedule_id=1)


# ---------------------------------------------------------------------------
# _strip_pii -- free-text (value-level) scrubbing
# ---------------------------------------------------------------------------


class TestStripPiiFreeText:
    """Value-level scrubbing of secrets no key names (#514).

    Key-based redaction cannot see a secret interpolated into a string. These
    cover the three surfaces the scrubber previously documented as uncovered
    -- log message text, exception strings, breadcrumb messages -- plus the
    string leaves the same walk now reaches everywhere else.
    """

    # Structurally realistic, obviously fake. Spotify access tokens carry a
    # BQ prefix and refresh tokens an AQ prefix, both far longer than an ID.
    ACCESS_TOKEN = "BQDfaketokenAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    REFRESH_TOKEN = "AQDfakerefreshAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    # A real playlist ID shape -- diagnostic content that must survive.
    PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"

    def test_token_interpolated_into_log_message_is_redacted(self):
        """logger.error(f"...{token}") must not egress the token."""
        event = {
            "logentry": {
                "message": f"Token exchange failed: {self.ACCESS_TOKEN}",
                "formatted": f"Token exchange failed: {self.ACCESS_TOKEN}",
            }
        }
        cleaned = _strip_pii(event, hint=None)

        assert self.ACCESS_TOKEN not in cleaned["logentry"]["message"]
        assert self.ACCESS_TOKEN not in cleaned["logentry"]["formatted"]
        # The diagnostic half of the message survives.
        assert "Token exchange failed" in cleaned["logentry"]["message"]

    def test_oauth_shaped_token_in_logentry_params_is_redacted(self):
        """The SHAPE locator reaches params -- and that is all this proves.

        Renamed from test_token_in_logentry_params_is_redacted. The old name and
        docstring claimed the params *surface* was validated; only the OAuth
        shape was. The value here matches ``_SENTRY_OAUTH_TOKEN_RE``, so the
        assertion is satisfied by the shape rule alone and would still pass if
        every other locator were deleted. Swap this value for a bare unshaped
        string and it leaks -- which is exactly what the bound test below
        records.

        Kept rather than deleted because it is the only test for this genuine
        cell (OAuth shape x params). The two tests that actually discriminate
        params handling are the labelled/Bearer pair below.
        """
        event = {
            "logentry": {
                "message": "Token refresh failed for %s",
                "params": [self.REFRESH_TOKEN],
            }
        }
        cleaned = _strip_pii(event, hint=None)

        assert self.REFRESH_TOKEN not in str(cleaned["logentry"]["params"])

    def test_token_interpolated_into_exception_string_is_redacted(self):
        """exception.values[].value is free text and must be scrubbed."""
        event = {
            "exception": {
                "values": [
                    {
                        "type": "SpotifyTokenError",
                        "value": f"refresh rejected: {self.REFRESH_TOKEN}",
                    }
                ]
            }
        }
        cleaned = _strip_pii(event, hint=None)

        value = cleaned["exception"]["values"][0]["value"]
        assert self.REFRESH_TOKEN not in value
        assert "refresh rejected" in value
        # Untouched neighbours stay untouched.
        assert cleaned["exception"]["values"][0]["type"] == "SpotifyTokenError"

    def test_token_in_breadcrumb_message_is_redacted(self):
        event = {
            "breadcrumbs": {
                "values": [
                    {
                        "category": "auth",
                        "message": f"exchanging code for {self.ACCESS_TOKEN}",
                    }
                ]
            }
        }
        cleaned = _strip_pii(event, hint=None)

        assert self.ACCESS_TOKEN not in cleaned["breadcrumbs"]["values"][0]["message"]

    def test_breadcrumbs_as_bare_list_are_scrubbed(self):
        """Older payload shape: breadcrumbs is a list, not {"values": [...]}."""
        event = {
            "breadcrumbs": [
                {"message": f"token={self.ACCESS_TOKEN}"},
            ]
        }
        cleaned = _strip_pii(event, hint=None)

        assert self.ACCESS_TOKEN not in cleaned["breadcrumbs"][0]["message"]

    def test_repr_of_token_dict_in_message_is_redacted_by_label_alone(self):
        """The named regression, with the shape coincidence removed.

        The original version of this test used ACCESS_TOKEN/REFRESH_TOKEN, which
        are OAuth-shaped AND sit under ``access_token``/``refresh_token`` -- both
        on ``_SENTRY_PII_DENYLIST``. It therefore passed for two independent
        reasons at once and could not tell you which locator worked, or whether
        either alone sufficed. Neutralising the shape regexes left it green.

        This half keeps the denylisted keys and drops the shape, so only the
        LABEL locator can satisfy it.
        """
        event = {
            "logentry": {
                "message": (
                    "Token exchange failed: "
                    "{'access_token': 'EXAMPLE-not-a-real-access-token', "
                    "'expires_in': 3600, "
                    "'refresh_token': 'EXAMPLE-not-a-real-refresh-token'}"
                )
            }
        }
        cleaned = _strip_pii(event, hint=None)

        message = cleaned["logentry"]["message"]
        assert "EXAMPLE-not-a-real-access-token" not in message
        assert "EXAMPLE-not-a-real-refresh-token" not in message
        # Non-sensitive fields of the same dict stay readable.
        assert "3600" in message

    def test_repr_of_token_dict_in_message_is_redacted_by_shape_alone(self):
        """The other half: shape with no denylisted key to ride on.

        ``payload_id`` is on neither denylist, so the label locator cannot fire
        and only the OAuth shape rule can satisfy this. Together with the test
        above, the pair distinguishes which locator does the work -- which the
        single doubly-covered test could not.
        """
        event = {
            "logentry": {
                "message": (f"Token exchange failed: {{'payload_id': '{self.ACCESS_TOKEN}', 'expires_in': 3600}}")
            }
        }
        cleaned = _strip_pii(event, hint=None)

        message = cleaned["logentry"]["message"]
        assert self.ACCESS_TOKEN not in message
        assert "3600" in message

    # test_repr_of_token_dict_in_message_is_redacted was REPLACED by the two
    # tests above, not supplemented. It used OAuth-shaped values under
    # denylisted keys, so shape and label each satisfied it independently;
    # neutralising the shape regexes left it green. Keeping it alongside the
    # split pair would add no information and would still read as coverage it
    # never had. Its named regression -- the whole-dict repr -- is asserted by
    # both halves, each with one locator isolated.

    def test_query_string_credential_in_message_is_redacted(self):
        """An OAuth callback URL logged whole carries ?code=..."""
        event = {
            "logentry": {
                "message": (
                    "callback hit: /callback?code=AQDfakeauthcodeAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&state=xyz"
                )
            }
        }
        cleaned = _strip_pii(event, hint=None)

        message = cleaned["logentry"]["message"]
        assert "AQDfakeauthcode" not in message
        assert "state=xyz" in message

    # The three tests below deliberately use secrets that do NOT match the
    # BQ/AQ shape rule, so each isolates one locator. Without them the
    # shape rule alone satisfies every assertion above and the label and
    # Bearer locators could be deleted with the suite still green.

    def test_labelled_secret_without_token_shape_is_redacted(self):
        """A client_secret is caught by its label, not by its shape."""
        event = {"logentry": {"message": "exchange rejected: client_secret=EXAMPLE-not-a-real-client-secret"}}
        cleaned = _strip_pii(event, hint=None)

        assert "EXAMPLE-not-a-real-client-secret" not in cleaned["logentry"]["message"]
        assert "exchange rejected" in cleaned["logentry"]["message"]

    def test_bearer_value_without_token_shape_is_redacted(self):
        """Bearer names its own value -- no key and no recognisable shape."""
        event = {"logentry": {"message": "retrying with Bearer EXAMPLE-not-a-real-bearer-value"}}
        cleaned = _strip_pii(event, hint=None)

        assert "EXAMPLE-not-a-real-bearer-value" not in cleaned["logentry"]["message"]
        assert "retrying with Bearer" in cleaned["logentry"]["message"]

    # The two below mirror that pair at the params position, for the same
    # reason: the only pre-existing params test used an OAuth-shaped value, so
    # it was satisfied by the shape rule and said nothing about params. These
    # use values the shape rule cannot match, so each isolates one locator and
    # would go red if that locator were removed.

    def test_labelled_secret_without_token_shape_in_params_is_redacted(self):
        """A client_secret in params is caught by its label, not its shape."""
        event = {
            "logentry": {
                "message": "exchange rejected: %s",
                "params": ["client_secret=EXAMPLE-not-a-real-client-secret"],
            }
        }
        cleaned = _strip_pii(event, hint=None)

        params = str(cleaned["logentry"]["params"])
        assert "EXAMPLE-not-a-real-client-secret" not in params
        assert "client_secret" in params

    def test_bearer_value_without_token_shape_in_params_is_redacted(self):
        """Bearer names its own value in params -- no key, no shape."""
        event = {
            "logentry": {
                "message": "retrying with %s",
                "params": ["Bearer EXAMPLE-not-a-real-bearer-value"],
            }
        }
        cleaned = _strip_pii(event, hint=None)

        params = str(cleaned["logentry"]["params"])
        assert "EXAMPLE-not-a-real-bearer-value" not in params
        assert "Bearer" in params

    def test_unshaped_secret_in_params_survives_the_best_effort_bound(self):
        """A secret with NO shape, NO label and NO registration is NOT redacted.

        This test asserts the leak on purpose. It is the executable form of the
        bound ``_strip_pii`` already documents in prose at
        ``shuffify/__init__.py:628-633`` -- value matching is "best-effort", "a
        backstop for the credential someone interpolates into a message, not a
        licence to do so". The four value locators are registered-secret,
        Bearer, labelled, and OAuth/Fernet shape; a value that is none of those
        has no locator at all, so it passes through untouched.

        Keeping that as prose only means the day someone claims the redactor
        covers arbitrary secrets, nothing contradicts them. If the walk is ever
        extended to close this gap, THIS TEST SHOULD FAIL -- invert it and it
        becomes the regression guard. Do not "fix" it by deleting it.

        One position is enough: ``_scrub_text`` is a pure function of its string
        argument with no per-position branching, so the gap is a property of the
        shape, not of params.
        """
        event = {
            "logentry": {
                "message": "sync failed for %s (attempt %s)",
                "params": ["unshapedsecretvalue12345", "3"],
            }
        }
        cleaned = _strip_pii(event, hint=None)

        params = cleaned["logentry"]["params"]
        # (1) the bare secret is still there -- current behaviour, not aspiration
        assert "unshapedsecretvalue12345" in str(params)
        # (2) a neighbouring non-secret survives untouched, so this is not
        #     vacuously passing on an event the redactor never walked
        assert "3" in str(params)
        assert params[1] == "3"

    def test_labelled_secret_in_dict_repr_is_redacted(self):
        """Quoted key/value pairs from a repr'd dict are labelled too."""
        event = {"exception": {"values": [{"value": "auth payload {'password': 'hunter2pass', 'retries': 2}"}]}}
        cleaned = _strip_pii(event, hint=None)

        value = cleaned["exception"]["values"][0]["value"]
        assert "hunter2pass" not in value
        assert "'retries': 2" in value

    def test_top_level_message_is_scrubbed(self):
        """capture_message() writes event["message"], not event["logentry"]."""
        event = {"message": f"manual capture: {self.ACCESS_TOKEN}"}
        cleaned = _strip_pii(event, hint=None)

        assert self.ACCESS_TOKEN not in cleaned["message"]
        assert "manual capture" in cleaned["message"]

    def test_secret_under_a_harmless_key_is_scrubbed(self):
        """Any string leaf the walk reaches, not only the named surfaces.

        These four nodes are walked for their sensitive *keys* already; the
        secret here sits in the text of a value whose key is innocuous, which
        is what the enumerated-field version of this fix missed.
        """
        event = {
            "extra": {"detail": f"exchange failed for code={self.ACCESS_TOKEN}"},
            "contexts": {"trace": {"note": f"Bearer {self.ACCESS_TOKEN}"}},
            "request": {"query_string": f"code={self.ACCESS_TOKEN}&state=xyz"},
            "breadcrumbs": {"values": [{"data": {"url": f"/token?code={self.ACCESS_TOKEN}"}}]},
        }
        cleaned = json.dumps(_strip_pii(event, hint=None))

        assert self.ACCESS_TOKEN not in cleaned
        assert "state=xyz" in cleaned

    def test_diagnostic_identifiers_survive(self):
        """Redaction must not gut the payload Sentry exists to show."""
        event = {
            "logentry": {
                "message": (
                    f"shuffle failed for playlist {self.PLAYLIST_ID} "
                    "(status_code=429, schedule_id=11, tracks_total=241)"
                )
            },
            "exception": {
                "values": [
                    {
                        "type": "SpotifyRateLimitError",
                        "value": "429 Too Many Requests, retry after 30s",
                    }
                ]
            },
        }
        cleaned = _strip_pii(event, hint=None)

        message = cleaned["logentry"]["message"]
        assert self.PLAYLIST_ID in message
        assert "429" in message
        assert "schedule_id=11" in message
        assert "tracks_total=241" in message
        assert cleaned["exception"]["values"][0]["value"] == "429 Too Many Requests, retry after 30s"

    def test_missing_free_text_surfaces_are_tolerated(self):
        """Events without logentry/exception/breadcrumbs pass through."""
        event = {"extra": {"schedule_id": 7}}
        assert _strip_pii(event, hint=None)["extra"] == {"schedule_id": 7}

        malformed = {
            "logentry": "not a dict",
            "exception": {"values": "not a list"},
            "breadcrumbs": 3,
        }
        # Must not raise.
        _strip_pii(malformed, hint=None)


class TestKnownSecretScrubbing:
    """Exact-value redaction of secrets the process already holds (#514)."""

    def test_client_secret_is_redacted_from_free_text(self):
        class Cfg(_FakeConfig):
            SPOTIFY_CLIENT_SECRET = "s3cr3t-client-value"

        _register_sentry_secret_values(Cfg)

        event = {"logentry": {"message": "auth failed using s3cr3t-client-value as the secret"}}
        cleaned = _strip_pii(event, hint=None)

        assert "s3cr3t-client-value" not in cleaned["logentry"]["message"]
        assert "auth failed using" in cleaned["logentry"]["message"]

    def test_secret_key_is_redacted_from_exception_text(self):
        class Cfg(_FakeConfig):
            SECRET_KEY = "flask-signing-key-abcdef"

        _register_sentry_secret_values(Cfg)

        event = {"exception": {"values": [{"value": "bad signature for flask-signing-key-abcdef"}]}}
        cleaned = _strip_pii(event, hint=None)

        assert "flask-signing-key-abcdef" not in cleaned["exception"]["values"][0]["value"]

    def test_short_or_empty_config_values_are_not_registered(self):
        """A blank or trivially short secret must not redact everything."""

        class Cfg(_FakeConfig):
            SPOTIFY_CLIENT_SECRET = ""
            SECRET_KEY = "dev"

        _register_sentry_secret_values(Cfg)

        event = {"logentry": {"message": "dev server started"}}
        cleaned = _strip_pii(event, hint=None)

        assert cleaned["logentry"]["message"] == "dev server started"

    def test_init_registers_config_secrets(self):
        class Cfg(_FakeConfig):
            SENTRY_DSN = "https://x@y/1"
            SPOTIFY_CLIENT_SECRET = "registered-via-init-secret"

        with patch("sentry_sdk.init"):
            _init_sentry(Cfg)

        event = {"logentry": {"message": "leak registered-via-init-secret here"}}
        assert "registered-via-init-secret" not in _strip_pii(event, hint=None)["logentry"]["message"]


class TestSecretPolicyDoesNotRot:
    """Guards on the two places this fix can silently stop covering things."""

    # Config attributes whose *name* looks secret-shaped. Adding
    # SPOTIFY_CLIENT_SECRET_V2 to config.py and forgetting to register it is a
    # leak every other layer reports as green.
    SECRET_SHAPED = re.compile(r"SECRET|TOKEN|KEY|PASSWORD|DSN|URL|URI|CRED", re.I)

    # Deliberately not secrets. Each entry is a decision, not an oversight.
    NOT_SECRETS = {
        "CACHE_KEY_PREFIX",  # a namespace string, e.g. "shuffify:cache:"
        "SESSION_KEY_PREFIX",  # ditto
        "SPOTIFY_REDIRECT_URI",  # public; registered in the Spotify dashboard
        "SENTRY_DSN",  # write-only ingest key, public by Sentry's own design
    }

    def test_every_secret_shaped_config_attr_is_classified(self):
        from config import Config

        classified = set(_SENTRY_SECRET_CONFIG_ATTRS) | set(_SENTRY_SECRET_URL_CONFIG_ATTRS) | self.NOT_SECRETS
        unclassified = sorted(
            attr
            for attr in dir(Config)
            if attr.isupper() and self.SECRET_SHAPED.search(attr) and attr not in classified
        )

        assert not unclassified, (
            f"{unclassified} look secret-shaped but are neither registered for redaction nor acknowledged as non-secret"
        )

    def test_derived_key_pattern_agrees_with_the_predicate(self):
        """The regex must open a match on every key the predicate calls sensitive.

        The pattern is a second encoding of the same policy. Over-matching is
        visible (something harmless gets redacted); under-matching is silent,
        and no other test would see it.
        """
        probe = re.compile(_sentry_sensitive_key_pattern() + "$", re.IGNORECASE)

        for key in list(_SENTRY_PII_DENYLIST) + list(_SENTRY_PII_EXACT_KEYS):
            for candidate in (key, key.upper(), f"spotify_{key}", f"{key}_1"):
                if _sentry_key_is_sensitive(candidate):
                    assert probe.match(candidate), f"pattern misses {candidate!r}"

    def test_exact_keys_keep_their_boundaries_in_text(self):
        """ "code" must not redact status_code -- the reason the exact list exists."""
        assert not _sentry_key_is_sensitive("status_code")

        event = {"logentry": {"message": "request failed status_code=429"}}
        cleaned = _strip_pii(event, hint=None)

        assert "status_code=429" in cleaned["logentry"]["message"]


# ---------------------------------------------------------------------------
# Defensive: missing sentry-sdk shouldn't crash _init_sentry
# ---------------------------------------------------------------------------


class TestInitSentryGracefulImport:
    def test_missing_sentry_sdk_returns_false(self):
        """If sentry-sdk is somehow not installed, init must not crash."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentry_sdk" or name.startswith("sentry_sdk."):
                raise ImportError("sentry_sdk not installed")
            return real_import(name, *args, **kwargs)

        class Cfg(_FakeConfig):
            SENTRY_DSN = "https://x@y/1"

        with patch.object(builtins, "__import__", side_effect=fake_import):
            assert _init_sentry(Cfg) is False
