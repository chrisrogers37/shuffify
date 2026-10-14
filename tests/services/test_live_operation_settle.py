"""A rollback restores every playlist, then watches those whose failed write may still land.

Spotify can apply a request after the client stopped waiting for it, so a rollback
keeps watching the playlist after restoring it; otherwise a late write could truncate
or reorder the playlist behind a "restored" report. These tests run the real
journal against a Spotify whose job write is lost in flight, and let that write,
or a restoration whose own answer was lost, land while the rollback waits: in the
middle of its restoration, in the middle of a check's read, or after a first
repair. The watch lasts the whole window, so a later landing is caught as well.
"""

import logging
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from shuffify.services.executors.base_executor import PlaylistVerificationError
from shuffify.services.live_operation_service import LiveOperation
from shuffify.services.workshop_commit_service import IncompletePlaylistRead, ObservedPlaylist
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyPartialBatchError,
    SpotifyQuotaExceededError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)

LOGGER = "shuffify.services.live_operation_service"
PLAYLIST = "playlist-1"
ARCHIVE = "archive-1"
STEP = "shuffle"
BASELINE = [f"spotify:track:{name}" for name in "abcd"]
SHUFFLED = BASELINE[::-1]
ROTATED = SHUFFLED[1:] + SHUFFLED[:1]
ARCHIVE_BASELINE = [f"spotify:track:{name}" for name in "wxyz"]


def applied(spotify, uris):
    spotify.uris = list(uris)


def partly_applied(spotify, uris):
    spotify.uris = list(uris[:2])


def ignored(spotify, uris):
    """Acknowledged, but the playlist stays as it was."""


def then_lands(late):
    """Applied, and then the write Spotify never answered lands behind it."""

    def outcome(spotify, uris):
        applied(spotify, uris)
        spotify.uris = list(late)

    return outcome


def fails_with(cause, confirmed=0):
    """A write that fails with ``cause``; with ``confirmed``, a first batch of that many tracks was confirmed first."""

    def outcome(spotify, uris):
        if confirmed:
            spotify.uris = list(uris[:confirmed])
        raise SpotifyPartialBatchError(
            playlist_id=PLAYLIST,
            method="update",
            completed_batches=1 if confirmed else 0,
            total_batches=2 if confirmed else 1,
            completed_uris=list(uris[:confirmed]),
            remaining_uris=list(uris[confirmed:]),
            cause=cause,
        )

    return outcome


# No reply: Spotify has not applied the write yet, and may after the client gave up.
lost = fails_with(SpotifyWriteUnverifiedError("no reply"))
rejected = fails_with(SpotifyWriteRejectedError("refused"))
# A partial write: the first batch (two tracks) is confirmed, then the next one fails.
lost_after_first_batch = fails_with(SpotifyWriteUnverifiedError("no reply"), confirmed=2)
quota_after_first_batch = fails_with(SpotifyQuotaExceededError("over quota"), confirmed=2)
refused_after_first_batch = fails_with(SpotifyWriteRejectedError("refused"), confirmed=2)


class Spotify:
    """A playlist and an archive as Spotify holds them: the journal reads them and writes them through its API.

    ``outcomes`` is what the next writes to the playlist do, one each; after those, a write applies, as does every
    write to the archive. ``read_faults`` is what the next reads do before they answer. ``events`` lists every
    read, write and wait, in order.
    """

    def __init__(self, *outcomes):
        self.uris = list(BASELINE)
        self.archive = list(ARCHIVE_BASELINE)
        self.outcomes = list(outcomes)
        self.read_faults = []
        self.writes = []
        self.reads = 0
        self.unreadable = False
        self.events = []

    def read_complete(self, playlist_id, user_spotify_id):
        self.events.append(("read", playlist_id))
        if self.unreadable:
            raise SpotifyAPIError("Spotify did not answer")
        if self.read_faults:
            self.read_faults.pop(0)(self)
        self.reads += 1
        uris = self.archive if playlist_id == ARCHIVE else self.uris
        return ObservedPlaylist(
            name="Playlist",
            track_uris=tuple(uris),
            tracks=tuple({"uri": uri} for uri in uris),
            loaded_at=f"read {self.reads}",
        )

    def update_playlist_tracks(self, playlist_id, uris):
        self.writes.append((playlist_id, list(uris)))
        self.events.append(("write", playlist_id))
        if playlist_id == ARCHIVE:
            self.archive = list(uris)
        else:
            (self.outcomes.pop(0) if self.outcomes else applied)(self, uris)
        return True


