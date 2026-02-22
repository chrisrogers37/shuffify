# Phase 5: Schema Tests, Dead Code, and Dependency Updates -- Remediation Plan

**Status**: ✅ COMPLETE
**Started**: 2026-02-19
**Completed**: 2026-02-19
**PR**: #87

## PR Metadata

| Field | Value |
|-------|-------|
| **PR title** | "Chore: Add schema tests, remove dead code, update dependencies" |
| **Risk level** | Low |
| **Estimated effort** | 2 hours |
| **Files created** | `tests/schemas/test_settings_requests.py`, `tests/schemas/test_snapshot_requests.py` |
| **Files modified** | `shuffify/services/upstream_source_service.py` (1 method removed), `requirements/base.txt`, `requirements/dev.txt` |
| **Dependencies** | None (fully independent of all other phases) |
| **Blocks** | None |

---

## Part A: Missing Schema Tests

### Context

There are currently 6 schema modules in `shuffify/schemas/` but only 4 have dedicated test files. The two missing test files are for `settings_requests.py` and `snapshot_requests.py`. These schemas ARE used in production routes (the settings route at `/Users/chris/Projects/shuffify/shuffify/routes/settings.py` line 133 and the snapshots route at `/Users/chris/Projects/shuffify/shuffify/routes/snapshots.py` line 89) but have zero unit-level coverage.

### Existing test file conventions (follow these exactly)

Based on analysis of all 5 existing schema test files, the following conventions MUST be followed:

1. **Import pattern**: Import directly from the specific schema module, not from `shuffify.schemas`:
   ```python
   from shuffify.schemas.settings_requests import UserSettingsUpdateRequest
   ```

2. **Test class naming**: Use `TestXxxValid` / `TestXxxInvalid` class pairs, or use a single class per schema model when the model is simple.

3. **Docstring style**: Module-level docstring describes what schemas are tested. Each class gets a one-line docstring. Each test method gets a one-line docstring starting with a verb phrase.

4. **Section separators**: Use `# ===...===` comment blocks between test classes (see `/Users/chris/Projects/shuffify/tests/schemas/test_schedule_requests.py` line 21, 98, 193).

5. **Assertion style**: For valid inputs, assert field values directly. For invalid inputs, use `pytest.raises(ValidationError)` and optionally check error message content with string assertions.

6. **No app context needed**: Schema tests do NOT need Flask app context. They are pure Pydantic model tests. However, the `UserSettingsUpdateRequest` validator calls `ShuffleRegistry.get_available_algorithms()`, which imports algorithm classes. These imports are module-level and do not require Flask context, so no special fixture is needed.

7. **Helper functions**: Use `_base_create_kwargs(**overrides)` style helpers for models with many required fields (see `test_schedule_requests.py` line 24-35). For simpler models, construct directly.

---

### Step A1: Create `tests/schemas/test_settings_requests.py`

**File to create**: `/Users/chris/Projects/shuffify/tests/schemas/test_settings_requests.py`

**Schema under test**: `/Users/chris/Projects/shuffify/shuffify/schemas/settings_requests.py` (lines 1-73)

The `UserSettingsUpdateRequest` model has:
- 6 optional fields (lines 18-31): `default_algorithm`, `theme`, `notifications_enabled`, `auto_snapshot_enabled`, `max_snapshots_per_playlist`, `dashboard_show_recent_activity`
- `validate_algorithm` validator (lines 33-52): strips whitespace, converts empty/whitespace-only to `None`, validates against `ShuffleRegistry.get_available_algorithms().keys()` which returns all 7 algorithm keys including hidden `TempoGradientShuffle`
- `validate_theme` validator (lines 54-69): strips + lowercases, validates against `{"light", "dark", "system"}`
- `max_snapshots_per_playlist` has `ge=1, le=50` constraint (line 27)
- `Config.extra = "ignore"` (lines 71-72)

**Valid algorithm names** (from registry at `/Users/chris/Projects/shuffify/shuffify/shuffle_algorithms/registry.py` lines 15-23):
`BasicShuffle`, `BalancedShuffle`, `PercentageShuffle`, `StratifiedShuffle`, `ArtistSpacingShuffle`, `AlbumSequenceShuffle`, `TempoGradientShuffle`

**Exact content to write**:

