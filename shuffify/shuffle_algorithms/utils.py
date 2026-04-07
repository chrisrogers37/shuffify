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


def split_locked_tracks(
    tracks: List[Dict[str, Any]],
    locked_positions: Dict[int, str],
) -> Tuple[Dict[int, str], List[Dict[str, Any]]]:
    """
    Separate locked and unlocked tracks.

    Validates that locked positions reference tracks that actually
    exist at those positions. Invalid entries are silently dropped.

    Args:
        tracks: Full list of track dictionaries with 'uri' keys.
        locked_positions: Dict of {position: uri} for locked tracks.

    Returns:
        Tuple of (validated_locked_map, unlocked_tracks).
    """
    if not locked_positions:
        return {}, list(tracks)

    validated = {}
    locked_indices = set()
    for pos, uri in locked_positions.items():
        pos = int(pos)
        if 0 <= pos < len(tracks):
            track_uri = tracks[pos].get("uri")
            if track_uri == uri:
                validated[pos] = uri
                locked_indices.add(pos)

    unlocked = [
        t for i, t in enumerate(tracks)
        if i not in locked_indices
    ]
    return validated, unlocked


def reassemble_with_locks(
    shuffled_uris: List[str],
    locked_positions: Dict[int, str],
    total_length: int,
) -> List[str]:
    """
    Reassemble a full URI list by placing locked tracks at their
    positions and filling remaining slots with shuffled URIs.

    Args:
        shuffled_uris: URIs from the shuffle algorithm (unlocked only).
        locked_positions: Dict of {position: uri} for locked tracks.
        total_length: Total number of tracks in the playlist.

    Returns:
        Complete URI list with locks in place.
    """
    if not locked_positions:
        return shuffled_uris

    result = [None] * total_length

    for pos, uri in locked_positions.items():
        pos = int(pos)
        if 0 <= pos < total_length:
            result[pos] = uri

    unlocked_idx = 0
    for i in range(total_length):
        if result[i] is None:
            if unlocked_idx < len(shuffled_uris):
                result[i] = shuffled_uris[unlocked_idx]
                unlocked_idx += 1

    return [u for u in result if u is not None]


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
