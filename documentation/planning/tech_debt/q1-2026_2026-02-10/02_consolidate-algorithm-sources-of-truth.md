# Phase 2: Consolidate Algorithm Lists Into Single Source of Truth

| Attribute | Value |
|-----------|-------|
| **PR Title** | `refactor: Use registry as single source of truth for algorithm validation` |
| **Risk Level** | Low |
| **Estimated Effort** | 30 minutes |
| **Files Modified** | 2 |
| **Dependencies** | None |
| **Blocks** | Nothing |

---

## Overview

Currently, the list of valid algorithms is defined in **three separate places**:
1. `shuffify/shuffle_algorithms/registry.py:15-23` — `_algorithms` dict (the authoritative source)
2. `shuffify/schemas/requests.py:101-109` — hardcoded `valid_algorithms` set in Pydantic validator
3. `shuffify/services/shuffle_service.py:45-53` — hardcoded `VALID_ALGORITHMS` set

This PR eliminates #2 and #3 by having them query the registry instead. It also replaces the 8-branch `elif` chain in `get_algorithm_params()` with a dict-based mapping.

**Why:** Adding a new algorithm currently requires updating 3 files. After this change, only the registry needs updating.

---

## Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/services/shuffle_service.py` | Remove `VALID_ALGORITHMS` set; query registry instead |
| `shuffify/schemas/requests.py` | Replace hardcoded set with registry query; refactor `get_algorithm_params()` |

---

## Step-by-Step Implementation

### Step 1: Update shuffle_service.py

**File:** `shuffify/services/shuffle_service.py`

#### 1a. Add registry import

**Current imports (lines 1-9):**
```python
"""
Shuffle service for managing playlist shuffle operations.

Handles algorithm selection, parameter validation, and shuffle execution.
Uses Pydantic schemas for type-safe parameter validation.
"""

import logging
from typing import Dict, List, Any, Optional
```

**Add after the existing imports (after line 9), before the `from shuffify...` import on line 11:**
```python
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
```

Note: `ShuffleRegistry` is already imported on line 11 (`from shuffify.shuffle_algorithms.registry import ShuffleRegistry`). If it is already imported, skip this sub-step. Read the file to verify.

#### 1b. Remove the VALID_ALGORITHMS class variable

**Current code (lines 44-53):**
```python
class ShuffleService:
    """Service for managing shuffle algorithm execution."""

    # Valid algorithm names for quick validation
    VALID_ALGORITHMS = {
        "BasicShuffle",
        "BalancedShuffle",
        "StratifiedShuffle",
        "PercentageShuffle",
        "ArtistSpacingShuffle",
        "AlbumSequenceShuffle",
        "TempoGradientShuffle",
    }
```

**Replace with:**
```python
class ShuffleService:
    """Service for managing shuffle algorithm execution."""
```

Delete the entire `VALID_ALGORITHMS` block (the comment and the set definition).

#### 1c. Update get_algorithm() to use registry directly

**Current code (lines 65-91):**
```python
    @staticmethod
    def get_algorithm(name: str) -> Any:
        """
        Get an algorithm instance by name.

        Args:
            name: The algorithm name (e.g., 'BasicShuffle').

        Returns:
            An instantiated algorithm object.

        Raises:
            InvalidAlgorithmError: If the algorithm doesn't exist.
        """
        if name not in ShuffleService.VALID_ALGORITHMS:
            logger.error(f"Invalid algorithm requested: {name}")
            raise InvalidAlgorithmError(
                f"Invalid algorithm '{name}'. "
                f"Valid options: {', '.join(sorted(ShuffleService.VALID_ALGORITHMS))}"
            )

        try:
            algorithm_class = ShuffleRegistry.get_algorithm(name)
            return algorithm_class()
        except ValueError:
            logger.error(f"Failed to get algorithm: {name}")
            raise InvalidAlgorithmError(f"Unknown algorithm: {name}")
```

**Replace with:**
```python
    @staticmethod
    def get_algorithm(name: str) -> Any:
        """
        Get an algorithm instance by name.

        Args:
            name: The algorithm name (e.g., 'BasicShuffle').

        Returns:
            An instantiated algorithm object.

        Raises:
            InvalidAlgorithmError: If the algorithm doesn't exist.
        """
        try:
            algorithm_class = ShuffleRegistry.get_algorithm(name)
            return algorithm_class()
        except ValueError:
            valid_names = sorted(ShuffleRegistry.get_available_algorithms().keys())
            logger.error(f"Invalid algorithm requested: {name}")
            raise InvalidAlgorithmError(
                f"Invalid algorithm '{name}'. "
                f"Valid options: {', '.join(valid_names)}"
            )
```

**Why this change:** Instead of checking against a hardcoded set and then calling the registry (which also checks), we call the registry directly. If the registry raises `ValueError`, we catch it and produce the same user-friendly error message. The valid names are pulled from the registry dynamically.

### Step 2: Update requests.py

**File:** `shuffify/schemas/requests.py`

#### 2a. Add registry import

**Current imports (lines 1-8):**
```python
"""
Request validation schemas using Pydantic.

Provides type-safe validation for all API request parameters.
"""

from typing import Literal, Annotated, Any, Dict
from pydantic import BaseModel, Field, field_validator
```

**Add after line 8:**
```python
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
```

#### 2b. Update ShuffleRequest.validate_algorithm_name

