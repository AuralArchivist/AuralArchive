"""
Module Name: validation.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Validation logic for import operations, including filesystem checks,
    supported format validation, and audio quality detection via ffprobe
    when available.

Location:
    /services/processing/filing/validation.py
"""

import os
from typing import Dict, Tuple
from pathlib import Path
import subprocess

from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Import.Validator")


class ImportValidator:
    """Validates import operations and file integrity."""
    
    SUPPORTED_FORMATS = {
        'm4b', 'm4a', 'mp3', 'mp4', 'flac', 'ogg', 'opus', 'aac'
    }
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
    
    def validate_import_request(self, source_file_path: str, book_data: Dict) -> Tuple[bool, str]:
        """Validate an import request.

        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        try:
            if not os.path.exists(source_file_path):
                return False, f"Source file does not exist: {source_file_path}"
            
            if not os.path.isfile(source_file_path):
                return False, f"Source is not a file: {source_file_path}"
            
            if not os.access(source_file_path, os.R_OK):
                return False, f"Source file is not readable: {source_file_path}"
            
            file_ext = Path(source_file_path).suffix.lstrip('.').lower()
            if file_ext not in self.SUPPORTED_FORMATS:
                return False, f"Unsupported file format: {file_ext}"
            
            file_size = os.path.getsize(source_file_path)
            if file_size == 0:
                return False, "Source file is empty (0 bytes)"
            
            if not book_data:
                return False, "Book metadata is required"
            
            if not (book_data.get('Title') or book_data.get('title')):
                return False, "Book metadata missing: Title"
            
            if not (book_data.get('AuthorName') or book_data.get('Author') or book_data.get('author')):
                self.logger.warning("Book metadata missing Author - will use 'Unknown Author'")
            
            if not (book_data.get('ASIN') or book_data.get('asin')):
                self.logger.warning("Book metadata missing ASIN - import tracking may be limited")
            
            return True, ""
            
        except Exception as e:
            self.logger.error(f"Error validating import request: {e}", exc_info=True)
            return False, f"Validation error: {str(e)}"
    
    def detect_file_quality(self, file_path: str) -> str:
        """Detect audio file quality (bitrate, sample rate, etc.).

        Returns:
            Quality descriptor string (e.g., "M4B 128kbps Stereo")
        """
        try:
            file_ext = Path(file_path).suffix.lstrip('.').upper()
            
            try:
                result = subprocess.run(
                    [
                        'ffprobe',
                        '-v', 'quiet',
                        '-select_streams', 'a:0',
                        '-show_entries', 'stream=codec_name,bit_rate,sample_rate,channels',
                        '-of', 'default=noprint_wrappers=1',
                        file_path
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0:
                    output = result.stdout
                    bitrate = self._extract_value(output, 'bit_rate')
                    channels = self._extract_value(output, 'channels')
                    
                    quality_parts = [file_ext]
                    
                    if bitrate:
                        try:
                            quality_parts.append(f"{int(bitrate) // 1000}kbps")
                        except ValueError:
                            pass
                    
                    if channels:
                        try:
                            ch = int(channels)
                        except ValueError:
                            ch = 0
                        if ch:
                            quality_parts.append('Stereo' if ch == 2 else f'{ch}ch')
                    
                    return ' '.join(quality_parts)
                
            except FileNotFoundError:
                self.logger.debug("ffprobe not found, using basic quality detection")
            except subprocess.TimeoutExpired:
                self.logger.warning(f"ffprobe timeout for {file_path}")
            except Exception as e:
                self.logger.debug(f"ffprobe failed: {e}")
            
            file_size = os.path.getsize(file_path)
            size_mb = file_size / (1024 * 1024)
            
            return f"{file_ext} ({size_mb:.1f} MB)"
            
        except Exception as e:
            self.logger.error(f"Error detecting file quality: {e}", exc_info=True)
            return "Unknown Quality"
    
    def _extract_value(self, text: str, key: str) -> str:
        """Extract a value from ffprobe output."""
        for line in text.split('\n'):
            if line.startswith(f"{key}="):
                return line.split('=', 1)[1].strip()
        return ""
    