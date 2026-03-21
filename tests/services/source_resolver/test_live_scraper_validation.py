"""Live validation tests for PublicScraperPathway.

These tests make REAL HTTP requests to Spotify's public web endpoints
to verify that our scraper assumptions and HTML parsing still work.

They validate:
1. Spotify embed/public pages are still accessible via HTTP
2. The HTML structure contains extractable track data
3. Our extraction strategies produce valid Spotify track URIs
4. The full PublicScraperPathway.resolve() chain works end-to-end

Run with:
    pytest tests/services/source_resolver/test_live_scraper_validation.py -v

Skip in CI (no network):
    pytest -m "not integration"

These tests use well-known Spotify editorial playlists that are
unlikely to be deleted. If a test fails, it likely means Spotify
changed their page structure and the scraper needs updating.
"""

import json
import re

import pytest
import requests

from shuffify.services.source_resolver.public_scraper_pathway import (
    PublicScraperPathway,
    EMBED_URL,
    PUBLIC_URL,
    REQUEST_HEADERS,
    REQUEST_TIMEOUT,
    _extract_uris,
    _extract_from_next_data,
    _extract_from_track_list,
    _extract_with_regex,
    NEXT_DATA_PATTERN,
    TRACK_LIST_SCRIPT_PATTERN,
)

# ======================================================================
# Well-known Spotify editorial playlists (stable, high-profile)
# ======================================================================

# "Today's Top Hits" — Spotify's flagship playlist (~50 tracks, updated daily)
TODAYS_TOP_HITS_ID = "37i9dQZF1DXcBWIGoYBM5M"

# "RapCaviar" — another major editorial playlist
RAPCAVIAR_ID = "37i9dQZF1DX0XUsuxWHRQd"

# Spotify track URI format: spotify:track:<22 alphanumeric chars>
TRACK_URI_PATTERN = re.compile(r"^spotify:track:[a-zA-Z0-9]{22}$")

# Minimum number of tracks we expect from a major editorial playlist.
# These playlists typically have 50+ tracks; if we get fewer than this
# threshold, something is likely wrong with our extraction.
MIN_EXPECTED_TRACKS = 5


# ======================================================================
# Network helpers
# ======================================================================


def _fetch_page(url: str) -> requests.Response:
    """Fetch a Spotify page with browser-like headers."""
    return requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers=REQUEST_HEADERS,
    )


def _try_fetch(url: str):
    """Attempt to fetch a URL, returning (response, None) or (None, reason).

    Separates network errors (skip) from HTTP errors (meaningful test data).
    """
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers=REQUEST_HEADERS,
        )
        return resp, None
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.ProxyError,
    ) as e:
        return None, f"Network unreachable: {e}"
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        return None, f"Request failed: {e}"


def _require_network(url: str):
    """Fetch a URL or skip the test if the network is unavailable.

    Returns the Response on success. Skips the test (not fails) if
    the network is blocked (proxy, DNS, timeout). HTTP error codes
    like 403 ARE returned — those are meaningful test results.
    """
    resp, skip_reason = _try_fetch(url)
    if resp is None:
        pytest.skip(f"Network unavailable — {skip_reason}")
    return resp


def _require_html(playlist_id: str, endpoint: str = "embed"):
    """Fetch a Spotify page and return its HTML, or skip on network failure.

    Skips on network errors. Returns HTML even for non-200 status codes
    so tests can assert on the status code themselves.
    """
    if endpoint == "embed":
        url = EMBED_URL.format(playlist_id=playlist_id)
    else:
        url = PUBLIC_URL.format(playlist_id=playlist_id)

    resp = _require_network(url)
    return resp


# ======================================================================
# Layer 1: HTTP accessibility — can we still reach the pages?
# ======================================================================


