"""A single Spotify budget covers the complete API request and is always reset."""


def test_api_request_has_one_bounded_spotify_budget(app):
    from shuffify.spotify.deadline import remaining_seconds

    @app.get("/api/deadline-probe")
    def deadline_probe():
        remaining = remaining_seconds()
        assert remaining is not None and 0 < remaining <= 20
        return {"success": True}

    assert remaining_seconds() is None
    response = app.test_client().get("/api/deadline-probe")
    assert response.status_code == 200
    assert remaining_seconds() is None


def test_api_deadline_is_reset_after_a_failed_view(app):
    from shuffify.spotify.deadline import remaining_seconds

    observed = []

    @app.get("/api/deadline-failure")
    def deadline_failure():
        observed.append(remaining_seconds())
        raise RuntimeError("Example failure")

    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = app.test_client().get("/api/deadline-failure")
    assert response.status_code == 500
    assert observed[0] is not None and 0 < observed[0] <= 20
    assert remaining_seconds() is None


def test_public_request_does_not_create_a_spotify_budget(app):
    from shuffify.spotify.deadline import remaining_seconds

    @app.get("/deadline-public")
    def public_probe():
        assert remaining_seconds() is None
        return "Example"

    assert app.test_client().get("/deadline-public").status_code == 200
