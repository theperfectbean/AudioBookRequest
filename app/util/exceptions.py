"""
Standard exception handling utilities for AudioBookRequest.

This module provides consistent exception handling patterns for common scenarios
like external API calls, database operations, and data validation.
"""
from typing import TypeVar, Callable, Any
from aiohttp import ClientError
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.util.log import logger

T = TypeVar('T')


def handle_external_api_error(
    error: Exception,
    service: str,
    operation: str,
    **context: Any
) -> None:
    """
    Standard logging for external API failures.

    Args:
        error: The caught exception
        service: Name of the external service (e.g., "Audnexus", "Google Books")
        operation: What operation was being attempted (e.g., "fetch book", "search")
        **context: Additional context to log (e.g., asin=..., title=...)

    Example:
        try:
            response = await fetch_book(asin)
        except (ClientError, ValidationError, ValueError) as e:
            handle_external_api_error(e, "Audnexus", "fetch book", asin=asin)
            return None
    """
    logger.error(
        f"{service} {operation} failed",
        error=str(error),
        error_type=type(error).__name__,
        service=service,
        operation=operation,
        **context
    )


def handle_database_error(
    error: SQLAlchemyError,
    operation: str,
    rollback_session: Any = None,
    **context: Any
) -> None:
    """
    Standard logging and handling for database errors.

    Args:
        error: The caught SQLAlchemy exception
        operation: What database operation was being attempted
        rollback_session: Optional SQLModel Session to rollback
        **context: Additional context to log

    Example:
        try:
            session.add(book)
            session.commit()
        except SQLAlchemyError as e:
            handle_database_error(e, "save book", rollback_session=session, asin=book.asin)
            raise
    """
    logger.error(
        f"Database {operation} failed",
        error=str(error),
        error_type=type(error).__name__,
        operation=operation,
        **context
    )

    if rollback_session is not None:
        try:
            rollback_session.rollback()
        except Exception as rollback_error:
            logger.warning(
                "Failed to rollback session after database error",
                error=str(rollback_error)
            )


def handle_validation_error(
    error: ValidationError,
    data_source: str,
    **context: Any
) -> None:
    """
    Standard logging for data validation failures.

    Args:
        error: The caught ValidationError
        data_source: Where the invalid data came from (e.g., "Audnexus response", "cache")
        **context: Additional context to log

    Example:
        try:
            book = Audiobook.model_validate(data)
        except ValidationError as e:
            handle_validation_error(e, "Audnexus response", asin=asin)
            return None
    """
    logger.error(
        f"{data_source} validation failed",
        error=str(error),
        error_type=type(error).__name__,
        data_source=data_source,
        **context
    )


def handle_cache_error(
    error: Exception,
    operation: str,
    cache_key: str,
    **context: Any
) -> None:
    """
    Standard logging for cache operation failures.

    Args:
        error: The caught exception
        operation: What cache operation was being attempted (e.g., "get", "set")
        cache_key: The cache key involved
        **context: Additional context to log

    Example:
        try:
            cached = cache.get(key)
        except Exception as e:
            handle_cache_error(e, "get", key)
            return None
    """
    logger.warning(
        f"Cache {operation} failed",
        error=str(error),
        error_type=type(error).__name__,
        operation=operation,
        cache_key=cache_key,
        **context
    )
