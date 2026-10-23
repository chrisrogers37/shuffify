"""An operation refused for an unresolved needs_attention run replies with an actionable, non-retryable 409."""

from types import SimpleNamespace

from shuffify.routes.queue import enqueue_command
from shuffify.services.operations.repository import BusyOperation, PlaylistNeedsReview


def reply(app, monkeypatch, error):
    class Repository:
        def enqueue(self, **kwargs):
            raise error

    monkeypatch.setattr("shuffify.routes.queue.OperationRepository", Repository)
    with app.test_request_context("/api/example", method="POST"):
        response, status = enqueue_command(SimpleNamespace(id=1), "example", {}, lambda storage: None)[:2]
        return response.get_json(), status


def test_refusal_names_the_blocking_run_and_is_not_retryable(app, monkeypatch):
    body, status = reply(app, monkeypatch, PlaylistNeedsReview(41))
    assert status == 409
    assert body["error_code"] == "playlist_needs_review"
    assert body["operation_id"] == 41 and body["reason"] == "needs_attention"
    assert body["retryable"] is False
    assert "Restore or resolve it first" in body["message"]


def test_a_busy_refusal_is_unchanged(app, monkeypatch):
    body, status = reply(app, monkeypatch, BusyOperation(7))
    assert (status, body["error_code"], body["retryable"], body["operation_id"]) == (409, "playlist_busy", True, 7)
