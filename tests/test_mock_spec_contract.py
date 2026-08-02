"""A mock that stands in for a real class must be spec'd.

An unspec'd ``Mock`` answers *any* attribute, so a test calling a method that
was renamed, removed, or never existed still passes -- the mock invents it.
navi proved this is not theoretical: reintroducing the real #518 rename left
all nine dispatch tests green, and only a static check caught it (#521).

Items 1 and 2 of that issue fix the instances. This is item 3, which fixes the
*default*: ``Mock()`` without ``spec=`` is the path of least resistance, so the
defect regrows at the rate people write tests unless something opposes it.

This is a RATCHET, not a clean gate. The known-unspec'd sites are listed below
with their count per file, and the count may only go DOWN. A new unspec'd
stand-in fails immediately; converting one and forgetting the baseline also
fails, so the list cannot drift out of date in either direction.
"""

import ast
import pathlib
import re

TESTS = pathlib.Path(__file__).parent

# Names that claim to stand in for one of the two classes #521 measured.
# Deliberately narrow: attribute overlap alone is not identity, since
# PlaylistService and SpotifyAPI share method names.
STAND_IN = re.compile(
    r"^(mock_)?(api|spotify_api|spotify_client|client|auth_service|auth_svc)$"
)
MOCK_CALLS = {"Mock", "MagicMock"}
# Patch targets that stand in for one of the two classes #521 measured.
PATCH_TARGET = re.compile(r"(SpotifyAPI|AuthService|spotify\.api|auth_service)")


def _is_specd(call):
    return any(k.arg in ("spec", "spec_set", "autospec") for k in call.keywords)


# file -> number of unspec'd stand-ins still to convert. May only decrease.
# Regenerate with: python -m pytest tests/test_mock_spec_contract.py -q
BASELINE = {
    "routes/test_activity_routes.py": 4,
    "routes/test_core_routes.py": 19,
    "routes/test_error_page_rendering.py": 4,
    "routes/test_schedules_routes.py": 12,
    "routes/test_settings_routes.py": 4,
    "routes/test_shuffle_routes.py": 1,
    "routes/test_snapshot_routes.py": 1,
    "routes/test_workshop_external_routes.py": 2,
    "routes/test_workshop_routes.py": 2,
    "routes/test_workshop_search_routes.py": 1,
    "services/test_auth_service.py": 23,
    "services/test_drip_now_no_side_effects.py": 1,
    "services/test_job_executor_rotate.py": 12,
    "services/test_job_executor_service.py": 2,
    "services/test_playlist_lock.py": 1,
    "services/test_verify_playlist_state.py": 14,
    "spotify/test_api.py": 37,
    "spotify/test_api_search.py": 4,
    "spotify/test_api_write_contracts.py": 18,
    "templates/test_dashboard_overlay.py": 2,
    "test_integration.py": 4,
    "test_rate_limiting.py": 1,
    "test_refresh_and_registry.py": 3,
}


def _unspecd_by_file():
    """Count unspec'd stand-ins in ALL THREE idioms.

    An earlier version of this file counted only ``name = Mock()``. navi found
    that misses two other shapes, and that they are DISJOINT populations -- a
    decorator is not an assignment, so there is zero overlap and the counts do
    not merely differ, they describe different things:

      1. ``name = Mock()``            -- assignment
      2. ``return Mock()``            -- factory/fixture return
      3. ``@patch("...SpotifyAPI")``  -- patch without autospec=True

    Shape 3 is the largest population AND the most natural way to write a new
    test, so a ratchet blind to it would have passed exactly the tests most
    likely to be written next. That is the defect this file exists to prevent,
    so leaving it out would have made this guard another check that cannot fail.
    """
    counts = {}
    for path in sorted(TESTS.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        n = 0
        for node in ast.walk(tree):
            # 1. name = Mock()
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                if (
                    getattr(call.func, "id", None) in MOCK_CALLS
                    and not _is_specd(call)
                    and any(
                        STAND_IN.match(t.id)
                        for t in node.targets
                        if isinstance(t, ast.Name)
                    )
                ):
                    n += 1
            # 2. return Mock()
            elif isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
                if getattr(node.value.func, "id", None) in MOCK_CALLS and not _is_specd(
                    node.value
                ):
                    n += 1
            # 3. @patch("...SpotifyAPI") with no autospec
            elif isinstance(node, ast.Call) and (
                getattr(node.func, "id", None) == "patch"
                or getattr(node.func, "attr", None) == "object"
            ):
                arg = node.args[0] if node.args else None
                target = arg.value if isinstance(arg, ast.Constant) else ""
                if isinstance(target, str) and PATCH_TARGET.search(target):
                    if not any(
                        k.arg in ("autospec", "spec", "spec_set", "new_callable")
                        for k in node.keywords
                    ):
                        n += 1
        if n:
            counts[str(path.relative_to(TESTS))] = n
    return counts


class TestMockSpecRatchet:
    def test_no_new_unspecd_stand_ins(self):
        """A file may improve, never regress, and never appear from nowhere."""
        actual = _unspecd_by_file()

        regressions = {
            f: (BASELINE.get(f, 0), n)
            for f, n in actual.items()
            if n > BASELINE.get(f, 0)
        }
        assert not regressions, (
            "New unspec'd Mock standing in for AuthService/SpotifyAPI.\n"
            "An unspec'd mock answers any attribute, so the test cannot fail on "
            "a rename (#521).\n"
            "Pass spec=SpotifyAPI / spec=AuthService, or create_autospec(...).\n"
            f"file -> (allowed, found): {regressions}"
        )

    def test_baseline_has_no_stale_entries(self):
        """Converting a site must shrink the baseline, so it cannot rot."""
        actual = _unspecd_by_file()
        stale = {
            f: (allowed, actual.get(f, 0))
            for f, allowed in BASELINE.items()
            if actual.get(f, 0) < allowed
        }
        assert not stale, (
            "The baseline claims more unspec'd mocks than exist -- someone "
            "converted sites without lowering it. Update BASELINE in this file "
            "to the current counts (the ratchet only tightens).\n"
            f"file -> (claimed, actual): {stale}"
        )