@pytest.mark.integration
class TestSpotifyEndpointAccessibility:
    """Verify Spotify embed and public pages are reachable.

    These tests catch the most fundamental failure: Spotify blocking
    our requests entirely. If these fail with non-skip errors, our
    User-Agent or request strategy needs updating.
    """

    def test_embed_endpoint_returns_200(self):
        """Spotify embed page should be publicly accessible."""
        resp = _require_html(TODAYS_TOP_HITS_ID, "embed")

        assert resp.status_code == 200, (
            f"Embed endpoint returned {resp.status_code}. "
            f"Spotify may be blocking server-side requests or "
            f"the embed URL format has changed. "
            f"Response headers: {dict(resp.headers)}"
        )
        assert len(resp.text) > 1000, (
            f"Embed page is only {len(resp.text)} bytes — "
            f"likely a redirect, CAPTCHA, or error page. "
            f"First 500 chars: {resp.text[:500]}"
        )

    def test_public_page_returns_200(self):
        """Spotify public playlist page should be accessible."""
        resp = _require_html(TODAYS_TOP_HITS_ID, "public")

        assert resp.status_code == 200, (
            f"Public page returned {resp.status_code}. "
            f"Spotify may require cookies or JS for access."
        )
        assert len(resp.text) > 1000, (
            f"Public page is only {len(resp.text)} bytes — "
            f"likely a redirect, CAPTCHA, or error page."
        )

    def test_embed_content_type_is_html(self):
        """Embed response should be HTML, not a JS bundle or redirect."""
        resp = _require_html(TODAYS_TOP_HITS_ID, "embed")
        if resp.status_code != 200:
            pytest.skip(f"Embed returned {resp.status_code}")

        content_type = resp.headers.get("Content-Type", "")
        assert "text/html" in content_type, (
            f"Expected text/html, got '{content_type}'. "
            f"Spotify may have changed the embed to a pure SPA "
            f"that requires JavaScript execution."
        )

    def test_nonexistent_playlist_yields_no_tracks(self):
        """A fake playlist ID should not produce extractable tracks."""
        resp = _require_html("0000000000000000000000", "embed")

        # Any status code is fine — the key assertion is no tracks
        uris = _extract_uris(resp.text) if resp.text else []
        assert len(uris) == 0, (
            f"Extracted {len(uris)} tracks from a nonexistent playlist: "
            f"{uris[:3]}... — extraction logic may be too aggressive."
        )


# ======================================================================
# Layer 2: HTML structure — do the pages still contain our markers?
# ======================================================================