```python
"""
Tests for user settings request validation schemas.

Tests UserSettingsUpdateRequest Pydantic model for settings
form payload validation.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.settings_requests import (
    UserSettingsUpdateRequest,
)


# =============================================================================
# UserSettingsUpdateRequest — Valid inputs
# =============================================================================


class TestUserSettingsUpdateRequestValid:
    """Tests for valid UserSettingsUpdateRequest payloads."""

    def test_all_fields_provided(self):
        """Should accept a full settings update."""
        req = UserSettingsUpdateRequest(
            default_algorithm="BasicShuffle",
            theme="dark",
            notifications_enabled=True,
            auto_snapshot_enabled=True,
            max_snapshots_per_playlist=10,
            dashboard_show_recent_activity=False,
        )
        assert req.default_algorithm == "BasicShuffle"
        assert req.theme == "dark"
        assert req.notifications_enabled is True
        assert req.auto_snapshot_enabled is True
        assert req.max_snapshots_per_playlist == 10
        assert req.dashboard_show_recent_activity is False

    def test_partial_update_algorithm_only(self):
        """Should accept partial update with just algorithm."""
        req = UserSettingsUpdateRequest(
            default_algorithm="BalancedShuffle",
        )
        assert req.default_algorithm == "BalancedShuffle"
        assert req.theme is None
        assert req.notifications_enabled is None

    def test_partial_update_theme_only(self):
        """Should accept partial update with just theme."""
        req = UserSettingsUpdateRequest(theme="light")
        assert req.theme == "light"
        assert req.default_algorithm is None

    def test_partial_update_booleans_only(self):
        """Should accept partial update with boolean fields."""
        req = UserSettingsUpdateRequest(
            notifications_enabled=False,
            auto_snapshot_enabled=True,
        )
        assert req.notifications_enabled is False
        assert req.auto_snapshot_enabled is True

    def test_empty_request_all_none(self):
        """Should accept request with no fields set."""
        req = UserSettingsUpdateRequest()
        assert req.default_algorithm is None
        assert req.theme is None
        assert req.notifications_enabled is None
        assert req.auto_snapshot_enabled is None
        assert req.max_snapshots_per_playlist is None
        assert req.dashboard_show_recent_activity is None

    def test_all_valid_algorithms(self):
        """Should accept every registered algorithm name."""
        valid_names = [
            "BasicShuffle",
            "BalancedShuffle",
            "PercentageShuffle",
            "StratifiedShuffle",
            "ArtistSpacingShuffle",
            "AlbumSequenceShuffle",
            "TempoGradientShuffle",
        ]
        for name in valid_names:
            req = UserSettingsUpdateRequest(
                default_algorithm=name
            )
            assert req.default_algorithm == name

    def test_all_valid_themes(self):
        """Should accept all valid theme choices."""
        for theme in ["light", "dark", "system"]:
            req = UserSettingsUpdateRequest(theme=theme)
            assert req.theme == theme

    def test_theme_case_insensitive(self):
        """Should normalize theme to lowercase."""
        req = UserSettingsUpdateRequest(theme="DARK")
        assert req.theme == "dark"

        req = UserSettingsUpdateRequest(theme="Light")
        assert req.theme == "light"

    def test_theme_stripped(self):
        """Should strip whitespace from theme."""
        req = UserSettingsUpdateRequest(theme="  dark  ")
        assert req.theme == "dark"

    def test_algorithm_none_is_valid(self):
        """Should accept None as algorithm (no default)."""
        req = UserSettingsUpdateRequest(
            default_algorithm=None
        )
        assert req.default_algorithm is None

    def test_algorithm_empty_string_becomes_none(self):
        """Should normalize empty string algorithm to None."""
        req = UserSettingsUpdateRequest(
            default_algorithm=""
        )
        assert req.default_algorithm is None

    def test_algorithm_whitespace_only_becomes_none(self):
        """Should normalize whitespace-only algorithm to None."""
        req = UserSettingsUpdateRequest(
            default_algorithm="   "
        )
        assert req.default_algorithm is None

    def test_max_snapshots_boundary_min(self):
        """Should accept minimum value of 1."""
        req = UserSettingsUpdateRequest(
            max_snapshots_per_playlist=1
        )
        assert req.max_snapshots_per_playlist == 1

    def test_max_snapshots_boundary_max(self):
        """Should accept maximum value of 50."""
        req = UserSettingsUpdateRequest(
            max_snapshots_per_playlist=50
        )
        assert req.max_snapshots_per_playlist == 50

    def test_extra_fields_ignored(self):
        """Should ignore unknown fields."""
        req = UserSettingsUpdateRequest(
            theme="dark",
            unknown_field="should be ignored",
        )
        assert req.theme == "dark"
        assert not hasattr(req, "unknown_field")


# =============================================================================
# UserSettingsUpdateRequest — Invalid inputs
# =============================================================================


class TestUserSettingsUpdateRequestInvalid:
    """Tests for invalid UserSettingsUpdateRequest payloads."""

    def test_invalid_algorithm_name(self):
        """Should reject unknown algorithm names."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(
                default_algorithm="NonexistentShuffle"
            )
        assert "Invalid algorithm" in str(exc_info.value)
        assert "NonexistentShuffle" in str(exc_info.value)

    def test_invalid_theme(self):
        """Should reject invalid theme choices."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(theme="blue")
        assert "Invalid theme" in str(exc_info.value)
        assert "blue" in str(exc_info.value)

    def test_max_snapshots_below_min(self):
        """Should reject max_snapshots below 1."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(
                max_snapshots_per_playlist=0
            )
        assert (
            "greater than or equal to 1"
            in str(exc_info.value).lower()
        )

    def test_max_snapshots_above_max(self):
        """Should reject max_snapshots above 50."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(
                max_snapshots_per_playlist=51
            )
        assert (
            "less than or equal to 50"
            in str(exc_info.value).lower()
        )

    def test_max_snapshots_negative(self):
        """Should reject negative max_snapshots."""
        with pytest.raises(ValidationError):
            UserSettingsUpdateRequest(
                max_snapshots_per_playlist=-5
            )

    def test_theme_empty_string(self):
        """Should reject empty string theme after strip."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(theme="")
        assert "Invalid theme" in str(exc_info.value)
```

