"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Package exports for audiobook conversion services and helper utilities.

Location:
    /services/processing/conversion/__init__.py
"""

from .conversion_service import ConversionService
from .ffmpeg_handler import FFmpegHandler
from .format_detector import FormatDetector
from .metadata_processor import MetadataProcessor

__all__ = [
    "ConversionService",
    "FFmpegHandler",
    "FormatDetector",
    "MetadataProcessor",
]