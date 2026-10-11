"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Expose the Audnexus audiobook metadata service helpers.

Location:
    /integrations/audnexus/__init__.py
"""

from .audnexus_client import AudnexusClient

__all__ = ["AudnexusClient"]
