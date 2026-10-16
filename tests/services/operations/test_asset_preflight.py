"""Known preflight read refusals retain their authored no-effect result."""

import pytest

from shuffify.models.db import ActivityLog, PlaylistPair
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistNotFoundError
from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyRateLimitError, SpotifyTokenExpiredError
from tests.services.operations.test_asset_commands import asset_operation as asset_operation


@pytest.mark.parametrize(
    "failure,status,code,retryable",
    [
        (PlaylistNotFoundError("private detail"), 404, "playlist_not_found", False),
        (PlaylistAccessError("private detail"), 403, "access_denied", False),
        (SpotifyTokenExpiredError("private token detail"), 401, "authentication_required", False),
        (SpotifyRateLimitError("private provider detail", retry_after=12), 429, "rate_limited", True),
        (SpotifyAPIError("private provider detail"), 502, "spotify_unavailable", True),
    ],
)
def test_target_preflight_failure_has_authored_status_and_zero_mutation(
    asset_operation,
    monkeypatch,
    failure,
    status,
    code,
    retryable,
):
    case = asset_operation

    def unavailable(resource):
        raise failure

    monkeypatch.setattr(case.world, "read", unavailable)
    completed = case.run(case.enqueue("setup_playlist_create", purpose="archive"))
    result = completed.journal["result"]
    assert completed.state == "failed"
    assert (result["http_status"], result["error_code"], result["retryable"]) == (status, code, retryable)
    assert result["outcome"] == "not_started" and not result["creation_unknown"]
    assert case.world.writes == [] and PlaylistPair.query.count() == ActivityLog.query.count() == 0
    assert "private" not in str(result)
    if code == "rate_limited":
        assert result["retry_after"] == 12
