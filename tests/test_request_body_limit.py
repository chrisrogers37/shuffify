"""Request body limits.

Werkzeug leaves every body to MAX_CONTENT_LENGTH. The app-wide CSRF check
parses the form of every POST before any auth check, so a form body is held to
the smaller MAX_FORM_MEMORY_SIZE (shuffify.BoundedRequest), and any other body
to MAX_CONTENT_LENGTH, which the Workshop's JSON has to fit.
"""

import json

import pytest
from flask import request
from werkzeug.exceptions import RequestEntityTooLarge

# One track as the Workshop's preview shuffle sends it, with long metadata.
TRACK = {
    "id": "4uLU6hMCjMI75M1A2tKUQC",
    "name": "Symphony No. 9 in D Minor, Op. 125 'Choral': IV. Presto - Allegro assai (Live at the Royal Albert Hall)",
    "artists": ["Ludwig van Beethoven", "Berliner Philharmoniker", "Herbert von Karajan", "Wiener Singverein"],
    "album_name": "Beethoven: The Complete Symphonies (Remastered Deluxe Edition with Bonus Rehearsals)",
    "album_image_url": "https://i.scdn.co/image/ab67616d0000b273" + "0" * 24,
    "duration_ms": 1_440_000,
    "uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
}


@pytest.fixture
def client(app):
    app.config["WTF_CSRF_ENABLED"] = True  # as in production; TestConfig turns it off
    return app.test_client()


@pytest.mark.parametrize("content_type, accept, says", [
    ("application/x-www-form-urlencoded", "text/html", b"That request was too large to send."),
    ("application/x-www-form-urlencoded", "application/json", b'"That request is too large."'),
    ("multipart/form-data", "text/html", b"That request was too large to send."),
])
def test_a_form_over_the_form_limit_is_refused_before_it_is_parsed(app, client, content_type, accept, says):
    """Parsed, it would be a 400 for its missing CSRF token."""
    form = {"a": "x" * app.config["MAX_FORM_MEMORY_SIZE"]}

    response = client.post("/logout", data=form, content_type=content_type, headers={"Accept": accept})

    assert response.status_code == 413
    assert says in response.data


def test_the_largest_workshop_body_is_read(app):
    """The preview shuffle sends a full object per track: 10,000 long-titled ones."""
    body = json.dumps({"algorithm": "BasicShuffle", "tracks": [TRACK] * 10_000})
    assert len(body) > app.config["MAX_FORM_MEMORY_SIZE"]  # so the form limit would refuse it

    with app.test_request_context("/", method="POST", data=body, content_type="application/json"):
        assert len(request.get_json()["tracks"]) == 10_000


def test_any_body_over_the_general_limit_is_refused(app):
    body = b'"' + b"x" * app.config["MAX_CONTENT_LENGTH"] + b'"'

    with app.test_request_context("/", method="POST", data=body, content_type="application/json"):
        with pytest.raises(RequestEntityTooLarge):
            request.get_json()


def test_with_no_form_limit_a_form_is_held_to_the_general_one(app):
    app.config["MAX_FORM_MEMORY_SIZE"] = None
    body = b"a=" + b"x" * app.config["MAX_CONTENT_LENGTH"]

    with app.test_request_context("/", method="POST", data=body, content_type="application/x-www-form-urlencoded"):
        with pytest.raises(RequestEntityTooLarge):
            request.form.get("a")


def test_a_view_can_still_set_its_own_limit(app):
    with app.test_request_context("/", method="POST", data=b"[1, 2]", content_type="application/json"):
        request.max_content_length = 3

        with pytest.raises(RequestEntityTooLarge):
            request.get_json()
