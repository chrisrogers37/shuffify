"""
Job executor package.

Modules:
- base_executor: The Spotify client factory for background work, and the
  errors the operation executors raise

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
)

__all__ = [
    "JobExecutorService",
    "JobExecutionError",
    "PlaylistVerificationError",
]
