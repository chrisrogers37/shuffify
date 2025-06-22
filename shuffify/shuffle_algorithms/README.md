# Shuffle Algorithms

This directory contains various algorithms for shuffling Spotify playlists, each with its own unique approach and parameters.

## Available Algorithms

### Basic
- **Class**: `BasicShuffle`
- **Description**: Standard random shuffle with the option to keep tracks fixed at the start.
- **Parameters**:
  - `keep_first` (integer): Number of tracks to keep in their original position at the start
  - Default: 0, Min: 0


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
  - `shuffle_location` (string): Choose 'front' to shuffle the beginning or 'back' to shuffle the end
- **Example**:
  With 100 tracks and 30% shuffle:
  - Back: First 70 tracks stay in order, last 30 get shuffled
  - Front: First 30 tracks get shuffled, last 70 stay in order

### Stratified
- **Class**: `StratifiedShuffle`
- **Description**: Groups tracks by a specific audio feature (like 'danceability' or 'energy') and then shuffles the groups, preserving the internal order of each group.
- **Parameters**:
  - `feature` (string): The audio feature to group by. Options include `danceability`, `energy`, `valence`, `acousticness`, `instrumentalness`, `liveness`, `speechiness`.
  - `bins` (integer): The number of groups (bins) to create based on the feature's value.
    - Default: 5, Min: 2, Max: 10
- **Example**:
  With `feature='energy'` and `bins=3`:
  1. Tracks are fetched with their audio features from Spotify.
  2. Tracks are divided into 3 groups: low energy, medium energy, and high energy.
  3. The *groups themselves* are shuffled. For example, the new order might be [Medium Energy Group, High Energy Group, Low Energy Group].
  4. The final playlist is constructed by concatenating the tracks from the shuffled groups. The order of tracks *within* each group remains unchanged.
- **Use Cases**:
  - Creating a workout playlist that builds from low to high energy (by not shuffling the groups).
  - Creating a mood-based playlist that flows from happy to sad songs.
  - Interspersing instrumental and vocal tracks in a study playlist.


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
   
   def shuffle(self, tracks: List[str], sp: Optional[Spotify] = None, **kwargs) -> List[str]:
       """Actual shuffle implementation"""
   ```
4. Register your algorithm in `registry.py`

## Best Practices

1. **Parameter Validation**: Always validate parameters and provide sensible defaults
2. **Error Handling**: Handle edge cases (empty playlists, invalid parameters)
3. **Documentation**: Include clear descriptions and examples
4. **Performance**: Consider efficiency for large playlists
5. **Testing**: Add unit tests for your algorithm 