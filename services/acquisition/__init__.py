"""
Module Name: __init__.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Getting the books you want: indexer search, the download queue, the
    download clients and the pipeline that takes a download to import.

Location:
    /services/acquisition/__init__.py
"""

from .service import DownloadManagementService

__all__ = ['DownloadManagementService']