@pytest.mark.integration
class TestSpotifyPageStructure:
    """Verify Spotify pages still contain the HTML structures we parse.

    These tests check for the presence of our parsing anchors without
    running extraction. If a structural test fails but extraction still
    works, it means we're relying on a fallback strategy.
    """

    @pytest.fixture(scope="class")
    def embed_html(self):
        resp = _require_html(TODAYS_TOP_HITS_ID, "embed")
        if resp.status_code != 200:
            pytest.skip(f"Embed returned {resp.status_code}")
        return resp.text

    @pytest.fixture(scope="class")
    def public_html(self):
        resp = _require_html(TODAYS_TOP_HITS_ID, "public")
        if resp.status_code != 200:
            pytest.skip(f"Public page returned {resp.status_code}")
        return resp.text

    def test_embed_has_script_tags(self, embed_html):
        """Embed page should contain <script> tags with data."""
        assert "<script" in embed_html, (
            "No <script> tags found in embed HTML. "
            "Spotify may have moved to a different rendering approach."
        )

    def test_at_least_one_page_has_track_markers(
        self, embed_html, public_html
    ):
        """At least one page should contain recognizable track markers.

        We look for any of our known markers across both pages.
        If NONE are found, all extraction strategies will fail.
        """
        markers = [
            "__NEXT_DATA__",
            "trackList",
            "spotify:track:",
            "/track/",
        ]

        combined = embed_html + public_html
        found = [m for m in markers if m in combined]

        assert len(found) > 0, (
            f"None of our expected markers found in either page. "
            f"Checked for: {markers}. "
            f"Spotify has changed their page structure — "
            f"the scraper needs updating. "
            f"Embed size: {len(embed_html)} bytes, "
            f"Public size: {len(public_html)} bytes."
        )

    def test_embed_has_structured_data(self, embed_html):
        """Embed page should have at least one structured data source.

        This is the most critical structural check — if this fails,
        our primary extraction strategies (1 and 2) are broken and
        we're relying on regex fallback only.
        """
        has_next_data = NEXT_DATA_PATTERN.search(embed_html) is not None
        has_track_list = "trackList" in embed_html
        has_track_uris = "spotify:track:" in embed_html

        assert has_next_data or has_track_list or has_track_uris, (
            "Embed page has no __NEXT_DATA__, no trackList, and no "
            "track URIs. All extraction strategies will fail. "
            "Spotify has fundamentally changed their embed page."
        )

    def test_next_data_is_valid_json(self, embed_html):
        """If __NEXT_DATA__ exists, verify it contains parseable JSON."""
        match = NEXT_DATA_PATTERN.search(embed_html)
        if match is None:
            pytest.skip(
                "__NEXT_DATA__ tag not present — Spotify may have "
                "removed it. Other strategies may still work."
            )

        raw = match.group(1)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            pytest.fail(
                f"__NEXT_DATA__ contains invalid JSON: {e}. "
                f"First 300 chars: {raw[:300]}"
            )

        assert isinstance(data, dict), (
            f"__NEXT_DATA__ parsed as {type(data).__name__}, expected dict."
        )

    def test_track_list_scripts_parseable(self, embed_html):
        """Script blocks with 'trackList' should contain valid JSON."""
        found_any = False

        for script_match in TRACK_LIST_SCRIPT_PATTERN.finditer(embed_html):
            content = script_match.group(1).strip()
            if "trackList" not in content:
                continue

            found_any = True
            try:
                data = json.loads(content)
                assert isinstance(data, dict), (
                    "trackList script parsed but was not a dict."
                )
            except (json.JSONDecodeError, ValueError):
                # Not all script blocks with "trackList" are pure JSON.
                # Some may be JS code containing that string. This is OK
                # as long as extraction still works.
                continue

        if not found_any:
            pytest.skip(
                "No script blocks with 'trackList' found — "
                "Spotify may use a different embed format now."
            )


# ======================================================================
# Layer 3: Extraction validation — does our logic find real tracks?
# ======================================================================


