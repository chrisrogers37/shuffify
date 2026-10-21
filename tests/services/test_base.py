"""
Tests for shared service base utilities.

Tests cover safe_commit, commit_or_stage, get_user_or_raise,
get_owned_entity and ensure_private.
"""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from shuffify.models.db import (
    User,
    WorkshopSession,
    db,
)
from shuffify.services.base import (
    commit_or_stage,
    ensure_private,
    get_owned_entity,
    get_user_or_raise,
    safe_commit,
)
from shuffify.spotify.api import SpotifyAPI


class FakeError(Exception):
    """Fake exception class for verifying exception behavior."""

    pass


class FakeNotFoundError(Exception):
    """Fake exception for not-found scenarios."""

    pass


@pytest.fixture
def test_user(app_ctx):
    """Create a test user in the database."""
    user = User(
        spotify_id="base_test_user",
        display_name="Base Test User",
    )
    db.session.add(user)
    db.session.commit()
    return user


class TestSafeCommit:
    """Tests for safe_commit utility."""

    def test_success(self, app_ctx, test_user):
        """Should commit and persist changes."""
        user2 = User(
            spotify_id="new_user",
            display_name="New User",
        )
        db.session.add(user2)
        safe_commit("create test user", FakeError)

        found = User.query.filter_by(
            spotify_id="new_user"
        ).first()
        assert found is not None
        assert found.display_name == "New User"

    def test_rollback_on_failure(
        self, app_ctx, test_user
    ):
        """Should rollback and raise on commit failure."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(FakeError, match="Failed to"):
            safe_commit("create duplicate user", FakeError)

    def test_session_clean_after_rollback(
        self, app_ctx, test_user
    ):
        """After failed safe_commit, session should be clean."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(FakeError):
            safe_commit("create duplicate", FakeError)

        # Session should be usable after rollback
        user2 = User(
            spotify_id="after_rollback",
            display_name="After Rollback",
        )
        db.session.add(user2)
        db.session.commit()
        found = User.query.filter_by(
            spotify_id="after_rollback"
        ).first()
        assert found is not None

    def test_default_exception_class(
        self, app_ctx, test_user
    ):
        """Should raise plain Exception when no class given."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(Exception, match="Failed to"):
            safe_commit("create duplicate user")

    def test_custom_exception_class(
        self, app_ctx, test_user
    ):
        """Should raise the specified custom exception."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(FakeError):
            safe_commit("create duplicate", FakeError)


class TestCommitOrStage:
    """commit=True is safe_commit; commit=False flushes and leaves the transaction to the caller."""

    def test_committing_is_safe_commit(self, app_ctx, test_user):
        db.session.add(User(spotify_id="committed", display_name="Committed"))
        commit_or_stage(True, "create a user", FakeError)
        db.session.rollback()
        assert User.query.filter_by(spotify_id="committed").first() is not None
        db.session.add(User(spotify_id="base_test_user", display_name="Duplicate"))
        with pytest.raises(FakeError, match="Failed to create a duplicate"):
            commit_or_stage(True, "create a duplicate", FakeError)
        assert User.query.filter_by(spotify_id="base_test_user").count() == 1

    def test_staging_flushes_without_committing(self, app_ctx, test_user):
        db.session.add(User(spotify_id="staged", display_name="Staged"))
        with patch.object(db.session, "commit") as commit:
            commit_or_stage(False, "stage a user", FakeError)
        commit.assert_not_called()
        assert User.query.filter_by(spotify_id="staged").one().id is not None
        db.session.rollback()
        assert User.query.filter_by(spotify_id="staged").first() is None

    def test_a_staging_error_propagates_unwrapped_and_is_not_rolled_back(self, app_ctx, test_user):
        db.session.add(User(spotify_id="base_test_user", display_name="Duplicate"))
        with patch.object(db.session, "rollback") as rollback, pytest.raises(IntegrityError):
            commit_or_stage(False, "stage a duplicate", FakeError)
        rollback.assert_not_called()
        db.session.rollback()


class TestGetUserOrRaise:
    """Tests for get_user_or_raise utility."""

    def test_returns_user_when_found(
        self, app_ctx, test_user
    ):
        """Should return the user when found."""
        user = get_user_or_raise(
            "base_test_user", FakeError
        )
        assert user is not None
        assert user.spotify_id == "base_test_user"

    def test_raises_when_not_found(self, app_ctx):
        """Should raise when user not found."""
        with pytest.raises(
            FakeError, match="User not found"
        ):
            get_user_or_raise("ghost", FakeError)

    def test_returns_none_when_no_exception_class(
        self, app_ctx
    ):
        """Should return None when no exception class."""
        result = get_user_or_raise("ghost")
        assert result is None

    def test_exception_message_contains_spotify_id(
        self, app_ctx
    ):
        """Should include spotify_id in error message."""
        with pytest.raises(
            FakeError, match="nonexistent_user"
        ):
            get_user_or_raise(
                "nonexistent_user", FakeError
            )

    def test_returns_user_without_exception_class(
        self, app_ctx, test_user
    ):
        """Should return user even when no exception class."""
        user = get_user_or_raise("base_test_user")
        assert user is not None
        assert user.spotify_id == "base_test_user"


