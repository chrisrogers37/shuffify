---
description: "Guide for developing a new shuffle algorithm"
---

Follow this workflow to create a new shuffle algorithm:

## Step 1: Design Phase

Ask the user:
1. **Algorithm Name**: What should it be called?
2. **Description**: What does it do? (1-2 sentences)
3. **Use Case**: When would users want this?
4. **Parameters**: What options should be configurable?
   - Parameter names, types, defaults, descriptions

## Step 2: Create Algorithm File

Create `shuffify/shuffle_algorithms/{algorithm_name}.py`:

```python
from .registry import ShuffleAlgorithm, register_algorithm

@register_algorithm(
    algorithm_id='{algorithm_id}',
    name='{Algorithm Name}',
    description='{Brief description}',
    parameters={
        'param_name': {
            'type': 'int',  # or 'float', 'bool', 'str'
            'default': 10,
            'description': 'Parameter description'
        }
    }
)
class {AlgorithmName}Algorithm(ShuffleAlgorithm):
    """
    Detailed algorithm description.

    Parameters:
        param_name: What this parameter does

    Example:
        >>> algorithm = {AlgorithmName}Algorithm()
        >>> shuffled = algorithm.shuffle(tracks, param_name=15)
    """

    def shuffle(self, tracks, **params):
        """
        Shuffle the tracks according to the algorithm.

        Args:
            tracks: List of track dictionaries from Spotify API
            **params: Algorithm parameters

        Returns:
            List of reordered tracks
        """
        # Extract parameters with defaults
        param_name = params.get('param_name', 10)

        # Your algorithm logic here
        shuffled_tracks = # ... your implementation

        return shuffled_tracks
```

## Step 3: Register Algorithm

Add import to `shuffify/shuffle_algorithms/__init__.py`:
```python
from .{algorithm_name} import {AlgorithmName}Algorithm
```

## Step 4: Create Tests

Create `tests/shuffle_algorithms/test_{algorithm_name}.py`:

```python
import pytest
from shuffify.shuffle_algorithms.{algorithm_name} import {AlgorithmName}Algorithm


class Test{AlgorithmName}Algorithm:
    """Test suite for {AlgorithmName}Algorithm."""

    @pytest.fixture
    def sample_tracks(self):
        """Create sample tracks for testing."""
        return [
            {'id': str(i), 'name': f'Track {i}'}
            for i in range(20)
        ]

    def test_shuffle_basic(self, sample_tracks):
        """Test basic shuffle functionality."""
        algorithm = {AlgorithmName}Algorithm()
        result = algorithm.shuffle(sample_tracks)

        # Basic validation
        assert len(result) == len(sample_tracks)
        assert set(t['id'] for t in result) == set(t['id'] for t in sample_tracks)

    def test_shuffle_with_params(self, sample_tracks):
        """Test shuffle with parameters."""
        algorithm = {AlgorithmName}Algorithm()
        result = algorithm.shuffle(sample_tracks, param_name=15)

        # Add assertions specific to your algorithm's behavior
        pass

    def test_empty_playlist(self):
        """Test handling of empty playlist."""
        algorithm = {AlgorithmName}Algorithm()
        result = algorithm.shuffle([])
        assert result == []

    def test_single_track(self):
        """Test handling of single track."""
        algorithm = {AlgorithmName}Algorithm()
        tracks = [{'id': '1', 'name': 'Single Track'}]
        result = algorithm.shuffle(tracks)
        assert result == tracks
```

## Step 5: Update Documentation

Update `shuffify/shuffle_algorithms/README.md`:

Add a new section:
```markdown
### {Algorithm Name}

**Description**: {Detailed description}

**Use Case**: {When to use this algorithm}

**Parameters**:
- `param_name` (type, default: value): Description

**Example**:
```python
algorithm = {AlgorithmName}Algorithm()
shuffled = algorithm.shuffle(tracks, param_name=15)
```

**How It Works**:
1. Step 1 explanation
2. Step 2 explanation
3. ...
```

## Step 6: Update CHANGELOG

Add to `CHANGELOG.md` under `## [Unreleased]`:

```markdown
### Added
- **{Algorithm Name} Shuffle** - {Brief description}
  - {Implementation detail}
  - Accessible via `/shuffle?algorithm={algorithm_id}`
  - Added {AlgorithmName}Algorithm class in `shuffify/shuffle_algorithms/{algorithm_name}.py`
```

## Step 7: Verify

1. Run tests: `pytest tests/shuffle_algorithms/test_{algorithm_name}.py -v`
2. Test in browser:
   - Start app: `python run.py`
   - Connect with Spotify
   - Select a playlist
   - Choose your new algorithm
   - Verify it works as expected
3. Check that algorithm appears in UI dropdown

## Complete!

Report to user:
- ✅ Algorithm created: `shuffify/shuffle_algorithms/{algorithm_name}.py`
- ✅ Tests created: `tests/shuffle_algorithms/test_{algorithm_name}.py`
- ✅ Documentation updated
- ✅ CHANGELOG updated
- Next steps: Manual testing, then create PR with `/commit-push-pr`
