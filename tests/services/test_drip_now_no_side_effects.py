"""A manual "Drip Now" must not become a stored setting.

`drip_now()` used to pre-set `link.drip_enabled = True` in the session as an
implicit parameter to `execute_drip`, which gates on that flag. The mutation
was never explicitly committed -- but it sat pending on the shared session, and
the drip path commits for unrelated reasons (`_mark_dripped_as_promoted`), so
it rode along. One manual drip permanently enabled drip on the raid link.

The forced-run intent now travels in `algorithm_params`, so the link is never
touched. These pin both halves: the force still works, and the flag stays put.
"""

from unittest.mock import MagicMock, patch

import pytest

from shuffify.spotify.api import SpotifyAPI


@pytest.fixture
def seeded(db_app):
    from shuffify.models.db import RaidPlaylistLink, db
    from shuffify.services.user_service import UserService

    with db_app.app_context():
        user = UserService.upsert_from_spotify(
            {"id": "user123", "display_name": "u", "images": []}
        ).user
        db.session.add(
            RaidPlaylistLink(
                user_id=user.id,
                target_playlist_id="target1",
                target_playlist_name="Target",
                raid_playlist_id="raid1",
                raid_playlist_name="Raid",
                drip_enabled=False,
                drip_count=3,
            )
        )
        db.session.commit()
        return user.id


def _stored_drip_enabled(db_app):
    from shuffify.models.db import RaidPlaylistLink, db

    with db_app.app_context():
        db.session.expire_all()
        return (
            RaidPlaylistLink.query.filter_by(target_playlist_id="target1")
            .first()
            .drip_enabled
        )


class TestDripNowLeavesTheLinkAlone:
    def test_inline_drip_does_not_enable_drip_on_the_link(
        self, db_app, seeded
    ):
        """The regression: a one-off action must not persist a setting."""
        from shuffify.services.base import safe_commit
        from shuffify.services.raid_sync_service import RaidSyncService

        def _executor_that_commits(**_kwargs):
            # The real drip path commits the shared session for its own
            # reasons (drip_executor._mark_dripped_as_promoted). A stand-in
            # that skips the commit cannot observe a pending mutation riding
            # along, so it would pass whether or not the bug is present.
            safe_commit("mark dripped tracks as promoted")
            return {"status": "success", "tracks_added": 1}

        with db_app.app_context():
            with patch(
                "shuffify.services.executors.JobExecutorService."
                "execute_drip_for_user",
                side_effect=_executor_that_commits,
            ):
                RaidSyncService.drip_now("user123", "target1")

        assert _stored_drip_enabled(db_app) is False

    def test_pending_link_mutation_would_survive_an_unrelated_commit(
        self, db_app, seeded
    ):
        """Why the flag can't be used as a parameter: the session is shared.

        Pins the mechanism itself, so a future 'just set it on the link'
        shortcut fails here rather than in production.
        """
        from shuffify.models.db import RaidPlaylistLink
        from shuffify.services.base import safe_commit

        with db_app.app_context():
            link = RaidPlaylistLink.query.filter_by(
                target_playlist_id="target1"
            ).first()
            link.drip_enabled = True
            safe_commit("some unrelated write in the same request")

        assert _stored_drip_enabled(db_app) is True


class TestForcedDripStillRuns:
    def test_force_param_bypasses_the_drip_enabled_gate(self, db_app, seeded):
        """The feature must keep working with drip_enabled False."""
        from shuffify.enums import JobType
        from shuffify.models.db import Schedule
        from shuffify.services.executors.drip_executor import execute_drip

        schedule = Schedule(
            user_id=seeded,
            job_type=JobType.DRIP,
            target_playlist_id="target1",
            target_playlist_name="Target",
            algorithm_params={"force": True, "drip_count": 3},
            is_enabled=True,
        )
        api = MagicMock()
        api.get_playlist_tracks.return_value = []

        with db_app.app_context():
            result = execute_drip(schedule, api)

        assert result.get("skipped_reason") != "drip_disabled"

    def test_unforced_drip_still_respects_the_gate(self, db_app, seeded):
        """A scheduled drip on a disabled link must still skip."""
        from shuffify.enums import JobType
        from shuffify.models.db import Schedule
        from shuffify.services.executors.drip_executor import execute_drip

        schedule = Schedule(
            user_id=seeded,
            job_type=JobType.DRIP,
            target_playlist_id="target1",
            target_playlist_name="Target",
            algorithm_params={"drip_count": 3},
            is_enabled=True,
        )
        api = MagicMock(spec=SpotifyAPI)
        api.get_playlist_tracks.return_value = []

        with db_app.app_context():
            result = execute_drip(schedule, api)

        assert result["skipped_reason"] == "drip_disabled"

    def test_execute_drip_for_user_marks_the_run_as_forced(self, db_app, seeded):
        """The intent travels in params, not on the link."""
        from shuffify.services.executors import JobExecutorService

        with db_app.app_context():
            with patch.object(
                JobExecutorService, "_run_job", return_value={"status": "success"}
            ) as run_job:
                JobExecutorService.execute_drip_for_user(
                    user_id=seeded,
                    target_playlist_id="target1",
                    target_playlist_name="Target",
                    drip_count=3,
                )

        schedule = run_job.call_args[0][0]
        assert schedule.algorithm_params["force"] is True
        assert schedule.algorithm_params["drip_count"] == 3