@pytest.fixture
def settling(app_context):
    """The app with ``Config``'s rollback settings: three re-checks, five seconds apart."""
    app_context.config.update(ROLLBACK_SETTLE_CHECKS=3, ROLLBACK_SETTLE_SECONDS=5)
    return app_context


@pytest.fixture
def sleep(monkeypatch):
    """The rollback's waits, recorded rather than slept.

    ``sleep.during(n, action)`` runs ``action`` while the rollback waits for check ``n``. ``sleep.later()`` runs
    the actions of the waits it never got to, as if the clock went on after the rollback ended.
    """
    waits = Mock()
    planned = {}

    def wait(seconds):
        for action in planned.get(waits.call_count, ()):
            action()

    def during(number, action):
        planned.setdefault(number, []).append(action)

    def later():
        for number in sorted(planned):
            if number > waits.call_count:
                for action in planned[number]:
                    action()

    waits.side_effect, waits.during, waits.later = wait, during, later
    monkeypatch.setattr("shuffify.services.live_operation_service.time.sleep", waits)
    return waits


def lands_during_wait(sleep, number, spotify, uris):
    """Spotify applies the write the client gave up on while the rollback waits for check ``number``."""
    sleep.during(number, lambda: setattr(spotify, "uris", list(uris)))


def torn_by(uris):
    """A read the late write lands in the middle of: the snapshot changes under it, so it fails."""

    def fault(spotify):
        spotify.uris = list(uris)
        raise IncompletePlaylistRead()

    return fault


def unanswered(spotify):
    """A read Spotify does not answer."""
    raise SpotifyAPIError("Spotify did not answer")


def fails_during_wait(sleep, number, spotify, fault):
    """The read that follows the rollback's wait number ``number`` runs into ``fault``."""
    sleep.during(number, lambda: spotify.read_faults.append(fault))


def failed_job(spotify, *writes, error_code="write_unverified"):
    """The journal of a shuffle step whose last write failed, as the executor leaves it when it rolls back."""
    journal = LiveOperation(STEP, spotify, SimpleNamespace(spotify_id="owner"))
    journal.reader = spotify
    journal.plan(PLAYLIST, SHUFFLED, STEP)
    writes = writes or (SHUFFLED,)
    for uris in writes[:-1]:
        journal.write("update_playlist_tracks", PLAYLIST, uris, STEP)
    with pytest.raises((SpotifyPartialBatchError, PlaylistVerificationError)):
        journal.write("update_playlist_tracks", PLAYLIST, writes[-1], STEP)
    (resource,) = journal.resources
    assert (resource["dispatched"], resource["status"], resource["error_code"]) == (True, "unknown", error_code)
    return journal


def failed_rotation(spotify):
    """The journal of a rotation that wrote its archive and then lost its target write, as the executor leaves it."""
    journal = LiveOperation("rotate", spotify, SimpleNamespace(spotify_id="owner"))
    journal.reader = spotify
    swapped_out, archived = BASELINE[1:], ARCHIVE_BASELINE + BASELINE[:1]
    journal.plan(PLAYLIST, swapped_out, "rotate")  # the target is planned first ...
    journal.plan(ARCHIVE, archived, "rotate")
    journal.write("update_playlist_tracks", ARCHIVE, archived, "rotate")  # ... and the archive is written first
    with pytest.raises(SpotifyPartialBatchError):
        journal.write("update_playlist_tracks", PLAYLIST, swapped_out, "rotate")
    assert journal.unconfirmed == {PLAYLIST}
    target, archive = journal.resources
    assert [(r["resource"], r["status"]) for r in (target, archive)] == [(PLAYLIST, "unknown"), (ARCHIVE, "complete")]
    return journal


