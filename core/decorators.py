"""
Module Name: decorators.py
Author: AuralArchivist
Created: June 4, 2026
Last Modified: October 9, 2026
Description:
    Shared Flask view decorators. handle_errors() turns an uncaught exception
    in an API view into a logged traceback plus a generic JSON 500, so
    internal error details never reach the client.

Location:
    /core/decorators.py
"""

from functools import wraps

from flask import jsonify

from core.logging import get_module_logger

_logger = get_module_logger("Core.Decorators")


def handle_errors(f):
    """Decorator to handle API errors consistently across all API blueprints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception:
            _logger.exception(f"API Error in {f.__name__}")
            return jsonify({'error': 'Internal error (details in server log)', 'success': False}), 500
    return decorated_function
