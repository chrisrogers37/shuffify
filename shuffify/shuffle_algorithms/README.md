# Shuffle Algorithms

This directory contains various algorithms for shuffling Spotify playlists, each with its own unique approach and parameters.

**Total:** 7 algorithms registered (6 visible, 1 hidden)

## Available Algorithms

### Basic
- **Class**: `BasicShuffle`
- **File**: `basic.py`
- **Visible**: Yes
- **Requires Audio Features**: No
- **Description**: Standard random shuffle with the option to keep tracks fixed at the start.
- **Parameters**:
  - `keep_first` (integer): Number of tracks to keep in their original position at the start
    - Default: 0, Min: 0
- **Example**:
  With `keep_first=3`:
  1. First 3 tracks remain in their original positions
  2. Remaining tracks are shuffled randomly
  3. Result: [Track1, Track2, Track3, Shuffled_Track7, Shuffled_Track15, ...]
- **Use Cases**:
  - Keeping your favorite songs at the top of a playlist
  - Maintaining a specific opening sequence while randomizing the rest
  - Creating playlists with a consistent start but varied middle/end


### Percentage
- **Class**: `PercentageShuffle`
- **File**: `percentage.py`
- **Visible**: Yes
- **Requires Audio Features**: No
- **Description**: Shuffle a portion of your playlist while keeping the rest in order.
- **Parameters**:
  - `shuffle_percentage` (float): Percentage to shuffle (0-100%)
    - Default: 50.0, Min: 0.0, Max: 100.0
  - `shuffle_location` (string): Choose 'front' to shuffle the beginning or 'back' to shuffle the end
    - Default: 'front', Options: ['front', 'back']
- **Example**:
  With 100 tracks and 30% shuffle:
  - **Front**: First 30 tracks get shuffled, last 70 stay in order
    - Result: [Shuffled_Track15, Shuffled_Track8, Shuffled_Track22, Track31, Track32, ...]
  - **Back**: First 70 tracks stay in order, last 30 get shuffled
    - Result: [Track1, Track2, ..., Track70, Shuffled_Track85, Shuffled_Track92, ...]
- **Use Cases**:
  - Shuffling only the beginning of a playlist to find a good starting point
  - Keeping the end of a playlist in order while randomizing the middle
  - Creating variety in specific sections while maintaining structure elsewhere


### Balanced
- **Class**: `BalancedShuffle`
- **File**: `balanced.py`
- **Visible**: Yes
- **Requires Audio Features**: No
- **Description**: Ensures fair representation from all parts of the playlist by dividing it into sections and using a round-robin selection process.
- **Parameters**:
  - `keep_first` (integer): Number of tracks to keep in their original position
    - Default: 0, Min: 0
  - `section_count` (integer): Number of sections to divide the playlist into
    - Default: 4, Min: 2, Max: 10

- **How it works in detail**:
  1. Divides the playlist (excluding kept tracks) into equal-sized sections
  2. Shuffles each section internally
  3. Builds final sequence by selecting one track at a time from each section in round-robin fashion

  Example with 100 tracks and 4 sections:
  ```
  Original playlist: [Track1...Track100]

  1. Division into sections:
     Section 1: [Track1...Track25]    (Early playlist tracks)
     Section 2: [Track26...Track50]   (Early-middle tracks)
     Section 3: [Track51...Track75]   (Late-middle tracks)
     Section 4: [Track76...Track100]  (End playlist tracks)

  2. Each section is shuffled internally

  3. Final assembly (round-robin):
     [Section1_Track1, Section2_Track1, Section3_Track1, Section4_Track1,
      Section1_Track2, Section2_Track2, Section3_Track2, Section4_Track2, ...]
  ```

  Note: While tracks from each section are distributed evenly throughout the playlist, their relative positions will be significantly different from the original order. For example, a track from Section 4 (originally near the end) will appear at positions 4, 8, 12, etc. in the final shuffle.

- **Benefits**:
  - Prevents clustering of tracks from the same playlist region
  - Ensures fair representation from all parts of the playlist in the new playlist order
  - Systematically intersperses tracks from different playlist regions

- **Use Cases**:
  - Large personal libraries where you want to mix older and newer tracks consistently
  - Multi-album playlists where you want regular rotation between albums
  - Genre-mixed playlists where tracks are grouped by style and you want consistent genre variety
  - Any playlist where you want to avoid clustering of tracks from the same section


### Stratified
- **Class**: `StratifiedShuffle`
- **File**: `stratified.py`
- **Visible**: Yes
- **Requires Audio Features**: No
- **Description**: Divide the playlist into sections, shuffle each section independently, and reassemble the sections in the original order.
- **Parameters**:
  - `keep_first` (integer): Number of tracks to keep in their original position
    - Default: 0, Min: 0
  - `section_count` (integer): Number of sections to divide the playlist into
    - Default: 5, Min: 1, Max: 20