@pytest.mark.parametrize(
    "check, landed",
    [
        pytest.param(2, SHUFFLED, id="whole-write-found-on-second-check"),
        pytest.param(1, SHUFFLED[:2], id="truncation-found-on-first-check"),
    ],
)
def test_a_late_write_is_restored_and_the_playlist_is_watched_to_the_end(settling, sleep, check, landed):
    spotify = Spotify(lost)
    journal = failed_job(spotify)
    lands_during_wait(sleep, check, spotify, landed)

    assert journal.compensate() is True

    # The job's own lost write, then exactly one restoration: the playlist matched the baseline when the
    # rollback began, so only the late write is undone. The checks that follow find nothing to undo.
    assert spotify.writes == [(PLAYLIST, SHUFFLED), (PLAYLIST, BASELINE)]
    assert spotify.uris == BASELINE
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE
    assert resource["restored_track_uris"] == BASELINE


def test_a_later_batch_whose_reply_was_lost_is_still_rechecked(settling, sleep):
    spotify = Spotify(lost_after_first_batch)
    journal = failed_job(spotify)
    lands_during_wait(sleep, 1, spotify, SHUFFLED)  # the lost batch lands after the first restoration

    assert journal.compensate() is True

    # The first batch had applied, so there are two restorations: one at once, one after the lost batch lands.
    assert spotify.writes == [(PLAYLIST, SHUFFLED), (PLAYLIST, BASELINE), (PLAYLIST, BASELINE)]
    assert spotify.uris == BASELINE
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE


def test_a_late_write_that_lands_during_the_restoration_is_restored_again(settling, sleep):
    spotify = Spotify(lost_after_first_batch, then_lands(SHUFFLED))
    journal = failed_job(spotify)

    assert journal.compensate() is True

    # The first batch had applied, so the rollback writes the baseline back at once. The lost batch lands right
    # behind that write, before the restoration is read back: that restoration fails to verify, which does not end
    # the rollback. The first check finds the playlist changed and restores it again; the other two find nothing.
    assert spotify.writes == [(PLAYLIST, SHUFFLED), (PLAYLIST, BASELINE), (PLAYLIST, BASELINE)]
    assert spotify.uris == BASELINE
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE
    assert resource["restored_track_uris"] == BASELINE


def test_a_late_write_that_lands_during_a_check_is_restored(settling, sleep):
    spotify = Spotify(lost)
    journal = failed_job(spotify)
    # The write lands in the middle of the second check's read: the snapshot changes under that read, so it fails.
    fails_during_wait(sleep, 2, spotify, torn_by(SHUFFLED))

    assert journal.compensate() is True

    # The failed read is not the end of the rollback: the playlist is restored from a fresh read, and the
    # last check finds nothing more.
    assert spotify.writes == [(PLAYLIST, SHUFFLED), (PLAYLIST, BASELINE)]
    assert spotify.uris == BASELINE
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE
    assert resource["restored_track_uris"] == BASELINE


def test_a_check_that_fails_to_read_an_unchanged_playlist_is_settled_by_a_restoration_that_needs_no_write(
    settling, sleep
):
    spotify = Spotify(lost)
    journal = failed_job(spotify)
    reads = spotify.reads
    fails_during_wait(sleep, 1, spotify, unanswered)

    assert journal.compensate() is True

    # Spotify answered the restoration that follows the failed check: the playlist is still at the baseline, so
    # nothing is written, and the two checks that remain read it as usual.
    assert spotify.writes == [(PLAYLIST, SHUFFLED)]
    assert spotify.reads - reads == 6  # a restoration's two reads, the same again after the failed check, two checks
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE
    assert resource["loaded_at"] == f"read {spotify.reads}"


