# Shuffle Algorithms

This directory contains various algorithms for shuffling Spotify playlists, each with its own unique approach and parameters.

## Available Algorithms

### Basic
- **Class**: `BasicShuffle`
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


### Balanced
- **Class**: `BalancedShuffle`
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

### Percentage
- **Class**: `PercentageShuffle`
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

### Stratified
- **Class**: `StratifiedShuffle`
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


## Algorithm Comparison

| Algorithm | Randomness Level | Structure Preservation | Best For |
|-----------|------------------|----------------------|----------|
| **Basic** | High | Low | Simple random shuffling with optional fixed start |
| **Percentage** | Medium | High | Partial shuffling while keeping most order intact |
| **Balanced** | Medium | Medium | Ensuring fair representation from all playlist regions |
| **Stratified** | Low | High | Controlled randomness within defined sections |

## Adding New Algorithms

To create a new shuffle algorithm:

1. Create a new Python file in this directory
2. Inherit from the `ShuffleAlgorithm` base class
3. Implement required methods:
   ```python
   @property
   def name(self) -> str:
       """Human-readable name"""
   
   @property
   def description(self) -> str:
       """How the algorithm works"""
   
   @property
   def parameters(self) -> dict:
       """Parameter specifications"""
   
   def shuffle(self, tracks: List[Dict[str, Any]], features: Optional[Dict[str, Dict[str, Any]]] = None, **kwargs) -> List[str]:
       """Actual shuffle implementation - returns list of track URIs"""
   ```
4. Register your algorithm in `registry.py`

## Best Practices

1. **Parameter Validation**: Always validate parameters and provide sensible defaults
2. **Error Handling**: Handle edge cases (empty playlists, invalid parameters)
3. **Documentation**: Include clear descriptions and examples
4. **Performance**: Consider efficiency for large playlists
5. **Testing**: Add unit tests for your algorithm 