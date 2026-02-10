# Phase 4: Extract Shuffle Algorithm Shared Utilities

| Attribute | Value |
|-----------|-------|
| **PR Title** | `refactor: Extract shared utilities from shuffle algorithms to reduce duplication` |
| **Risk Level** | Low |
| **Estimated Effort** | 45 minutes |
| **Files Modified** | 6 (1 new, 5 modified) |
| **Dependencies** | None |
| **Blocks** | Nothing |

---

## Overview

Five shuffle algorithms contain duplicated patterns:
1. **URI extraction** — identical one-liner in 5 files
2. **keep_first split** — identical 10-line block in 4 files
3. **Section calculation** — identical logic in 2 files

This PR extracts these into a shared `utils.py` module and updates each algorithm to use the shared functions.

**Why:** Bug fixes or behavior changes to these patterns (e.g., handling edge cases in URI extraction) currently need to be replicated across 4-5 files. A single utility module ensures consistency.

---

## Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/shuffle_algorithms/utils.py` | **NEW** — shared utility functions |
| `shuffify/shuffle_algorithms/basic.py` | Use `extract_uris`, `split_keep_first` |
| `shuffify/shuffle_algorithms/balanced.py` | Use `extract_uris`, `split_keep_first`, `split_into_sections` |
| `shuffify/shuffle_algorithms/percentage.py` | Use `extract_uris` |
| `shuffify/shuffle_algorithms/stratified.py` | Use `extract_uris`, `split_keep_first`, `split_into_sections` |
| `shuffify/shuffle_algorithms/artist_spacing.py` | Use `extract_uris` |

---

## Step-by-Step Implementation

### Step 1: Create utils.py

**File:** `shuffify/shuffle_algorithms/utils.py` (NEW FILE)

```python
"""
Shared utility functions for shuffle algorithms.

Provides common operations used across multiple algorithms to avoid
code duplication and ensure consistent behavior.
"""

from typing import List, Dict, Any, Tuple


def extract_uris(tracks: List[Dict[str, Any]]) -> List[str]:
    """
    Extract track URIs from a list of track dictionaries.

    Args:
        tracks: List of track dictionaries, each with a 'uri' key.

    Returns:
        List of URI strings. Tracks without a 'uri' key are skipped.
    """
    return [track["uri"] for track in tracks if track.get("uri")]


def split_keep_first(
    uris: List[str], keep_first: int
) -> Tuple[List[str], List[str]]:
    """
    Split a URI list into kept (pinned) and shuffleable portions.

    Args:
        uris: Full list of track URIs.
        keep_first: Number of tracks to keep at the start.
            If 0, no tracks are kept. If >= len(uris), all are kept.

    Returns:
        Tuple of (kept_uris, shuffleable_uris).
    """
    if keep_first <= 0:
        return [], uris
    return uris[:keep_first], uris[keep_first:]


def split_into_sections(
    items: List[str], section_count: int
) -> List[List[str]]:
    """
    Divide a list into N sections of roughly equal size.

    Distributes remainder items across the first sections so that
    no section is more than 1 item larger than any other.

    Args:
        items: The list to divide.
        section_count: Number of sections. Clamped to len(items) if larger.

    Returns:
        List of sections, each a list of items.
    """
    if not items:
        return []

    # Don't create more sections than items
    section_count = min(section_count, len(items))

    total = len(items)
    base_size = total // section_count
    remainder = total % section_count

    sections = []
    start = 0
    for i in range(section_count):
        size = base_size + (1 if i < remainder else 0)
        sections.append(items[start : start + size])
        start += size

    return sections
```

### Step 2: Update basic.py

**File:** `shuffify/shuffle_algorithms/basic.py`

Read the file first to find the exact line numbers. Then make these changes:

#### 2a. Add import

Add after the existing imports (after the `from . import ShuffleAlgorithm` line):
```python
from .utils import extract_uris, split_keep_first
```

#### 2b. Replace URI extraction and keep_first logic

Find the `shuffle()` method. Inside it, locate the pattern:
```python
        uris = [track["uri"] for track in tracks if track.get("uri")]
```

Replace with:
```python
        uris = extract_uris(tracks)
```

Then locate the keep_first splitting logic (the block that creates `kept_tracks` and `to_shuffle`):
```python
        kept_tracks = uris[:keep_first] if keep_first > 0 else []
        to_shuffle = uris[keep_first:]
```

Replace with:
```python
        kept_tracks, to_shuffle = split_keep_first(uris, keep_first)
```