def test_a_restoration_whose_answer_was_lost_is_watched_until_it_lands(settling, sleep):
    # The job's later batch and the rollback's own first write are both lost: two writes are in flight.
    spotify = Spotify(lost_after_first_batch, lost)
    journal = failed_job(spotify)
    lands_during_wait(sleep, 1, spotify, SHUFFLED)  # the job's batch lands first
    lands_during_wait(sleep, 2, spotify, BASELINE[:2])  # then the lost restoration lands, its first batch only

    assert journal.compensate() is True
    sleep.later()  # whatever is still in flight lands once the rollback is over

    # Once everything in flight has landed the playlist is as it began. The first check finds the job's batch and
    # restores; the second finds the truncation the lost restoration left and restores again. A watch that ended
    # after the first repair would leave that truncation behind.
    assert spotify.uris == BASELINE
    assert spotify.writes == [(PLAYLIST, SHUFFLED)] + [(PLAYLIST, BASELINE)] * 3
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE
    assert resource["restored_track_uris"] == BASELINE


def test_a_check_that_cannot_read_a_playlist_does_not_end_the_watch(settling, sleep, caplog):
    spotify = Spotify(lost)
    journal = failed_job(spotify)
    fails_during_wait(sleep, 1, spotify, unanswered)  # the first check cannot read the playlist, still unchanged
    lands_during_wait(sleep, 2, spotify, SHUFFLED)  # the job's lost write lands after that check

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert journal.compensate() is True
    sleep.later()  # whatever is still in flight lands once the rollback is over

    # Once everything in flight has landed the playlist is as it began: the failed check is restored without a
    # write, and the second check still finds the late write.
    assert spotify.uris == BASELINE
    assert spotify.writes == [(PLAYLIST, SHUFFLED), (PLAYLIST, BASELINE)]
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE
    # The failed read is logged, with its traceback.
    (record,) = [r for r in caplog.records if r.getMessage().startswith("Could not re-check")]
    assert record.name == LOGGER
    assert record.levelno == logging.WARNING
    assert record.getMessage() == f"Could not re-check playlist {PLAYLIST} after its rollback"
    assert record.exc_info is not None and isinstance(record.exc_info[1], SpotifyAPIError)  # with its traceback


def test_a_playlist_that_changes_before_every_check_is_restored_after_each_and_no_more(settling, sleep):
    spotify = Spotify(lost)
    journal = failed_job(spotify)
    for check in (1, 2, 3):
        lands_during_wait(sleep, check, spotify, SHUFFLED)

    assert journal.compensate() is True

    # One restoration per check, and the watch is over: the window is bounded, whatever lands in it.
    assert spotify.writes == [(PLAYLIST, SHUFFLED)] + [(PLAYLIST, BASELINE)] * 3
    assert spotify.uris == BASELINE
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE


def test_every_playlist_is_restored_before_any_is_watched(settling, sleep):
    spotify = Spotify(lost)
    journal = failed_rotation(spotify)
    sleep.side_effect = lambda seconds: spotify.events.append(("sleep", seconds))
    spotify.events.clear()  # the rollback's own events only

    assert journal.compensate() is True

    # The target is first in the journal and its write may still land, but the archive must not wait behind
    # the target's checks: it is restored before the first wait, and only the target is read after it.
    first_wait = spotify.events.index(("sleep", 5))
    assert ("write", ARCHIVE) in spotify.events[:first_wait]
    assert spotify.events[first_wait:] == [("sleep", 5), ("read", PLAYLIST)] * 3
    assert spotify.uris == BASELINE
    assert spotify.archive == ARCHIVE_BASELINE
    assert {r["resource"]: r["status"] for r in journal.resources} == {PLAYLIST: "restored", ARCHIVE: "restored"}


def test_a_playlist_that_never_changes_is_checked_three_times_and_left_alone(settling, sleep):
    spotify = Spotify(lost)
    journal = failed_job(spotify)

    assert journal.compensate() is True

    assert spotify.writes == [(PLAYLIST, SHUFFLED)]
    assert sleep.call_args_list == [call(5)] * 3
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE
    assert resource["loaded_at"] == f"read {spotify.reads}"  # the last check's observation


