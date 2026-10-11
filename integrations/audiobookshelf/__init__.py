"""
Module Name: __init__.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Package for the Audiobookshelf REST client.

Location:
    /integrations/audiobookshelf/__init__.py
"""

from .client import (AbsAuthError, AbsClient, AbsError, AbsForbidden, AbsNotFound,
                     AbsUnavailable)

__all__ = ['AbsAuthError', 'AbsClient', 'AbsError', 'AbsForbidden', 'AbsNotFound', 'AbsUnavailable']
