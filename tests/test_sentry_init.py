"""Tests for Sentry initialization in the Flask app factory (F5)."""

from unittest.mock import patch, MagicMock

from shuffify import _init_sentry, _strip_pii


class _FakeConfig:
    """Stand-in for a config class with knobs for each test."""

    SENTRY_DSN = ""
    SENTRY_ENVIRONMENT = "production"
    SENTRY_TRACES_SAMPLE_RATE = 0.0
    SENTRY_PROFILES_SAMPLE_RATE = 0.0
    SENTRY_RELEASE = ""


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
            scope.set_tag.assert_any_call(
                "playlist_id", "wooklyn-id"
            )
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