@pytest.mark.parametrize(
    "error_code, outcomes, writes",
    [
        # Spotify acknowledged the write, but the playlist read back different.
        pytest.param("verification_failed", [partly_applied], [SHUFFLED], id="read-back-differs"),
        # A second write was refused outright, after the first was applied.
        pytest.param("write_rejected", [applied, rejected], [SHUFFLED, ROTATED], id="second-write-refused"),
        # A later batch was refused after the first was confirmed. write() records every partial write as
        # unverified, but a refused batch never lands.
        pytest.param("write_unverified", [quota_after_first_batch], [SHUFFLED], id="quota-after-first-batch"),
        pytest.param("write_unverified", [refused_after_first_batch], [SHUFFLED], id="refused-after-first-batch"),
    ],
)
def test_a_failure_with_nothing_in_flight_is_restored_without_waiting(settling, sleep, error_code, outcomes, writes):
    spotify = Spotify(*outcomes)
    journal = failed_job(spotify, *writes, error_code=error_code)
    reads, written = spotify.reads, len(spotify.writes)

    assert journal.compensate() is True

    sleep.assert_not_called()
    assert spotify.reads - reads == 2  # the restoration's own read before and after its write
    assert len(spotify.writes) - written == 1
    assert spotify.uris == BASELINE
    (resource,) = journal.resources
    assert resource["status"] == "restored"
    assert resource["current_track_uris"] == BASELINE


def test_a_second_restoration_that_does_not_verify_is_reported_unverified(settling, sleep):
    spotify = Spotify(lost, ignored)  # the restoration after the late write is acknowledged, not applied
    journal = failed_job(spotify)
    lands_during_wait(sleep, 1, spotify, SHUFFLED)

    assert journal.compensate() is False

    assert spotify.writes == [(PLAYLIST, SHUFFLED), (PLAYLIST, BASELINE)]
    (resource,) = journal.resources
    assert resource["status"] == "unknown"
    assert resource["error_code"] == "rollback_unverified"


def test_a_check_that_cannot_read_the_playlist_leaves_the_rollback_unverified(settling, sleep):
    spotify = Spotify(lost)
    journal = failed_job(spotify)
    sleep.side_effect = lambda seconds: setattr(spotify, "unreadable", True)

    assert journal.compensate() is False

    # The failed check read is a reason to restore again; that restoration cannot read the playlist either.
    assert spotify.writes == [(PLAYLIST, SHUFFLED)]
    assert sleep.call_args_list == [call(5)]
    (resource,) = journal.resources
    assert resource["status"] == "unknown"
    assert resource["error_code"] == "rollback_unverified"


def test_without_settle_checks_the_rollback_does_not_wait(app_context, sleep):
    # The testing config's default: unit tests that roll back an unconfirmed write don't wait.
    assert app_context.config["ROLLBACK_SETTLE_CHECKS"] == 0
    spotify = Spotify(lost)
    journal = failed_job(spotify)

    assert journal.compensate() is True

    sleep.assert_not_called()
    assert spotify.writes == [(PLAYLIST, SHUFFLED)]
    (resource,) = journal.resources
    assert resource["status"] == "restored"


def test_without_settle_checks_a_restoration_that_does_not_verify_is_reported_unverified(app_context, sleep):
    assert app_context.config["ROLLBACK_SETTLE_CHECKS"] == 0
    spotify = Spotify(lost_after_first_batch, ignored)  # the restoration is acknowledged, not applied
    journal = failed_job(spotify)

    assert journal.compensate() is False

    # Nothing watches the playlist afterwards, so an unconfirmed write earns no benefit of the doubt.
    sleep.assert_not_called()
    assert spotify.writes == [(PLAYLIST, SHUFFLED), (PLAYLIST, BASELINE)]
    (resource,) = journal.resources
    assert resource["status"] == "unknown"
    assert resource["error_code"] == "rollback_unverified"


def test_a_rollback_that_ends_unverified_is_logged(app_context, caplog):
    spotify = Spotify(partly_applied, ignored)  # the read-back differs; the restoration is acknowledged, not applied
    journal = failed_job(spotify, error_code="verification_failed")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert journal.compensate() is False

    (record,) = [r for r in caplog.records if r.name == LOGGER]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == f"Rollback of playlist {PLAYLIST} is not verified"
    assert str(record.exc_info[1]) == "Restoration not verified"  # the reason is in the log
