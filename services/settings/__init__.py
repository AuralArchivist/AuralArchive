"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Settings: reading, validating and saving config.txt, and the preferred
    languages.

Location:
    /services/settings/__init__.py
"""

from .management import ConfigService

__all__ = ["ConfigService"]