@pytest.mark.integration
class TestExtractionAgainstLiveHTML:
    """Verify our extraction functions produce valid results from real HTML.

    These are the core tests — they validate that our actual parsing
    logic works against Spotify's current page structure.
    """

    @pytest.fixture(scope="class")
    def embed_html(self):
        resp = _require_html(TODAYS_TOP_HITS_ID, "embed")
        if resp.status_code != 200:
            pytest.skip(
                f"Embed returned {resp.status_code} — "
                f"cannot test extraction."
            )
        return resp.text

    @pytest.fixture(scope="class")
    def public_html(self):
        resp = _require_html(TODAYS_TOP_HITS_ID, "public")
        if resp.status_code != 200:
            pytest.skip(f"Public page returned {resp.status_code}")
        return resp.text

    def test_extract_uris_from_embed(self, embed_html):
        """Main extraction pipeline should find tracks in embed HTML."""
        uris = _extract_uris(embed_html)

        assert len(uris) >= MIN_EXPECTED_TRACKS, (
            f"Expected at least {MIN_EXPECTED_TRACKS} tracks from "
            f"Today's Top Hits embed, got {len(uris)}. "
            f"Page size: {len(embed_html)} bytes. "
            f"Extraction is broken or Spotify changed their format."
        )

        for uri in uris:
            assert TRACK_URI_PATTERN.match(uri), (
                f"Invalid track URI format: '{uri}'. "
                f"Expected 'spotify:track:<22-char-id>'."
            )

    def test_extract_uris_from_public_page(self, public_html):
        """Extraction should also work on the public playlist page."""
        uris = _extract_uris(public_html)

        if len(uris) == 0:
            pytest.skip(
                "Public page yielded 0 tracks — may require JS "
                "rendering. The embed page is our primary source."
            )

        for uri in uris:
            assert TRACK_URI_PATTERN.match(uri), (
                f"Invalid track URI from public page: '{uri}'."
            )

    def test_no_duplicate_uris(self, embed_html):
        """Extraction should not return duplicate URIs."""
        uris = _extract_uris(embed_html)
        assert len(uris) == len(set(uris)), (
            f"Duplicate URIs in extraction result: "
            f"{len(uris)} total, {len(set(uris))} unique."
        )

    def test_individual_strategies_diagnostic(self, embed_html):
        """Diagnose which extraction strategies work against live HTML.

        At least one strategy must produce valid results. The diagnostic
        output helps pinpoint which strategy broke if the main pipeline
        starts failing.
        """
        results = {
            "next_data": _extract_from_next_data(embed_html),
            "track_list": _extract_from_track_list(embed_html),
            "regex": _extract_with_regex(embed_html),
        }

        working = {
            name: uris
            for name, uris in results.items()
            if len(uris) > 0
        }

        summary = ", ".join(
            f"{name}={len(uris)}" for name, uris in results.items()
        )

        assert len(working) > 0, (
            f"ALL extraction strategies returned 0 tracks. "
            f"Strategy results: {summary}. "
            f"Page size: {len(embed_html)} bytes. "
            f"Spotify has changed their page structure entirely."
        )

        # Validate URI format for each working strategy
        for name, uris in working.items():
            for uri in uris:
                assert TRACK_URI_PATTERN.match(uri), (
                    f"Strategy '{name}' produced invalid URI: '{uri}'."
                )

    def test_cross_playlist_consistency(self):
        """Extraction should work across different playlists.

        Tests a second playlist to ensure we're not overfitting
        to one playlist's specific page structure.
        """
        resp = _require_html(RAPCAVIAR_ID, "embed")
        if resp.status_code != 200:
            pytest.skip(f"RapCaviar embed returned {resp.status_code}")

        uris = _extract_uris(resp.text)

        assert len(uris) >= MIN_EXPECTED_TRACKS, (
            f"Expected at least {MIN_EXPECTED_TRACKS} tracks from "
            f"RapCaviar, got {len(uris)}. "
            f"Extraction may only work for specific playlists."
        )

        for uri in uris:
            assert TRACK_URI_PATTERN.match(uri), (
                f"Invalid URI from RapCaviar: '{uri}'."
            )


# ======================================================================
# Layer 4: End-to-end pathway — resolve() with real HTTP
# ======================================================================


