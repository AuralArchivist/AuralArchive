"""
Module Name: error_handling.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Error handling utilities for metadata updates, including retry helpers
    and validation/sanitization routines.

Location:
    /services/metadata/error_handling.py
"""

from functools import wraps
from typing import Callable, Tuple

from core.logging import get_module_logger
from core.retry import run_with_retry


_LOGGER = get_module_logger("Service.Metadata.ErrorHandling")

class MetadataErrorHandler:
    """Handles errors and retry logic for metadata update operations"""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
    
    def with_retry(self, max_retries: int = 2, retry_delay: float = 1.0):
        """Decorator for metadata operations; returns (False, message) on failure."""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs) -> Tuple[bool, str]:
                def log_retry(error: Exception, attempt: int, delay: float) -> None:
                    self.logger.warning("Retrying operation", extra={
                        "operation": func.__name__, "delay_seconds": delay, "attempt": attempt,
                        "max_retries": max_retries, "error": str(error),
                    })
                try:
                    return run_with_retry(lambda: func(*args, **kwargs), attempts=max_retries, delay=retry_delay,
                                          retry_on=lambda e: not self._is_permanent_error(e), on_retry=log_retry)
                except Exception as e:
                    if self._is_permanent_error(e):
                        self.logger.error("Permanent error in operation",
                                          extra={"operation": func.__name__, "error": str(e)}, exc_info=True)
                        return False, f"Permanent error: {str(e)}"
                    self.logger.error("Final retry failed",
                                      extra={"operation": func.__name__, "error": str(e)}, exc_info=True)
                    return False, f"Failed after {max_retries} attempts: {str(e)}"
            return wrapper
        return decorator
    
    def _is_permanent_error(self, error: Exception) -> bool:
        """Determine if an error is permanent and shouldn't be retried"""
        error_str = str(error).lower()
        
        permanent_indicators = [
            "book not found",
            "invalid asin",
            "no book found with id",
            "required fields missing",
            "invalid book data"
        ]
        
        return any(indicator in error_str for indicator in permanent_indicators)
    
    def validate_book_data(self, book_data: dict) -> Tuple[bool, str]:
        """Validate book data before processing"""
        try:
            required_fields = ['Title']
            missing_fields = [field for field in required_fields if not book_data.get(field)]
            
            if missing_fields:
                return False, f"Missing required fields: {missing_fields}"
            
            asin = book_data.get('ASIN', '')
            if asin and asin != 'N/A':
                if not self._is_valid_asin(asin):
                    return False, f"Invalid ASIN format: {asin}"
            
            title = book_data.get('Title', '')
            if len(title) > 500:
                return False, "Title too long (max 500 characters)"
            
            return True, "Validation passed"
        
        except Exception as e:
            self.logger.error(
                "Error validating book data",
                extra={"error": str(e)},
                exc_info=True,
            )
            return False, f"Validation error: {str(e)}"
    
    def _is_valid_asin(self, asin: str) -> bool:
        """Basic ASIN format validation"""
        if not asin or len(asin) != 10:
            return False
        
        return asin.isalnum()
    
    def sanitize_metadata(self, metadata: dict) -> dict:
        """Sanitize metadata to prevent issues during database updates"""
        try:
            sanitized = {}
            
            field_rules = {
                'Title': {'max_length': 500, 'required': True},
                'Author': {'max_length': 300, 'default': 'Unknown Author'},
                'Series': {'max_length': 200, 'default': 'N/A'},
                'Sequence': {'max_length': 50, 'default': 'N/A'},
                'Narrator': {'max_length': 300, 'default': 'Unknown Narrator'},
                'Runtime': {'max_length': 50, 'default': 'Unknown Runtime'},
                'Release Date': {'max_length': 50, 'default': 'Unknown'},
                'Language': {'max_length': 50, 'default': 'Unknown'},
                'Publisher': {'max_length': 200, 'default': 'Unknown Publisher'},
                'Overall Rating': {'max_length': 10, 'default': 'N/A'},
                'ASIN': {'max_length': 15, 'default': ''},
                'Summary': {'max_length': 5000, 'default': 'No summary available'},
                'Cover Image': {'max_length': 500, 'default': ''},
                'num_ratings': {'max_length': 10, 'default': 0},
                'series_asin': {'max_length': 20, 'default': ''}
            }
            
            aliases = {
                'Title': 'title', 'Author': 'author', 'Series': 'series_title',
                'Sequence': 'series_sequence', 'Narrator': 'narrator',
                'Runtime': 'runtime_length_min', 'Release Date': 'release_date',
                'Language': 'language', 'Publisher': 'publisher',
                'Overall Rating': 'rating', 'ASIN': 'asin', 'Summary': 'summary',
                'Cover Image': 'cover_image_url',
            }

            for field, rules in field_rules.items():
                value = metadata.get(field)
                alias_value = metadata.get(aliases[field]) if field in aliases else None
                if value in (None, '') and alias_value not in (None, ''):
                    value = alias_value
                elif field not in metadata:
                    value = rules.get('default', '')
                
                if value is None:
                    value = rules.get('default', '')
                else:
                    value = str(value)
                
                max_length = rules.get('max_length', 1000)
                if len(value) > max_length:
                    original_length = len(value)
                    value = value[:max_length-3] + "..."
                    self.logger.warning(
                        "Truncated field during sanitization",
                        extra={"field": field, "original_length": original_length, "max_length": max_length},
                    )
                
                value = value.strip()
                
                if rules.get('required') and not value:
                    value = rules.get('default', 'Unknown')
                
                sanitized[field] = value

            if 'series_asin' in metadata and 'series_asin' not in sanitized:
                sanitized['series_asin'] = str(metadata.get('series_asin') or '').strip()
            
            return sanitized
        
        except Exception as e:
            self.logger.error(
                "Error sanitizing metadata",
                extra={"error": str(e)},
                exc_info=True,
            )
            return metadata
    
    def log_update_attempt(self, book_id: int, operation: str, details: str = ""):
        """Log metadata update attempts for debugging"""
        payload = {"book_id": book_id, "operation": operation}
        if details:
            payload["details"] = details
        self.logger.info("Metadata update attempt", extra=payload)
    
    def log_update_result(self, book_id: int, success: bool, message: str):
        """Log the result of metadata update operations"""
        if success:
            self.logger.info(
                "Metadata update successful",
                extra={"book_id": book_id, "result_message": message},
            )
        else:
            self.logger.warning(
                "Metadata update failed",
                extra={"book_id": book_id, "result_message": message},
            )

error_handler = MetadataErrorHandler()