- **Example**:
  With `section_count=3` and 90 tracks:
  1. The playlist is divided into 3 equal sections:
     - Section 1: Tracks 1-30
     - Section 2: Tracks 31-60
     - Section 3: Tracks 61-90
  2. Each section is shuffled internally:
     - Section 1: [Track7, Track15, Track3, Track22, ...]
     - Section 2: [Track45, Track31, Track58, Track37, ...]
     - Section 3: [Track78, Track61, Track89, Track67, ...]
  3. The sections are reassembled in their original order: [Shuffled Section 1, Shuffled Section 2, Shuffled Section 3]
- **Use Cases**:
  - Creating playlists with controlled randomness within sections
  - Maintaining some structure while adding variety
  - Breaking up large playlists into manageable chunks for shuffling
  - Preserving the general flow of a playlist while adding local variety


### Artist Spacing
- **Class**: `ArtistSpacingShuffle`
- **File**: `artist_spacing.py`
- **Visible**: Yes
- **Requires Audio Features**: No
- **Description**: Shuffles tracks while ensuring the same artist never appears back-to-back for a more varied listening experience.
- **Parameters**:
  - `min_spacing` (integer): Minimum number of tracks between the same artist
    - Default: 1, Min: 1, Max: 10
- **How it works in detail**:
  1. Groups all tracks by primary artist name
  2. Shuffles tracks within each artist group
  3. Uses a max-heap (greedy) approach: always picks from the artist with the most remaining tracks that isn't blocked by the spacing constraint
  4. Artists go on a "cooldown" after being placed, and come off cooldown after `min_spacing` tracks have been placed
  5. If all artists are on cooldown (impossible to satisfy spacing), falls back to the artist whose cooldown expires soonest
- **Example**:
  With `min_spacing=2` and a playlist containing 5 songs by Artist A and 3 by Artist B:
  - Result: [A1, B1, A2, B2, A3, B3, A4, A5] (best effort spacing)
  - Artist A never appears with fewer than 2 tracks between consecutive appearances where possible
- **Use Cases**:
  - Playlists with multiple songs from the same artist where you want variety
  - Compilations or "best of" playlists where artist repetition is common
  - Large mixed playlists where you want to avoid hearing the same artist twice in a row


### Album Sequence
- **Class**: `AlbumSequenceShuffle`
- **File**: `album_sequence.py`
- **Visible**: Yes
- **Requires Audio Features**: No
- **Description**: Keeps tracks from the same album together in their original order, but shuffles which album plays first.
- **Parameters**:
  - `shuffle_within_albums` (string): Also shuffle track order within each album
    - Default: 'no', Options: ['no', 'yes']
- **How it works in detail**:
  1. Groups tracks by album name (extracted from track metadata)
  2. Preserves the insertion order of tracks within each album group
  3. Shuffles the order of album groups randomly
  4. Optionally shuffles tracks within each album group if `shuffle_within_albums=yes`
  5. Concatenates all album groups into the final track list
- **Example**:
  With a playlist containing Album A (tracks 1-4), Album B (tracks 5-8), Album C (tracks 9-12):
  - **shuffle_within_albums=no**: [B5, B6, B7, B8, C9, C10, C11, C12, A1, A2, A3, A4]
    - Albums shuffled, tracks within albums stay in original order
  - **shuffle_within_albums=yes**: [B7, B5, B8, B6, C11, C9, C12, C10, A3, A1, A4, A2]
    - Both albums and tracks within albums are shuffled
- **Use Cases**:
  - Multi-album playlists where album flow matters (concept albums, soundtracks)
  - "Discography" playlists where you want to listen to full albums in random order
  - Maintaining the intentional track ordering of albums while varying which album comes first


### Tempo Gradient (Hidden)
- **Class**: `TempoGradientShuffle`
- **File**: `tempo_gradient.py`
- **Visible**: **No** (hidden from UI — Spotify deprecated Audio Features API in November 2024)
- **Requires Audio Features**: **Yes** (requires `tempo` field from Spotify audio features)
- **Description**: Sorts tracks by tempo (BPM) for smooth DJ-style transitions. Choose ascending for a building energy flow, or descending to wind down.
- **Parameters**:
  - `direction` (string): Sort direction for tempo
    - Default: 'ascending', Options: ['ascending', 'descending']
- **How it works in detail**:
  1. Maps each track URI to its tempo (BPM) from audio features
  2. Falls back to 120.0 BPM for tracks without audio features data
  3. Sorts all track URIs by tempo in the specified direction
  4. Returns the sorted list
- **Why Hidden**:
  Spotify deprecated their Audio Features API endpoint in November 2024. This algorithm requires the `tempo` field from audio features to function correctly. It is registered in the `ShuffleRegistry` but excluded from `list_algorithms()` via the `_hidden_algorithms` set. It can still be accessed programmatically via `ShuffleRegistry.get_algorithm("TempoGradientShuffle")`.
  To unhide: remove `"TempoGradientShuffle"` from `_hidden_algorithms` in `registry.py` when extended API access is granted.
- **Use Cases** (when audio features are available):
  - DJ-style sets with gradual tempo increase
  - Workout playlists that build in intensity
  - Wind-down playlists that gradually slow tempo


## Algorithm Comparison

