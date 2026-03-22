"""
Shared scheduling utilities.

Cron expression builders and schedule type helpers used by
both raid and rotation scheduling.
"""

import re


# Frequencies that support a time-of-day picker
TIME_CAPABLE_FREQUENCIES = {"daily", "every_3d", "weekly"}

# HH:MM pattern
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def build_cron(schedule_value: str, schedule_time: str) -> str:
    """Convert frequency + HH:MM into a 5-field cron expression.

    Args:
        schedule_value: One of 'daily', 'every_3d', 'weekly'.
        schedule_time: Time in HH:MM format.

    Returns:
        A 5-field cron expression string.

    Raises:
        ValueError: If schedule_value is not time-capable.
    """
    hours, minutes = schedule_time.split(":")
    if schedule_value == "daily":
        return f"{minutes} {hours} * * *"
    if schedule_value == "every_3d":
        return f"{minutes} {hours} */3 * *"
    if schedule_value == "weekly":
        return f"{minutes} {hours} * * 0"
    raise ValueError(
        f"Cannot build cron for frequency: {schedule_value}"
    )