class TestGetOwnedEntity:
    """Tests for get_owned_entity utility."""

    def test_returns_entity_when_owned(
        self, app_ctx, test_user
    ):
        """Should return entity when ownership matches."""
        ws = WorkshopSession(
            user_id=test_user.id,
            playlist_id="p1",
            session_name="Test",
        )
        ws.track_uris = ["spotify:track:a"]
        db.session.add(ws)
        db.session.commit()

        result = get_owned_entity(
            WorkshopSession,
            ws.id,
            test_user.id,
            FakeNotFoundError,
        )
        assert result.id == ws.id

    def test_raises_when_not_found(
        self, app_ctx, test_user
    ):
        """Should raise when entity does not exist."""
        with pytest.raises(FakeNotFoundError):
            get_owned_entity(
                WorkshopSession,
                99999,
                test_user.id,
                FakeNotFoundError,
            )

    def test_raises_when_wrong_owner(
        self, app_ctx, test_user
    ):
        """Should raise when user_id does not match."""
        ws = WorkshopSession(
            user_id=test_user.id,
            playlist_id="p1",
            session_name="Test",
        )
        ws.track_uris = ["spotify:track:a"]
        db.session.add(ws)
        db.session.commit()

        with pytest.raises(FakeNotFoundError):
            get_owned_entity(
                WorkshopSession,
                ws.id,
                99999,
                FakeNotFoundError,
            )

    def test_exception_message_contains_class_name(
        self, app_ctx, test_user
    ):
        """Should include entity class name in message."""
        with pytest.raises(
            FakeNotFoundError,
            match="WorkshopSession",
        ):
            get_owned_entity(
                WorkshopSession,
                99999,
                test_user.id,
                FakeNotFoundError,
            )


class TestEnsurePrivate:
    """The follow-up PUT that keeps a created playlist private."""

    def test_confirms_a_private_playlist(self):
        api = Mock(spec=SpotifyAPI)
        assert ensure_private(api, "pl1") is True
        api.update_playlist_details.assert_called_once_with("pl1", public=False)

    def test_a_failure_is_logged_and_reported_never_raised(self, caplog):
        api = Mock(spec=SpotifyAPI)
        api.update_playlist_details.side_effect = RuntimeError("timed out")
        assert ensure_private(api, "pl1") is False
        assert "Could not make playlist pl1 private" in caplog.text


class TestCommitFailureText:
    """The raised text names the operation only; the driver's message stays in the log and cause."""

    def _fail(self):
        failure = IntegrityError("INSERT INTO t (c) VALUES (?)", ("SENTINEL-VALUE",), Exception("db.sentinel.invalid"))
        return failure, patch.object(db.session, "commit", side_effect=failure)

    def test_message_has_no_driver_text_and_chains_the_cause(self, app_ctx):
        from shuffify.services.base import is_commit_failure

        failure, armed = self._fail()
        with armed, pytest.raises(FakeError) as raised:
            safe_commit("create a thing", FakeError)
        assert str(raised.value) == "Failed to create a thing"
        assert raised.value.__cause__ is failure
        assert is_commit_failure(raised.value)

    def test_failure_is_logged_with_its_traceback(self, app_ctx, caplog):
        failure, armed = self._fail()
        with armed, caplog.at_level("ERROR"), pytest.raises(FakeError):
            safe_commit("create a thing", FakeError)
        record = next(r for r in caplog.records if r.name == "shuffify.services.base")
        assert record.exc_info[1] is failure
        assert "SENTINEL-VALUE" in record.getMessage() or "db.sentinel.invalid" in record.getMessage()

    def test_deliberate_errors_are_not_commit_failures(self):
        from shuffify.services.base import is_commit_failure

        assert not is_commit_failure(FakeError("Invalid theme"))

    def test_delete_schedule_message_and_cause(self, app_ctx):
        from shuffify.models.db import Schedule
        from shuffify.services import ScheduleError, SchedulerService

        user = User(spotify_id="sched_owner", display_name="Owner")
        db.session.add(user)
        db.session.commit()
        schedule = Schedule(
            user_id=user.id, job_type="shuffle", target_playlist_id="t", target_playlist_name="T",
            algorithm_name="BasicShuffle", algorithm_params={},
        )
        db.session.add(schedule)
        db.session.commit()
        failure, armed = self._fail()
        with armed, pytest.raises(ScheduleError) as raised:
            SchedulerService.delete_schedule(schedule.id, user.id)
        assert str(raised.value) == f"Failed to delete schedule {schedule.id}"
        assert raised.value.__cause__ is failure