| Algorithm | Randomness Level | Structure Preservation | Requires Features | Visible | Best For |
|-----------|------------------|----------------------|-------------------|---------|----------|
| **Basic** | High | Low | No | Yes | Simple random shuffling with optional fixed start |
| **Percentage** | Medium | High | No | Yes | Partial shuffling while keeping most order intact |
| **Balanced** | Medium | Medium | No | Yes | Ensuring fair representation from all playlist regions |
| **Stratified** | Low | High | No | Yes | Controlled randomness within defined sections |
| **Artist Spacing** | Medium | N/A (constraint-based) | No | Yes | Preventing same-artist back-to-back |
| **Album Sequence** | Medium (album level) | High (within albums) | No | Yes | Keeping album tracks together, shuffling album order |
| **Tempo Gradient** | None (deterministic sort) | None | **Yes** | **No** | DJ-style BPM ordering (currently hidden) |

## Architecture

### Protocol + Registry Pattern

All algorithms implement the `ShuffleAlgorithm` protocol defined in `__init__.py`:

```python
class ShuffleAlgorithm(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def parameters(self) -> dict: ...
    @property
    def requires_features(self) -> bool: ...
    def shuffle(self, tracks: List[Dict[str, Any]],
                features: Optional[Dict[str, Dict[str, Any]]] = None,
                **kwargs) -> List[str]: ...
```

Algorithms are registered in `registry.py` via the `ShuffleRegistry` class:
- `_algorithms` dict maps class names to classes (pre-populated)
- `_hidden_algorithms` set controls UI visibility
- `list_algorithms()` returns visible algorithms in a defined display order
- `get_algorithm(name)` returns any algorithm by class name (including hidden)
- `register(cls)` adds new algorithms dynamically

### Display Order in UI

The `list_algorithms()` method returns visible algorithms in this order:
1. Basic
2. Percentage
3. Balanced
4. Stratified
5. Artist Spacing
6. Album Sequence

(Tempo Gradient is hidden and not displayed)


## Shared Utilities

**Module**: `utils.py`

Common functions used across multiple algorithms:

| Function | Description | Used By |
|----------|-------------|---------|
| `extract_uris(tracks)` | Extract track URIs from track dicts, skipping None | Basic, Percentage, Balanced, Stratified |
| `split_keep_first(uris, keep_first)` | Split URI list into kept (pinned) and shuffleable portions | Basic, Balanced, Stratified |
| `split_into_sections(items, section_count)` | Divide a list into N roughly equal sections | Balanced, Stratified |


## Adding New Algorithms

To create a new shuffle algorithm:

1. Create a new Python file in this directory (e.g., `my_algorithm.py`)
2. Implement the `ShuffleAlgorithm` protocol:
   ```python
   from typing import List, Dict, Any, Optional
   from . import ShuffleAlgorithm

   class MyAlgorithm(ShuffleAlgorithm):
       @property
       def name(self) -> str:
           return "My Algorithm"

       @property
       def description(self) -> str:
           return "What this algorithm does"

       @property
       def parameters(self) -> dict:
           return {
               'param1': {
                   'type': 'integer',
                   'description': 'Parameter description',
                   'default': 5,
                   'min': 1,
                   'max': 10
               }
           }

       @property
       def requires_features(self) -> bool:
           return False

       def shuffle(self, tracks: List[Dict[str, Any]],
                   features: Optional[Dict[str, Dict[str, Any]]] = None,
                   **kwargs) -> List[str]:
           """Returns list of track URIs in new order."""
           # Your logic here
           return [t['uri'] for t in tracks if t.get('uri')]
   ```

3. Register in `registry.py` (no decorator — explicit registration):
   - Add import: `from .my_algorithm import MyAlgorithm`
   - Add to `_algorithms` dict: `"MyAlgorithm": MyAlgorithm`
   - Add to `desired_order` list in `list_algorithms()` for UI positioning

4. Add tests in `tests/algorithms/test_my_algorithm.py`

5. To hide an algorithm from the UI (e.g., if it depends on a deprecated API):
   - Add to `_hidden_algorithms` set: `_hidden_algorithms = {"MyAlgorithm"}`

## Parameter Types

Algorithms can declare parameters with these types:

| Type | Description | Extra Fields |
|------|-------------|-------------|
| `integer` | Whole number | `default`, `min`, `max` |
| `float` | Decimal number | `default`, `min`, `max` |
| `string` | Text or enum selection | `default`, `options` (list of valid values) |

Parameters are passed as `**kwargs` to the `shuffle()` method. The frontend auto-generates form fields from the `parameters` dict.

## Best Practices

1. **Parameter Validation**: Always validate parameters and provide sensible defaults via `kwargs.get('param', default)`
2. **Edge Cases**: Handle empty playlists and single-track playlists (return early)
3. **URI Extraction**: Filter tracks with `t.get('uri')` to handle None URIs
4. **Documentation**: Include clear descriptions in `name`, `description`, and `parameters`
5. **Performance**: Consider efficiency for large playlists (1000+ tracks)
6. **Testing**: Add tests covering basic functionality, edge cases, and parameter variations
7. **Determinism**: Use `random.shuffle()` or `random.random()` for randomness (testable with seed)
