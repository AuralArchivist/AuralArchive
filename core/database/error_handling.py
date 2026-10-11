"""
Module Name: error_handling.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Retry and cleanup helpers for database operations.

Location:
    /core/database/error_handling.py
"""

import sqlite3
from functools import wraps
from typing import Callable, Any
import sqlalchemy.exc
from core.logging import get_module_logger
from core.retry import run_with_retry


def _is_locked(exc: Exception) -> bool:
    return isinstance(exc, (sqlite3.OperationalError, sqlalchemy.exc.OperationalError)) and "database is locked" in str(exc)


class DatabaseErrorHandler:
    """Shared error handling and retry logic for database operations."""

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.Database.ErrorHandling")
    
    def with_retry(self, max_retries: int = 3, retry_delay: float = 0.5):
        """Decorator for database operations: retries while the database is locked."""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs) -> Any:
                try:
                    return run_with_retry(lambda: func(*args, **kwargs), attempts=max_retries,
                                          delay=retry_delay, retry_on=_is_locked, on_retry=self._log_locked_retry)
                except (sqlite3.IntegrityError, sqlalchemy.exc.IntegrityError) as e:
                    if "UNIQUE constraint failed" in str(e):
                        self.logger.warning("Duplicate entry detected", extra={"error": str(e)})
                        return False
                    self.logger.error("Database integrity error", extra={"error": str(e)})
                    raise
                except (sqlite3.OperationalError, sqlalchemy.exc.OperationalError) as e:
                    if _is_locked(e):
                        self.logger.error("Database remained locked after retries", extra={"retries": max_retries})
                    else:
                        self.logger.error("Database operational error", extra={"error": str(e)})
                    raise
                except Exception as e:
                    self.logger.error("Unexpected database error", extra={"error": str(e)})
                    raise
            return wrapper
        return decorator

    def _log_locked_retry(self, exc: Exception, attempt: int, delay: float) -> None:
        self.logger.warning("Database locked, retrying", extra={"delay_seconds": delay, "attempt": attempt})
    
    def log_operation(self, operation: str, details: str = ""):
        """Log database operations consistently"""
        if details:
            self.logger.info(operation, extra={"details": details})
        else:
            self.logger.info(operation)
    
    def validate_required_fields(self, data: dict, required_fields: list) -> bool:
        """Validate that required fields are present in data"""
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            self.logger.error(
                "Missing required fields",
                extra={"fields": missing_fields},
            )
            return False
        return True

error_handler = DatabaseErrorHandler()
