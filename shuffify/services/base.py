"""
Shared service utilities to reduce CRUD boilerplate.

Provides common patterns for database commit safety, user lookup,
and entity ownership verification. Used across all service modules.
"""

import logging
from typing import Type, Optional

from shuffify.models.db import db, User

logger = logging.getLogger(__name__)


def safe_commit(
    operation_name: str,
    exception_class: Type[Exception] = Exception,
) -> None:
    """
    Commit the current database session with rollback on failure.

    Wraps db.session.commit() in a try/except. On success, logs an
    info message. On failure, rolls back, logs the error with
    exc_info, and raises the specified exception class.

    Args:
        operation_name: Human-readable description of the operation
            (used in log messages and exception text).
        exception_class: The exception class to raise on failure.
            Defaults to Exception.

    Raises:
        The specified exception_class with a message describing
        the failure.
    """
    try:
        db.session.commit()
        logger.info("Success: %s", operation_name)
    except Exception as e:
        db.session.rollback()
        logger.error(
            "Failed to %s: %s",
            operation_name,
            e,
            exc_info=True,
        )
        raise exception_class(
            f"Failed to {operation_name}: {e}"
        )


def get_user_or_raise(
    spotify_id: str,
    exception_class: Optional[Type[Exception]] = None,
) -> Optional[User]:
    """
    Look up a User by spotify_id.

    If exception_class is provided, raises it when the user is not
    found. If exception_class is None, returns None when not found.

    Args:
        spotify_id: The Spotify user ID to look up.
        exception_class: Optional exception class to raise if user
            not found. If None, returns None instead of raising.

    Returns:
        The User instance, or None if not found and no
        exception_class was specified.

    Raises:
        The specified exception_class if user is not found and
        exception_class is not None.
    """
    user = User.query.filter_by(spotify_id=spotify_id).first()
    if not user and exception_class is not None:
        raise exception_class(
            f"User not found for spotify_id: {spotify_id}"
        )
    return user


def get_owned_entity(
    entity_class,
    entity_id: int,
    user_id: int,
    exception_class: Type[Exception],
):
    """
    Fetch an entity by primary key and verify ownership.

    Uses db.session.get() to fetch the entity, then checks that
    entity.user_id matches the provided user_id.

    Args:
        entity_class: The SQLAlchemy model class to query.
        entity_id: The primary key ID of the entity.
        user_id: The expected owner's internal database user ID.
        exception_class: The exception class to raise if the entity
            is not found or ownership does not match.

    Returns:
        The entity instance.

    Raises:
        The specified exception_class if entity is not found or
        user_id does not match.
    """
    entity = db.session.get(entity_class, entity_id)
    if not entity or entity.user_id != user_id:
        raise exception_class(
            f"{entity_class.__name__} {entity_id} not found"
        )
    return entity
