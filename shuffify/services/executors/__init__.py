"""
Job executor package.

Splits the monolithic job_executor_service into focused modules:
- base_executor: Lifecycle, token management, dispatch, shared utilities
- raid_executor: Raid-specific operations
- shuffle_executor: Shuffle-specific operations
- rotate_executor: Rotation modes and pairing logic

Public API (backward-compatible):
    from shuffify.services.executors import (
        JobExecutorService,
        JobExecutionError,
    )
"""

from shuffify.services.executors.base_executor import (
    JobExecutorService,
    JobExecutionError,
)

__all__ = [
    "JobExecutorService",
    "JobExecutionError",
]