@pytest.mark.integration
class TestPublicScraperPathwayLive:
    """Full end-to-end test of PublicScraperPathway.resolve().

    These tests exercise the complete pathway including HTTP fetching,
    strategy selection, and result construction.
    """

    def _make_source(self, playlist_id):
        from unittest.mock import Mock

        source = Mock()
        source.source_playlist_id = playlist_id
        source.source_type = "external"
        return source

    def _resolve_or_skip(self, playlist_id):
        """Run resolve() and skip if the failure is due to network issues.

        The pathway catches network errors internally and returns a
        generic "Scraping returned no tracks" message. We probe for
        network availability first to distinguish network issues
        from actual scraper failures.
        """
        # Pre-check: can we reach Spotify at all?
        url = EMBED_URL.format(playlist_id=playlist_id)
        _, skip_reason = _try_fetch(url)
        if skip_reason is not None:
            pytest.skip(f"Network unavailable — {skip_reason}")

        pathway = PublicScraperPathway()
        source = self._make_source(playlist_id)

        return pathway.resolve(source)

    def test_resolve_todays_top_hits(self):
        """Full resolve() should succeed for Today's Top Hits."""
        result = self._resolve_or_skip(TODAYS_TOP_HITS_ID)

        assert result.success is True, (
            f"resolve() failed: {result.error_message}. "
            f"The scraper pathway is broken for live Spotify pages."
        )
        assert result.pathway_name == "public_scraper"
        assert len(result.track_uris) >= MIN_EXPECTED_TRACKS, (
            f"resolve() returned only {len(result.track_uris)} tracks, "
            f"expected at least {MIN_EXPECTED_TRACKS}."
        )

        for uri in result.track_uris:
            assert TRACK_URI_PATTERN.match(uri), (
                f"resolve() returned invalid URI: '{uri}'."
            )

    def test_resolve_rapcaviar(self):
        """Full resolve() should succeed for RapCaviar."""
        result = self._resolve_or_skip(RAPCAVIAR_ID)

        assert result.success is True, (
            f"resolve() failed for RapCaviar: {result.error_message}."
        )
        assert len(result.track_uris) >= MIN_EXPECTED_TRACKS

    def test_resolve_nonexistent_playlist_fails_gracefully(self):
        """resolve() should return failure for a fake playlist, not crash."""
        result = self._resolve_or_skip("0000000000000000000000")

        assert result.success is False, (
            "resolve() should fail for a nonexistent playlist."
        )
        assert len(result.track_uris) == 0

    def test_resolve_consistency(self):
        """Two consecutive resolves should return similar track counts.

        Guards against non-deterministic extraction caused by
        dynamic page content or A/B testing.
        """
        result1 = self._resolve_or_skip(TODAYS_TOP_HITS_ID)
        result2 = self._resolve_or_skip(TODAYS_TOP_HITS_ID)

        if not result1.success or not result2.success:
            pytest.skip("One of the resolves failed — can't compare.")

        count1 = len(result1.track_uris)
        count2 = len(result2.track_uris)

        # Allow up to 20% variance for dynamic content
        max_count = max(count1, count2)
        assert abs(count1 - count2) <= max_count * 0.2, (
            f"Inconsistent results: {count1} vs {count2} tracks. "
            f"Extraction may be non-deterministic or Spotify is "
            f"A/B testing page formats."
        )


# ======================================================================
# Layer 5: Format contract — validate the shape of what we get
# ======================================================================


@pytest.mark.integration
class TestTrackURIFormatContract:
    """Validate that extracted URIs conform to the Spotify track URI contract.

    Downstream consumers (raid executor, pending raid service,
    playlist_add_items API) depend on URIs being exactly:
        spotify:track:<22 alphanumeric characters>

    These tests ensure we don't pass malformed URIs downstream.
    """

    @pytest.fixture(scope="class")
    def live_uris(self):
        resp = _require_html(TODAYS_TOP_HITS_ID, "embed")
        if resp.status_code != 200:
            pytest.skip(f"Embed returned {resp.status_code}")
        uris = _extract_uris(resp.text)
        if not uris:
            pytest.skip("No URIs extracted — scraper may be broken")
        return uris

    def test_all_uris_have_correct_prefix(self, live_uris):
        """Every URI must start with 'spotify:track:'."""
        for uri in live_uris:
            assert uri.startswith("spotify:track:"), (
                f"URI has wrong prefix: '{uri}'"
            )

    def test_all_track_ids_are_22_base62_chars(self, live_uris):
        """Spotify track IDs are always exactly 22 base62 characters."""
        for uri in live_uris:
            track_id = uri.replace("spotify:track:", "")
            assert len(track_id) == 22, (
                f"Track ID '{track_id}' is {len(track_id)} chars, "
                f"expected 22."
            )
            assert re.match(r"^[a-zA-Z0-9]+$", track_id), (
                f"Track ID '{track_id}' contains non-alphanumeric chars."
            )

    def test_no_empty_or_whitespace_uris(self, live_uris):
        """URI list must not contain empty, None, or whitespace-padded values."""
        for i, uri in enumerate(live_uris):
            assert uri is not None, f"URI at index {i} is None"
            assert uri != "", f"URI at index {i} is empty string"
            assert uri.strip() == uri, (
                f"URI at index {i} has whitespace: '{uri}'"
            )

    def test_all_uris_are_strings(self, live_uris):
        """All URIs should be plain str, not bytes or other types."""
        for uri in live_uris:
            assert isinstance(uri, str), (
                f"URI is {type(uri).__name__}, expected str: {uri!r}"
            )
