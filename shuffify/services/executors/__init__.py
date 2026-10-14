"""
Job executor package.

Modules:
- base_executor: Lifecycle, token management, dispatch, shared utilities
- raid_executor: Raid-specific operations
- drip_executor: Move tracks from the raid playlist into the target playlist
- shuffle_executor: Shuffle-specific operations
- rotate_executor: Rotation modes and pairing logic

Public API:
    from shuffify.services.executors import (
        JobExecutorService,
        JobExecutionError,
    )
"""

from shuffify.services.executors.base_executor import (
    JobExecutionError,
    JobExecutorService,
    PlaylistVerificationError,
    verify_playlist_state,
)

__all__ = [
    "JobExecutorService",
    "JobExecutionError",
    "PlaylistVerificationError",
    "verify_playlist_state",
]