**Key rationale for test choices**:
- The `validate_algorithm` validator at line 39 converts `""` to `None` and at lines 41-43 strips whitespace then converts empty result to `None`. Both paths need coverage.
- The `validate_theme` validator at line 61 does NOT convert `None` early, but at line 62 it strips + lowercases. An empty string after strip becomes `""`, which is not in `{"light", "dark", "system"}`, so it should raise.
- The `max_snapshots_per_playlist` field uses Pydantic's `ge=1, le=50` (line 27), so boundary tests at 0, 1, 50, and 51 are needed.

---

### Step A2: Create `tests/schemas/test_snapshot_requests.py`

**File to create**: `/Users/chris/Projects/shuffify/tests/schemas/test_snapshot_requests.py`

**Schema under test**: `/Users/chris/Projects/shuffify/shuffify/schemas/snapshot_requests.py` (lines 1-49)

The `ManualSnapshotRequest` model has:
- `playlist_name`: required string, `min_length=1, max_length=255` (lines 12-17)
- `track_uris`: required list of strings, `min_length=0` (lines 18-24) -- NOTE: min_length=0 means empty list is allowed by the Field constraint, but the `validate_track_uris` validator (lines 34-45) only checks URI format, so an empty list passes validation
- `trigger_description`: optional string, `max_length=500` (lines 25-32)
- `validate_track_uris` validator (lines 34-45): checks each URI starts with `"spotify:track:"`
- `Config.extra = "ignore"` (lines 47-48)

**Exact content to write**:

```python
"""
Tests for playlist snapshot request validation schemas.

Tests ManualSnapshotRequest Pydantic model for snapshot
creation payload validation.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.snapshot_requests import (
    ManualSnapshotRequest,
)


# =============================================================================
# Helpers
# =============================================================================


def _base_snapshot_kwargs(**overrides):
    """Return minimal valid kwargs for ManualSnapshotRequest."""
    defaults = {
        "playlist_name": "My Playlist",
        "track_uris": [
            "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
            "spotify:track:1301WleyT98MSxVHPZCA6M",
        ],
    }
    defaults.update(overrides)
    return defaults


# =============================================================================
# ManualSnapshotRequest — Valid inputs
# =============================================================================


class TestManualSnapshotRequestValid:
    """Tests for valid ManualSnapshotRequest payloads."""

    def test_valid_minimal_request(self):
        """Should accept request with required fields only."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs()
        )
        assert req.playlist_name == "My Playlist"
        assert len(req.track_uris) == 2
        assert req.trigger_description is None

    def test_valid_with_trigger_description(self):
        """Should accept request with optional description."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(
                trigger_description="Before shuffle"
            )
        )
        assert (
            req.trigger_description == "Before shuffle"
        )

    def test_single_track_uri(self):
        """Should accept a single track URI."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(
                track_uris=[
                    "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
                ],
            )
        )
        assert len(req.track_uris) == 1

    def test_empty_track_list(self):
        """Should accept an empty track list."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(track_uris=[])
        )
        assert len(req.track_uris) == 0

    def test_many_track_uris(self):
        """Should accept a large number of track URIs."""
        uris = [
            f"spotify:track:track{i:022d}"
            for i in range(500)
        ]
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(track_uris=uris)
        )
        assert len(req.track_uris) == 500

    def test_trigger_description_max_length(self):
        """Should accept description at max length."""
        desc = "x" * 500
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(
                trigger_description=desc
            )
        )
        assert len(req.trigger_description) == 500

    def test_extra_fields_ignored(self):
        """Should ignore unknown fields."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(
                unknown_field="ignored"
            )
        )
        assert req.playlist_name == "My Playlist"
        assert not hasattr(req, "unknown_field")


# =============================================================================
# ManualSnapshotRequest — Invalid inputs
# =============================================================================


class TestManualSnapshotRequestInvalid:
    """Tests for invalid ManualSnapshotRequest payloads."""

    def test_missing_playlist_name(self):
        """Should reject request without playlist_name."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                track_uris=[
                    "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
                ],
            )

    def test_empty_playlist_name(self):
        """Should reject empty string playlist_name."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(playlist_name="")
            )

    def test_playlist_name_too_long(self):
        """Should reject playlist_name exceeding 255 chars."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    playlist_name="x" * 256
                )
            )

    def test_missing_track_uris(self):
        """Should reject request without track_uris."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                playlist_name="My Playlist",
            )

    def test_invalid_track_uri_format(self):
        """Should reject non-Spotify track URIs."""
        with pytest.raises(ValidationError) as exc_info:
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    track_uris=["not_a_valid_uri"]
                )
            )
        assert "Invalid track URI format" in str(
            exc_info.value
        )

    def test_invalid_uri_spotify_album(self):
        """Should reject Spotify album URIs."""
        with pytest.raises(ValidationError) as exc_info:
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    track_uris=[
                        "spotify:album:4iV5W9uYEdYUVa79Axb7Rh"
                    ]
                )
            )
        assert "Invalid track URI format" in str(
            exc_info.value
        )

    def test_invalid_uri_spotify_playlist(self):
        """Should reject Spotify playlist URIs."""
        with pytest.raises(ValidationError) as exc_info:
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    track_uris=[
                        "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
                    ]
                )
            )
        assert "Invalid track URI format" in str(
            exc_info.value
        )

    def test_mixed_valid_and_invalid_uris(self):
        """Should reject if any URI is invalid."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    track_uris=[
                        "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
                        "bad_uri",
                    ]
                )
            )

    def test_trigger_description_too_long(self):
        """Should reject description exceeding 500 chars."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    trigger_description="x" * 501
                )
            )
```

**Key rationale for test choices**:
- The `track_uris` field has `min_length=0` (line 20), so empty list IS valid -- tested in `test_empty_track_list`.
- The `validate_track_uris` validator (line 41) checks `uri.startswith("spotify:track:")` -- tests must cover album URIs, playlist URIs, and garbage strings.
- The `_base_snapshot_kwargs` helper follows the same pattern as `_base_create_kwargs` in `test_schedule_requests.py`.

---

## Part B: Dead Code Removal

### Context

The method `count_sources_for_target()` at `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py` lines 210-223 was added during Workshop Powertools Phase 4 (smart raid panel, see archive doc at line 81) but was never actually called by any route, service, or test.

**Grep verification** (already confirmed): The string `count_sources_for_target` appears ONLY at:
1. The method definition itself (line 211)
2. Documentation/planning files (not code)

It does NOT appear in any route, service, template, or test file.

### What to Remove

**File**: `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py`

**Lines to remove**: 210-223 (the blank line before the method through the end of the method body)

**Before** (lines 208-246):
```python
            raise UpstreamSourceError(
                f"Failed to delete source: {e}"
            )

    @staticmethod
    def count_sources_for_target(
        spotify_id: str, target_playlist_id: str
    ) -> int:
        """Count upstream sources for a target playlist."""
        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()
        if not user:
            return 0
        return UpstreamSource.query.filter_by(
            user_id=user.id,
            target_playlist_id=target_playlist_id,
        ).count()

    @staticmethod
    def list_all_sources_for_user(
        spotify_id: str,
    ) -> List[UpstreamSource]:
```

**After** (lines 208-229):
```python
            raise UpstreamSourceError(
                f"Failed to delete source: {e}"
            )

    @staticmethod
    def list_all_sources_for_user(
        spotify_id: str,
    ) -> List[UpstreamSource]:
```

**What specifically gets deleted**: Lines 210-223 (the `@staticmethod` decorator, the `def count_sources_for_target(...)` signature, its docstring, and its method body). That is exactly 14 lines of code.

### Step-by-step

1. Open `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py`
2. Delete lines 210 through 223 (the blank line after `delete_source` method's closing, through the `.count()` return statement)
3. Verify the file still has the `list_all_sources_for_user` method immediately following `delete_source`
4. Run `flake8 shuffify/services/upstream_source_service.py` to confirm no lint errors
5. Run `pytest tests/services/test_upstream_source_service.py -v` to confirm all existing tests still pass (there are no tests for the removed method)

---

## Part C: Dependency Updates

### Current State of Requirements Files

**`/Users/chris/Projects/shuffify/requirements/base.txt`** uses a mixed pinning strategy:
- Hard pins (`==`): `spotipy==2.25.2`, `python-dotenv==1.2.1`, `gunicorn==23.0.0`, `requests==2.32.5`
- Floor pins (`>=`): `Flask>=3.1.0`, `werkzeug>=3.1.5`, etc.

**`/Users/chris/Projects/shuffify/requirements/dev.txt`** uses hard pins for most dev tools:
- `pytest==8.3.5`, `pytest-cov==6.0.0`, `flake8==6.1.0`, etc.

**`/Users/chris/Projects/shuffify/requirements/prod.txt`** has: `sentry-sdk==1.45.1`, `psycopg2>=2.9.9`

### Safe Updates (by file)

#### `requirements/base.txt` Changes

| Package | Current | Target | Pin Style | Reason |
|---------|---------|--------|-----------|--------|
| `gunicorn` | `==23.0.0` | `==25.0.3` | Hard pin | Major version jump (23 to 25); pin exactly to tested version |
| `certifi` | not pinned (transitive) | `>=2026.1.4` | Add as floor pin | Security certificates; add to transitive security section |
| `MarkupSafe` | not pinned (transitive) | `>=3.0.3` | Add as floor pin | Add to transitive security section |

**Before** (`base.txt`):
```
gunicorn==23.0.0
...
# --- Security: explicit floors for transitive deps with known CVEs ---
# These packages are pulled in by Flask, requests, cryptography, etc.
# pip will NOT upgrade them on its own unless we set a minimum version.
werkzeug>=3.1.5
urllib3>=2.6.3
marshmallow>=3.26.2,<4.0
pyasn1>=0.6.2
Flask-Limiter>=3.5.0
```

**After** (`base.txt`):
```
gunicorn==25.0.3
...
# --- Security: explicit floors for transitive deps with known CVEs ---
# These packages are pulled in by Flask, requests, cryptography, etc.
# pip will NOT upgrade them on its own unless we set a minimum version.
werkzeug>=3.1.5
urllib3>=2.6.3
certifi>=2026.1.4
MarkupSafe>=3.0.3
marshmallow>=3.26.2,<4.0
pyasn1>=0.6.2
Flask-Limiter>=3.5.0
```

**NOTE on Flask**: The task description says "Flask 3.1.2 -> 3.1.3" but `base.txt` already uses `Flask>=3.1.0` (floor pin). Since Flask 3.1.3 exists on PyPI and the floor allows it, a simple `pip install -r requirements/base.txt` will already pull 3.1.3 or later. However, since the installed version is 3.1.2, the floor should be bumped to `>=3.1.3` to guarantee the security fix is always installed.

**Revised Flask line**:
- **Before**: `Flask>=3.1.0`
- **After**: `Flask>=3.1.3`

**NOTE on PyYAML**: The task says "PyYAML 6.0.2 -> 6.0.3". PyYAML is not directly listed in any requirements file -- it is a transitive dependency (pulled in by other packages). The installed version is 6.0.2. To ensure the updated version, add it to the transitive security section.

**Additional line to add** to the transitive section:
```
PyYAML>=6.0.3
```

**NOTE on gunicorn 23 -> 25**: This is a major version jump (23 to 25). Gunicorn 25.0.0 introduces per-app worker allocation for dirty arbiters and HTTP/2 support (beta). The project uses gunicorn simply (`gunicorn run:app` per `CLAUDE.md`), so the upgrade should be safe for the basic usage pattern. However, the implementer MUST verify by running the app locally with gunicorn after the upgrade.

#### `requirements/dev.txt` Changes

| Package | Current | Target | Reason |
|---------|---------|--------|--------|
| `pytest-cov` | `==6.0.0` | `==7.0.0` | Major bump, but pytest-cov 7.0.0 only requires coverage>=7.10.6 which we have |
| `coverage` | not listed (transitive via pytest-cov) | No change needed | Already at 7.13.2 (installed), and pytest-cov 7.0.0 requires >=7.10.6 |

**Wait -- `pytest-cov` 6 -> 7 is a major version bump.** The task says "Do NOT update these major versions" for certain packages but does not list pytest-cov. The task says `coverage -> latest` and `pytest-cov -> latest`. Since pytest-cov 7.0.0 is the latest and the task explicitly calls it out as a dev-only update, proceed with it.

**Before** (`dev.txt`):
```
pytest-cov==6.0.0
```

**After** (`dev.txt`):
```
pytest-cov==7.0.0
```

**NOTE**: Do NOT add a `coverage` line to `dev.txt`. Coverage is a transitive dependency of `pytest-cov` and is already at 7.13.2 (which satisfies pytest-cov 7.0.0's requirement of >=7.10.6). Adding it explicitly would create a redundant pin.

#### `requirements/prod.txt` Changes

No changes needed. The task does not mention any prod-only updates.

### Summary of All Requirements Changes

**`requirements/base.txt`** -- 5 changes total:
1. Line 1: `Flask>=3.1.0` becomes `Flask>=3.1.3`
2. Line 5: `gunicorn==23.0.0` becomes `gunicorn==25.0.3`
3. Add `certifi>=2026.1.4` to transitive section (after `urllib3` line)
4. Add `MarkupSafe>=3.0.3` to transitive section (after `certifi` line)
5. Add `PyYAML>=6.0.3` to transitive section (after `MarkupSafe` line)

**`requirements/dev.txt`** -- 1 change:
1. Line 6: `pytest-cov==6.0.0` becomes `pytest-cov==7.0.0`

---

## Implementation Sequence

Execute these steps in order:

### Step 1: Create test files (Part A)

1. Create `/Users/chris/Projects/shuffify/tests/schemas/test_settings_requests.py` with the exact content shown in Step A1
2. Create `/Users/chris/Projects/shuffify/tests/schemas/test_snapshot_requests.py` with the exact content shown in Step A2
3. Run: `pytest tests/schemas/test_settings_requests.py tests/schemas/test_snapshot_requests.py -v`
4. Verify all new tests pass (expected: ~30 tests)

### Step 2: Remove dead code (Part B)

1. Open `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py`
2. Delete lines 210-223 (the `count_sources_for_target` method)
3. Run: `flake8 shuffify/services/upstream_source_service.py`
4. Run: `pytest tests/services/test_upstream_source_service.py -v`
5. Verify zero failures

### Step 3: Update dependencies (Part C)

1. Edit `/Users/chris/Projects/shuffify/requirements/base.txt`:
   - Change `Flask>=3.1.0` to `Flask>=3.1.3`
   - Change `gunicorn==23.0.0` to `gunicorn==25.0.3`
   - Add `certifi>=2026.1.4`, `MarkupSafe>=3.0.3`, `PyYAML>=6.0.3` to the transitive security section
2. Edit `/Users/chris/Projects/shuffify/requirements/dev.txt`:
   - Change `pytest-cov==6.0.0` to `pytest-cov==7.0.0`
3. Run: `source venv/bin/activate && pip install -r requirements/dev.txt`
4. Verify installed versions: `pip show Flask gunicorn certifi MarkupSafe PyYAML pytest-cov`
5. Run quick smoke test: `python -c "from shuffify import create_app; print('OK')"`

### Step 4: Full verification

1. Run: `flake8 shuffify/`
2. Run: `pytest tests/ -v`
3. Verify ALL tests pass (should be 1081 + ~30 new = ~1111)
4. Run: `black --check shuffify/` (should pass -- no source code formatting changes)

---

## Verification Checklist

Before marking complete, confirm each of the following:

- [ ] `tests/schemas/test_settings_requests.py` exists and all tests pass
- [ ] `tests/schemas/test_snapshot_requests.py` exists and all tests pass
- [ ] `count_sources_for_target` method is removed from `upstream_source_service.py`
- [ ] `list_all_sources_for_user` method still exists and is intact (immediately follows `delete_source`)
- [ ] `upstream_source_service.py` has no lint errors (`flake8`)
- [ ] All upstream source service tests still pass
- [ ] `requirements/base.txt` has `Flask>=3.1.3`
- [ ] `requirements/base.txt` has `gunicorn==25.0.3`
- [ ] `requirements/base.txt` has `certifi>=2026.1.4`, `MarkupSafe>=3.0.3`, `PyYAML>=6.0.3` in transitive section
- [ ] `requirements/dev.txt` has `pytest-cov==7.0.0`
- [ ] `requirements/prod.txt` is UNCHANGED
- [ ] `pip install -r requirements/dev.txt` succeeds without errors
- [ ] `flake8 shuffify/` returns 0 errors
- [ ] `pytest tests/ -v` passes all tests
- [ ] CHANGELOG.md is updated with entries under `[Unreleased]`

---

## What NOT To Do

1. **Do NOT update `flake8` to version 7.x** -- the task explicitly forbids this major version bump. Keep `flake8==6.1.0`.

2. **Do NOT update `pytest` to version 9.x** -- keep at `pytest==8.3.5`.

3. **Do NOT update `marshmallow` to version 4.x** -- keep at `>=3.26.2,<4.0`.

4. **Do NOT update `isort` to version 7.x** -- keep at `isort==5.13.2`.

5. **Do NOT add `coverage` as a direct dependency in `dev.txt`** -- it is a transitive dep of `pytest-cov` and is already at a sufficient version.

6. **Do NOT change the `requirements/prod.txt` file** -- no production-only dependency updates are included in this phase.

7. **Do NOT remove `list_all_sources_for_user`** -- that method IS used (called in `tests/services/test_upstream_source_service.py` line 166 and potentially in routes). Only remove `count_sources_for_target`.

8. **Do NOT add Flask app context fixtures to the new schema test files** -- Pydantic schema tests do not require Flask app context. The `ShuffleRegistry` import chain is pure Python and does not need an app context.

9. **Do NOT change the pinning style convention** -- use `==` for packages currently hard-pinned, and `>=` for packages currently floor-pinned or newly added to the transitive security section.

10. **Do NOT modify `tests/schemas/__init__.py`** -- it is a bare docstring file and does not need imports or registration for new test modules.

11. **Do NOT use `pytest.raises(ValidationError, match=...)` for the settings tests** unless you are confident in the exact error message string. The existing `test_requests.py` pattern uses `assert "..." in str(exc_info.value)` which is more resilient to Pydantic version differences.

12. **Do NOT pin `Flask` with `==`** -- it is currently floor-pinned with `>=` and should remain that way (matches the existing project convention for framework packages).

---

### Critical Files for Implementation

- `/Users/chris/Projects/shuffify/shuffify/schemas/settings_requests.py` - Schema to test; contains `UserSettingsUpdateRequest` with 2 validators and 6 fields
- `/Users/chris/Projects/shuffify/shuffify/schemas/snapshot_requests.py` - Schema to test; contains `ManualSnapshotRequest` with track URI validator
- `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py` - Dead code removal target; delete `count_sources_for_target` at lines 210-223
- `/Users/chris/Projects/shuffify/requirements/base.txt` - Dependency floor bumps for Flask, gunicorn, certifi, MarkupSafe, PyYAML
- `/Users/chris/Projects/shuffify/tests/schemas/test_schedule_requests.py` - Reference pattern for new test files (helper functions, class structure, assertion style)