**Current code (lines 97-118):**
```python
    @field_validator("algorithm")
    @classmethod
    def validate_algorithm_name(cls, v: str) -> str:
        """Ensure algorithm name is valid."""
        valid_algorithms = {
            "BasicShuffle",
            "BalancedShuffle",
            "StratifiedShuffle",
            "PercentageShuffle",
            "ArtistSpacingShuffle",
            "AlbumSequenceShuffle",
            "TempoGradientShuffle",
        }
        if not v or not v.strip():
            raise ValueError("Algorithm name cannot be empty")
        v = v.strip()
        if v not in valid_algorithms:
            raise ValueError(
                f"Invalid algorithm '{v}'. "
                f"Valid options: {', '.join(sorted(valid_algorithms))}"
            )
        return v
```

**Replace with:**
```python
    @field_validator("algorithm")
    @classmethod
    def validate_algorithm_name(cls, v: str) -> str:
        """Ensure algorithm name is valid."""
        if not v or not v.strip():
            raise ValueError("Algorithm name cannot be empty")
        v = v.strip()
        valid_algorithms = set(ShuffleRegistry.get_available_algorithms().keys())
        if v not in valid_algorithms:
            raise ValueError(
                f"Invalid algorithm '{v}'. "
                f"Valid options: {', '.join(sorted(valid_algorithms))}"
            )
        return v
```

#### 2c. Replace get_algorithm_params() elif chain with dict mapping

**Current code (lines 120-145):**
```python
    def get_algorithm_params(self) -> Dict[str, Any]:
        """
        Extract only the parameters relevant to the selected algorithm.

        Returns:
            Dictionary of algorithm-specific parameters.
        """
        if self.algorithm == "BasicShuffle":
            return {"keep_first": self.keep_first}
        elif self.algorithm == "BalancedShuffle":
            return {"keep_first": self.keep_first, "section_count": self.section_count}
        elif self.algorithm == "StratifiedShuffle":
            return {"keep_first": self.keep_first, "section_count": self.section_count}
        elif self.algorithm == "PercentageShuffle":
            return {
                "shuffle_percentage": self.shuffle_percentage,
                "shuffle_location": self.shuffle_location,
            }
        elif self.algorithm == "ArtistSpacingShuffle":
            return {"min_spacing": self.min_spacing}
        elif self.algorithm == "AlbumSequenceShuffle":
            return {"shuffle_within_albums": self.shuffle_within_albums}
        elif self.algorithm == "TempoGradientShuffle":
            return {"direction": self.direction}
        else:
            return {}
```

**Replace with:**
```python
    # Maps algorithm names to the list of parameter field names they use.
    # When adding a new algorithm, add an entry here with its parameter names.
    _ALGORITHM_PARAMS = {
        "BasicShuffle": ["keep_first"],
        "BalancedShuffle": ["keep_first", "section_count"],
        "StratifiedShuffle": ["keep_first", "section_count"],
        "PercentageShuffle": ["shuffle_percentage", "shuffle_location"],
        "ArtistSpacingShuffle": ["min_spacing"],
        "AlbumSequenceShuffle": ["shuffle_within_albums"],
        "TempoGradientShuffle": ["direction"],
    }

    def get_algorithm_params(self) -> Dict[str, Any]:
        """
        Extract only the parameters relevant to the selected algorithm.

        Returns:
            Dictionary of algorithm-specific parameters.
        """
        param_names = self._ALGORITHM_PARAMS.get(self.algorithm, [])
        return {name: getattr(self, name) for name in param_names}
```

**Why:** The dict mapping is data, not logic. Adding a new algorithm means adding one line to the dict instead of a new elif branch. The `getattr` call reads the validated Pydantic field value by name.

**Important note:** This still requires updating `_ALGORITHM_PARAMS` when adding a new algorithm. However, now there are only **two** places to update (registry + this dict) instead of three, and the parameter mapping is declarative rather than procedural.

#### 2d. Also update ShuffleRequestBase (if still present)

**Current code (lines 11-24):**
```python
class ShuffleRequestBase(BaseModel):
    """Base schema for shuffle requests."""

    algorithm: str = Field(
        default="BasicShuffle", description="Name of the shuffle algorithm to use"
    )

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm_name(cls, v: str) -> str:
        """Ensure algorithm name is not empty."""
        if not v or not v.strip():
            raise ValueError("Algorithm name cannot be empty")
        return v.strip()
```

Leave `ShuffleRequestBase` as-is. It only validates that the name is non-empty (no hardcoded algorithm list). It's fine.

---

## Verification Checklist

- [ ] `grep -rn "VALID_ALGORITHMS" shuffify/` returns no results
- [ ] `grep -rn "BasicShuffle.*BalancedShuffle" shuffify/schemas/requests.py` returns no results in the validator (only in `_ALGORITHM_PARAMS` dict)
- [ ] All 479 tests pass: `pytest tests/ -v`
- [ ] Lint passes: `flake8 shuffify/`
- [ ] Manual test: submit a shuffle request with an invalid algorithm name and verify a clear error is returned
- [ ] Manual test: submit a shuffle request with each valid algorithm and verify it works

---

## What NOT To Do

- **Do NOT remove the `_ALGORITHM_PARAMS` dict.** While it's still a second location, it maps *parameters* (not validity). The registry doesn't know which Pydantic fields map to which algorithm — that's the schema's job.
- **Do NOT change the registry itself** in this PR. The registry is the source of truth and should not be modified here.
- **Do NOT import the registry at the top of `shuffle_service.py` if it's already imported.** Read the file first to check.
- **Do NOT modify the `ShuffleRequestBase` class.** It only checks for non-empty names and doesn't have a hardcoded list.
- **Do NOT change `BasicShuffleParams`, `BalancedShuffleParams`, etc.** These per-algorithm Pydantic models (lines 27-63) are not used by the main request flow but may be useful for future per-algorithm validation. Leave them.