**Important:** Keep all other logic (the early returns, the `random.shuffle(to_shuffle)`, the `return kept_tracks + to_shuffle`) exactly as-is. Only replace the two patterns above.

### Step 3: Update balanced.py

**File:** `shuffify/shuffle_algorithms/balanced.py`

#### 3a. Add import

Add after existing imports:
```python
from .utils import extract_uris, split_keep_first, split_into_sections
```

#### 3b. Replace URI extraction

Find:
```python
        uris = [track["uri"] for track in tracks if track.get("uri")]
```
Replace with:
```python
        uris = extract_uris(tracks)
```

#### 3c. Replace keep_first logic

Find the kept_tracks/to_shuffle split:
```python
        kept_tracks = uris[:keep_first] if keep_first > 0 else []
        to_shuffle = uris[keep_first:]
```
Replace with:
```python
        kept_tracks, to_shuffle = split_keep_first(uris, keep_first)
```

#### 3d. Replace section calculation

Find the section calculation block (the `base_section_size`, `remainder`, and for-loop that builds sections):
```python
        total_tracks = len(to_shuffle)
        base_section_size = total_tracks // section_count
        remainder = total_tracks % section_count

        sections = []
        start = 0

        for i in range(section_count):
            current_section_size = base_section_size + (1 if i < remainder else 0)
            end = start + current_section_size
            section = to_shuffle[start:end]
            sections.append(section)
            start = end
```

Replace with:
```python
        sections = split_into_sections(to_shuffle, section_count)
```

**Important:** Keep the round-robin reassembly logic that follows (the part that picks one track from each section in rotation). That is specific to `BalancedShuffle` and must NOT be replaced.

### Step 4: Update stratified.py

**File:** `shuffify/shuffle_algorithms/stratified.py`

Apply the **exact same changes** as balanced.py (Steps 3a-3d). The section calculation logic is identical.

**Important:** Keep the internal section shuffling (`random.shuffle(section)`) and sequential reassembly (extending result with each section in order). That is specific to `StratifiedShuffle`.

### Step 5: Update percentage.py

**File:** `shuffify/shuffle_algorithms/percentage.py`

#### 5a. Add import

Add after existing imports:
```python
from .utils import extract_uris
```

#### 5b. Replace URI extraction

Find:
```python
        uris = [track["uri"] for track in tracks if track.get("uri")]
```
Replace with:
```python
        uris = extract_uris(tracks)
```

**Note:** `PercentageShuffle` does NOT use `keep_first` or section calculation. Only replace the URI extraction.

### Step 6: Update artist_spacing.py

**File:** `shuffify/shuffle_algorithms/artist_spacing.py`

#### 6a. Add import

Add after existing imports:
```python
from .utils import extract_uris
```

#### 6b. Replace URI extraction

Find:
```python
        uris = [t["uri"] for t in tracks if t.get("uri")]
```

Note: This file uses `t` instead of `track` as the variable name. Replace with:
```python
        uris = extract_uris(tracks)
```

**Note:** `ArtistSpacingShuffle` does NOT use `keep_first` or section calculation. Only replace the URI extraction.

---

## Verification Checklist

- [ ] `shuffify/shuffle_algorithms/utils.py` exists with all three functions
- [ ] All 479 tests pass: `pytest tests/ -v`
- [ ] Specifically run algorithm tests: `pytest tests/algorithms/ -v`
- [ ] Lint passes: `flake8 shuffify/`
- [ ] `grep -rn 'track\["uri"\] for track in tracks' shuffify/shuffle_algorithms/` returns no results (all replaced)
- [ ] Verify no algorithm behavior changed by running tests with `-v` and checking output counts

---

## What NOT To Do

- **Do NOT change `album_sequence.py` or `tempo_gradient.py`** — these algorithms have different URI extraction patterns (they work with features) or no duplication to extract. Leave them unchanged.
- **Do NOT change the algorithm-specific logic** (round-robin in balanced, section-internal shuffle in stratified, percentage calculation, artist spacing heap). Only extract the shared patterns.
- **Do NOT add utils.py to the `__init__.py`** of the shuffle_algorithms package. The utils are internal to the package and don't need to be exported.
- **Do NOT change the `shuffle()` method signatures.** They must remain compatible with the existing calling convention.
- **Do NOT remove the `import random`** from any algorithm file — each file still uses `random.shuffle()` in its algorithm-specific logic.
- **Do NOT change the early return conditions** (e.g., `if len(uris) <= 1: return uris`). Keep those exactly as they are in each algorithm.
