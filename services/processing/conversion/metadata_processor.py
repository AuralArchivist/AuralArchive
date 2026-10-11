"""
Module Name: metadata_processor.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Extracts, preserves, and embeds metadata during audiobook conversion flows.

Location:
    /services/processing/conversion/metadata_processor.py
"""

import os
import subprocess
import json
from typing import Dict, Any, Optional

from core.logging import get_module_logger


class MetadataProcessor:
    """Processor for audiobook metadata and chapters"""

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.Conversion.MetadataProcessor")
        
        self.metadata_fields = {
            'title': 'title',
            'artist': 'artist',
            'album': 'album',
            'albumartist': 'album_artist',
            'composer': 'composer',
            'date': 'date',
            'year': 'year',
            'genre': 'genre',
            'comment': 'comment',
            'description': 'description',
            'synopsis': 'synopsis',
            'narrator': 'narrator',
            'author': 'author',
            'publisher': 'publisher',
            'series': 'series',
            'series_part': 'series_part'
        }
    
    def process_metadata(
        self, 
        output_file: str, 
        quality_settings: Dict[str, Any], 
        custom_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Process and embed metadata into the output file"""
        try:
            self.logger.info("Processing metadata", extra={"output_file": output_file})
            
            results = {
                'success': True,
                'processed': [],
                'warnings': [],
                'errors': []
            }
            
            existing_metadata = self._extract_metadata(output_file)
            results['existing_metadata'] = existing_metadata
            
            if quality_settings.get('preserve_chapters', True):
                chapter_result = self._process_chapters(output_file)
                results['chapter_processing'] = chapter_result
                if chapter_result.get('success'):
                    results['processed'].append('chapters')
                else:
                    results['warnings'].append(f'Chapter processing: {chapter_result.get("error")}')
            
            if quality_settings.get('embed_cover', True):
                cover_result = self._process_cover_art(output_file, custom_metadata)
                results['cover_processing'] = cover_result
                if cover_result.get('success'):
                    results['processed'].append('cover_art')
                else:
                    results['warnings'].append(f'Cover processing: {cover_result.get("error")}')
            
            if quality_settings.get('preserve_metadata', True):
                cleanup_result = self._standardize_metadata(output_file, custom_metadata)
                results['metadata_cleanup'] = cleanup_result
                if cleanup_result.get('success'):
                    results['processed'].append('metadata_standardization')
                else:
                    results['warnings'].append(f'Metadata cleanup: {cleanup_result.get("error")}')
            
            validation_result = self._validate_processed_file(output_file)
            results['validation'] = validation_result
            
            if not validation_result.get('success'):
                results['success'] = False
                results['errors'].append(validation_result.get('error', 'File validation failed'))
            
            self.logger.info(
                "Metadata processing complete",
                extra={
                    "output_file": output_file,
                    "processed": results['processed'],
                    "warnings": results['warnings'],
                    "errors": results['errors']
                }
            )
            
            return results
            
        except Exception as e:
            self.logger.exception("Metadata processing failed", extra={"output_file": output_file, "error": str(e)})
            return {
                'success': False,
                'error': str(e)
            }
    
    def _extract_metadata(self, file_path: str) -> Dict[str, Any]:
        """Extract metadata from audio file using FFprobe"""
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_chapters',
                file_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                probe_data = json.loads(result.stdout)
                
                format_info = probe_data.get('format', {})
                chapters = probe_data.get('chapters', [])
                
                return {
                    'tags': format_info.get('tags', {}),
                    'duration': float(format_info.get('duration', 0)),
                    'bitrate': int(format_info.get('bit_rate', 0)),
                    'chapters': chapters,
                    'chapter_count': len(chapters)
                }
            else:
                self.logger.warning("Could not extract metadata", extra={"file_path": file_path, "stderr": result.stderr})
                return {}
                
        except Exception as e:
            self.logger.exception("Error extracting metadata", extra={"file_path": file_path, "error": str(e)})
            return {}
    
    def _process_chapters(self, file_path: str) -> Dict[str, Any]:
        """Check the chapters ffmpeg carried through conversion."""
        try:
            chapters = self._extract_metadata(file_path).get('chapters', [])
            timed = [c for c in chapters if 'start_time' in c and 'end_time' in c]
            untitled = sum(1 for c in timed if not (c.get('tags') or {}).get('title'))

            if len(timed) < len(chapters):
                self.logger.warning("Chapters missing timing information",
                                    extra={"file_path": file_path, "count": len(chapters) - len(timed)})
            if untitled:
                self.logger.warning("Chapters without titles",
                                    extra={"file_path": file_path, "count": untitled})
            self.logger.info(f"Chapters: {len(timed)}", extra={"file_path": file_path})

            return {
                'success': True,
                'chapter_count': len(timed),
                'message': f'{len(timed)} chapters' if timed else 'No chapters found in file'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    def _process_cover_art(self, file_path: str, custom_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Process and embed cover art"""
        try:
            existing_cover = self._extract_cover_art(file_path)
            
            cover_path = None
            if custom_metadata and 'cover_path' in custom_metadata:
                cover_path = custom_metadata['cover_path']
            elif existing_cover:
                return {
                    'success': True,
                    'message': 'Cover art already present',
                    'has_cover': True
                }
            
            if not cover_path:
                cover_path = self._find_cover_file(file_path)
            
            if cover_path and os.path.exists(cover_path):
                embed_result = self._embed_cover_art(file_path, cover_path)
                return {
                    'success': embed_result,
                    'cover_path': cover_path,
                    'message': 'Cover art embedded' if embed_result else 'Failed to embed cover art'
                }
            else:
                return {
                    'success': True,
                    'message': 'No cover art found to embed',
                    'has_cover': False
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _extract_cover_art(self, file_path: str) -> bool:
        """Check if file has embedded cover art"""
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=codec_name',
                '-of', 'csv=p=0',
                file_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return result.returncode == 0 and result.stdout.strip() != ''
            
        except (OSError, subprocess.SubprocessError):
            self.logger.debug("ffprobe cover check failed for %s", file_path, exc_info=True)
            return False
    
    def _find_cover_file(self, file_path: str) -> Optional[str]:
        """Find cover art file in the same directory"""
        directory = os.path.dirname(file_path)
        cover_names = ['cover', 'folder', 'albumart', 'front']
        cover_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.gif']
        
        for name in cover_names:
            for ext in cover_extensions:
                cover_file = os.path.join(directory, f"{name}{ext}")
                if os.path.exists(cover_file):
                    return cover_file
        
        return None
    
    @staticmethod
    def _temp_path(file_path: str, tag: str) -> str:
        """Temp file next to file_path that keeps its extension, so ffmpeg knows the format."""
        root, ext = os.path.splitext(file_path)
        return f"{root}.{tag}{ext}"

    def _embed_cover_art(self, file_path: str, cover_path: str) -> bool:
        """Embed cover art into the audio file"""
        try:
            temp_output = self._temp_path(file_path, 'cover_temp')

            cmd = [
                'ffmpeg', '-y',
                '-i', file_path,
                '-i', cover_path,
                '-c', 'copy',
                '-map', '0:a',
                '-map', '1',
                '-disposition:v:0', 'attached_pic',
                temp_output
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0 and os.path.exists(temp_output):
                os.replace(temp_output, file_path)
                return True
            else:
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                self.logger.warning("Cover embedding failed", extra={"file_path": file_path, "cover_path": cover_path, "stderr": result.stderr})
                return False
                
        except Exception as e:
            self.logger.exception("Error embedding cover art", extra={"file_path": file_path, "cover_path": cover_path, "error": str(e)})
            return False
    
    def _standardize_metadata(self, file_path: str, custom_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Standardize and clean up metadata tags"""
        try:
            if not custom_metadata:
                return {
                    'success': True,
                    'message': 'No custom metadata to process'
                }
            
            metadata_params = []
            
            for key, value in custom_metadata.items():
                if key in self.metadata_fields and value:
                    ffmpeg_key = self.metadata_fields[key]
                    metadata_params.extend(['-metadata', f'{ffmpeg_key}={value}'])
            
            if not metadata_params:
                return {
                    'success': True,
                    'message': 'No metadata to standardize'
                }
            
            temp_output = self._temp_path(file_path, 'metadata_temp')
            
            cmd = ['ffmpeg', '-y', '-i', file_path, '-c', 'copy'] + metadata_params + [temp_output]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0 and os.path.exists(temp_output):
                os.replace(temp_output, file_path)
                return {
                    'success': True,
                    'message': f'Applied {len(metadata_params)//2} metadata fields'
                }
            else:
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                return {
                    'success': False,
                    'error': f'Metadata application failed: {result.stderr}'
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _validate_processed_file(self, file_path: str) -> Dict[str, Any]:
        try:
            if not os.path.exists(file_path):
                return {
                    'success': False,
                    'error': 'Processed file does not exist'
                }
            
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                return {
                    'success': False,
                    'error': 'Processed file is empty'
                }
            
            metadata = self._extract_metadata(file_path)
            if not metadata:
                return {
                    'success': False,
                    'error': 'Could not read metadata from processed file'
                }
            
            return {
                'success': True,
                'file_size': file_size,
                'duration': metadata.get('duration', 0),
                'has_chapters': metadata.get('chapter_count', 0) > 0,
                'metadata_fields': len(metadata.get('tags', {}))
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }