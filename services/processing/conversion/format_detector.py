"""
Module Name: format_detector.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Detects and validates audiobook file formats for conversion workflows.

Location:
    /services/processing/conversion/format_detector.py
"""

import os
import mimetypes
from typing import Dict, Any, Optional
from pathlib import Path

from core.logging import get_module_logger


class FormatDetector:
    """Detector for audiobook file formats"""

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.Conversion.FormatDetector")
        
        self.format_extensions = {
            '.aax': 'aax',
            '.aaxc': 'aaxc', 
            '.mp3': 'mp3',
            '.m4a': 'm4a',
            '.m4b': 'm4b',
            '.flac': 'flac',
            '.ogg': 'ogg',
            '.oga': 'ogg',
            '.wav': 'wav',
            '.wave': 'wav'
        }
        
        self.format_mimes = {
            'audio/mp4': 'm4a',
            'audio/mpeg': 'mp3',
            'audio/flac': 'flac',
            'audio/ogg': 'ogg',
            'audio/wav': 'wav',
            'audio/x-wav': 'wav'
        }
        
        self.magic_signatures = {
            b'ID3': 'mp3',
            b'\xff\xfb': 'mp3',
            b'\xff\xf3': 'mp3',
            b'\xff\xf2': 'mp3',
            b'ftyp': 'm4a',
            b'fLaC': 'flac',
            b'OggS': 'ogg',
            b'RIFF': 'wav'
        }
    
    def detect_format(self, file_path: str) -> str:
        """Detect audio file format using multiple methods

        Returns:
            Detected format string (e.g., 'mp3', 'aax', 'm4b')
        """
        try:
            if not os.path.exists(file_path):
                self.logger.warning("Input file missing", extra={"file_path": str(file_path)})
                return 'unknown'
            
            file_path = Path(file_path)
            
            extension_format = self._detect_by_extension(file_path)
            if extension_format != 'unknown':
                self.logger.debug(
                    "Detected format by extension",
                    extra={"file_path": str(file_path), "format": extension_format},
                )
                
                if extension_format == 'm4a':
                    refined_format = self._refine_m4_format(file_path)
                    if refined_format:
                        return refined_format
                
                return extension_format
            
            mime_format = self._detect_by_mime(file_path)
            if mime_format != 'unknown':
                self.logger.debug(
                    "Detected format by MIME type",
                    extra={"file_path": str(file_path), "format": mime_format},
                )
                return mime_format
            
            magic_format = self._detect_by_magic(file_path)
            if magic_format != 'unknown':
                self.logger.debug(
                    "Detected format by magic number",
                    extra={"file_path": str(file_path), "format": magic_format},
                )
                return magic_format
            
            self.logger.warning("Could not detect format", extra={"file_path": str(file_path)})
            return 'unknown'
            
        except Exception as e:
            self.logger.exception("Error detecting format", extra={"file_path": str(file_path), "error": str(e)})
            return 'unknown'
    
    def _detect_by_extension(self, file_path: Path) -> str:
        """Detect format by file extension"""
        extension = file_path.suffix.lower()
        return self.format_extensions.get(extension, 'unknown')
    
    def _detect_by_mime(self, file_path: Path) -> str:
        """Detect format by MIME type"""
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type:
            return self.format_mimes.get(mime_type, 'unknown')
        return 'unknown'
    
    def _detect_by_magic(self, file_path: Path) -> str:
        """Detect format by magic number/file signature"""
        try:
            with open(file_path, 'rb') as f:
                header = f.read(32)
                
                for signature, format_type in self.magic_signatures.items():
                    if header.startswith(signature):
                        return format_type
                    
                    if signature == b'ftyp' and len(header) > 8 and header[4:8] == signature:
                        return self._determine_mp4_subtype(f)
                
                return 'unknown'
                
        except Exception as e:
            self.logger.exception("Error reading magic numbers", extra={"file_path": str(file_path), "error": str(e)})
            return 'unknown'
    
    def _determine_mp4_subtype(self, file_handle) -> str:
        """Determine if MP4 file is M4A, M4B, or AAX based on content"""
        try:
            file_handle.seek(0)
            header = file_handle.read(64)
            
            header_str = header.decode('utf-8', errors='ignore').lower()
            
            if 'audible' in header_str or 'aax' in header_str:
                return 'aax'
            elif 'm4b' in header_str or 'audiobook' in header_str:
                return 'm4b'
            else:
                return 'm4a'
                
        except OSError:
            return 'm4a'
    
    def _refine_m4_format(self, file_path: Path) -> Optional[str]:
        """Refine M4A detection to distinguish M4A from M4B"""
        try:
            with open(file_path, 'rb') as f:
                chunk = f.read(1024)
                content = chunk.decode('utf-8', errors='ignore').lower()
                
                if any(indicator in content for indicator in ['audiobook', 'chapters', 'm4b']):
                    return 'm4b'
                    
            return 'm4a'
            
        except OSError as e:
            self.logger.debug("Could not refine M4 format detection", extra={"file_path": str(file_path), "error": str(e)})
            return 'm4a'
    
    def validate_input_file(self, file_path: str) -> Dict[str, Any]:
        """Validate input file for conversion compatibility"""
        try:
            if not os.path.exists(file_path):
                return {
                    'valid': False,
                    'error': 'File does not exist'
                }
            
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                return {
                    'valid': False,
                    'error': 'File is empty'
                }
            
            detected_format = self.detect_format(file_path)
            if detected_format == 'unknown':
                return {
                    'valid': False,
                    'error': 'Unsupported or unrecognized file format'
                }
            
            supported_formats = ['aax', 'aaxc', 'mp3', 'm4a', 'm4b', 'flac', 'ogg', 'wav']
            if detected_format not in supported_formats:
                return {
                    'valid': False,
                    'error': f'Format {detected_format} is not supported for conversion'
                }
            
            format_validation = self._validate_format_specific(file_path, detected_format)
            
            return {
                'valid': not format_validation.get('errors'),
                'format': detected_format,
                'file_size': file_size,
                'file_size_mb': round(file_size / (1024 * 1024), 2),
                'format_validation': format_validation
            }
            
        except Exception as e:
            return {
                'valid': False,
                'error': f'Validation error: {str(e)}'
            }
    
    def _validate_format_specific(self, file_path: str, format_type: str) -> Dict[str, Any]:
        """Perform format-specific validation"""
        validation = {'checks_passed': [], 'warnings': [], 'errors': []}
        
        try:
            if format_type in ['aax', 'aaxc']:
                validation['checks_passed'].append('DRM-protected audiobook format detected')
                validation['warnings'].append('Requires activation bytes for conversion')
            
            elif format_type == 'mp3':
                with open(file_path, 'rb') as f:
                    header = f.read(10)
                    if header.startswith(b'ID3'):
                        validation['checks_passed'].append('ID3 metadata detected')
                    elif header.startswith((b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')):
                        validation['checks_passed'].append('Valid MP3 frame header detected')
                    else:
                        validation['warnings'].append('No standard MP3 header detected')
            
            elif format_type in ['m4a', 'm4b']:
                with open(file_path, 'rb') as f:
                    f.seek(4)
                    ftype = f.read(4)
                    if ftype == b'ftyp':
                        validation['checks_passed'].append('Valid MP4 container detected')
                    else:
                        validation['errors'].append('Invalid MP4 container structure')
            
            elif format_type == 'flac':
                with open(file_path, 'rb') as f:
                    header = f.read(4)
                    if header == b'fLaC':
                        validation['checks_passed'].append('Valid FLAC signature detected')
                    else:
                        validation['errors'].append('Invalid FLAC signature')
            
            file_size = os.path.getsize(file_path)
            if file_size < 1024:
                validation['warnings'].append('File is very small, may be corrupted')
            elif file_size > 2 * 1024 * 1024 * 1024:
                validation['warnings'].append('File is very large, conversion may take significant time')
            
            return validation
            
        except Exception as e:
            validation['errors'].append(f'Format validation error: {str(e)}')
            return validation
    
    def get_supported_formats(self) -> Dict[str, Any]:
        """Get information about supported formats"""
        return {
            'input_formats': {
                'aax': {
                    'description': 'Audible AAX (DRM-protected)',
                    'requires_activation_bytes': True,
                    'common_extensions': ['.aax']
                },
                'aaxc': {
                    'description': 'Audible AAXC (DRM-protected)',
                    'requires_activation_bytes': True,
                    'common_extensions': ['.aaxc']
                },
                'mp3': {
                    'description': 'MP3 Audio',
                    'requires_activation_bytes': False,
                    'common_extensions': ['.mp3']
                },
                'm4a': {
                    'description': 'MPEG-4 Audio',
                    'requires_activation_bytes': False,
                    'common_extensions': ['.m4a']
                },
                'm4b': {
                    'description': 'MPEG-4 Audiobook',
                    'requires_activation_bytes': False,
                    'common_extensions': ['.m4b']
                },
                'flac': {
                    'description': 'FLAC Lossless Audio',
                    'requires_activation_bytes': False,
                    'common_extensions': ['.flac']
                },
                'ogg': {
                    'description': 'Ogg Vorbis Audio',
                    'requires_activation_bytes': False,
                    'common_extensions': ['.ogg', '.oga']
                },
                'wav': {
                    'description': 'WAV Audio',
                    'requires_activation_bytes': False,
                    'common_extensions': ['.wav', '.wave']
                }
            },
            'output_format': {
                'm4b': {
                    'description': 'M4B Audiobook format (optimal for audiobooks)',
                    'supports_chapters': True,
                    'supports_metadata': True,
                    'compatible_players': ['AudiobookShelf', 'Apple Books', 'Smart AudioBook Player']
                }
            }
        }