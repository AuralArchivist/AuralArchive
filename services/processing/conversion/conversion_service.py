"""
Module Name: conversion_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    FFmpeg-backed audiobook conversion orchestrator with quality and metadata
    management.

Location:
    /services/processing/conversion/conversion_service.py
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional
from pathlib import Path

from services.container import container
from core.logging import get_module_logger

class ConversionService:
    """Main service for audiobook format conversion using FFmpeg"""

    def __init__(
        self,
        *,
        logger=None,
        config_service=None,
        audible_library_client=None,
        helpers=None,
        **_kwargs: Any,
    ):
        self.logger = logger or get_module_logger("Service.Conversion.Main")
        self.config_service = config_service
        self.audible_library_client = audible_library_client
        self.helpers = helpers

        self._initialize_helpers()

        self.logger.success("Conversion service started successfully")
    
    def _initialize_helpers(self):
        """Initialize helper modules"""
        if self.helpers:
            return
        try:
            from .ffmpeg_handler import FFmpegHandler
            from .format_detector import FormatDetector
            from .metadata_processor import MetadataProcessor
            
            self.helpers = {
                'ffmpeg': FFmpegHandler(),
                'format_detector': FormatDetector(),
                'metadata_processor': MetadataProcessor(),
            }
            
            self.logger.debug("Helper modules initialized")
            
        except ImportError as e:
            self.logger.exception("Failed to initialize helper modules", extra={"error": str(e)})
            self.helpers = {}
    
    def _get_config_service(self):
        """Get config service instance"""
        if self.config_service is None:
            self.config_service = container.get_config_service()
        return self.config_service
    
    def _get_audible_library_client(self):
        """Get audible library service instance"""
        if self.audible_library_client is None:
            try:
                from integrations.audible.library.library_client import AudibleLibraryClient
                self.audible_library_client = AudibleLibraryClient()
            except ImportError as e:
                self.logger.exception("Failed to import audible library service", extra={"error": str(e)})
        return self.audible_library_client
    
    def _temp_dir_for(self, output_file: str) -> str:
        """Temp files go next to the output (the download's convert_<id>
        folder), so deleting that one folder removes every trace of a
        conversion - finished, failed or interrupted."""
        out_dir = os.path.dirname(output_file or '')
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            return out_dir
        return self._get_shared_temp_dir()

    @staticmethod
    def _remove_quietly(*paths) -> None:
        for path in paths:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _get_shared_temp_dir(self) -> str:
        """Scratch space for conversions: <work>/conversion."""
        try:
            from core.paths import get_path_resolver
            temp_dir = get_path_resolver().get_conversion_dir()
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            return temp_dir

        except Exception as e:
            self.logger.exception("Failed to get shared temp dir", extra={"error": str(e)})
            return tempfile.gettempdir()
    
    def get_quality_settings(self) -> Dict[str, Any]:
        """Get current quality settings from config"""
        default_settings = {
            'codec': 'aac',
            'bitrate': '64k',
            'sample_rate': '22050',
            'channels': '1',
            'profile': '',
            'format': 'm4b',
            'preserve_chapters': True,
            'embed_cover': True,
            'preserve_metadata': True
        }

        try:
            config = self._get_config_service()
            
            user_settings = {
                'codec': config.get('conversion.quality.codec', default_settings['codec']),
                'bitrate': config.get('conversion.quality.bitrate', default_settings['bitrate']),
                'sample_rate': config.get('conversion.quality.sample_rate', default_settings['sample_rate']),
                'channels': config.get('conversion.quality.channels', default_settings['channels']),
                'profile': config.get('conversion.quality.profile', default_settings['profile']),
                'format': config.get('conversion.quality.format', default_settings['format']),
                'preserve_chapters': config.get_config_bool('conversion.quality', 'preserve_chapters', default_settings['preserve_chapters']),
                'embed_cover': config.get_config_bool('conversion.quality', 'embed_cover', default_settings['embed_cover']),
                'preserve_metadata': config.get_config_bool('conversion.quality', 'preserve_metadata', default_settings['preserve_metadata'])
            }
            
            return user_settings
            
        except Exception as e:
            self.logger.exception("Failed to get quality settings", extra={"error": str(e)})
            return default_settings
    
    def convert_audiobook(
        self, 
        input_file: str, 
        output_file: str = None,
        progress_callback: Callable[[str, int], None] = None,
        metadata: Dict[str, Any] = None,
        voucher_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """Convert audiobook file to M4B format"""
        try:
            self.logger.info("Conversion started", extra={'input_file': input_file})
            
            if not os.path.exists(input_file):
                self.logger.warning("Input file does not exist", extra={'input_file': input_file})
                return {
                    'success': False,
                    'error': 'Input file does not exist',
                    'input_file': input_file
                }

            voucher_path = os.path.abspath(voucher_file) if voucher_file else None
            if voucher_path and not os.path.exists(voucher_path):
                self.logger.warning("Voucher file not found", extra={'voucher_file': voucher_path, 'input_file': input_file})
                return {
                    'success': False,
                    'error': f'Voucher file not found: {voucher_path}',
                    'input_file': input_file
                }
            
            if progress_callback:
                progress_callback("Detecting input format...", 5)
            
            input_format = self.helpers['format_detector'].detect_format(input_file)
            self.logger.debug("Detected format", extra={"input_format": input_format})
            
            quality_settings = self.get_quality_settings()
            
            activation_bytes = None
            if input_format.lower() in ['aax', 'aaxc']:
                if progress_callback:
                    progress_callback("Getting activation bytes...", 10)
                
                activation_result = self._get_activation_bytes()
                if activation_result['success']:
                    activation_bytes = activation_result.get('activation_bytes')
                else:
                    self.logger.warning("Activation bytes unavailable", extra={'input_file': input_file, 'reason': activation_result.get('error')})
                    return {
                        'success': False,
                        'error': f'Failed to get activation bytes: {activation_result.get("error")}',
                        'input_file': input_file
                    }
            
            if output_file is None:
                output_file = self._generate_output_filename(input_file)
            
            temp_dir = self._temp_dir_for(output_file)
            temp_output = os.path.join(temp_dir, f"temp_{int(time.time())}_{os.path.basename(output_file)}")
            
            if progress_callback:
                progress_callback("Preparing conversion...", 15)
            
            ffmpeg_cmd = self.helpers['ffmpeg'].build_conversion_command(
                input_file=input_file,
                output_file=temp_output,
                quality_settings=quality_settings,
                activation_bytes=activation_bytes,
                metadata=metadata,
                voucher_file=voucher_path
            )
            
            if progress_callback:
                progress_callback("Converting audiobook...", 20)

            duration_seconds = self.helpers['ffmpeg'].get_audio_duration(input_file) or 0
            total_ms = int(duration_seconds * 1000)

            conversion_result = self._run_ffmpeg_with_progress(
                ffmpeg_cmd,
                output_file=temp_output,
                total_ms=total_ms,
                progress_callback=progress_callback,
                progress_lo=20,
                progress_hi=90,
            )
            
            if not conversion_result['success']:
                self.logger.error("FFmpeg conversion failed", extra={"input_file": input_file, "error": conversion_result.get('error'), "stderr": conversion_result.get('stderr')})
                self._remove_quietly(temp_output)
                return conversion_result
            
            if progress_callback:
                progress_callback("Processing metadata...", 90)
            
            metadata_result = self.helpers['metadata_processor'].process_metadata(
                temp_output,
                quality_settings,
                metadata
            )

            if not metadata_result.get('success', True):
                self.logger.warning("Metadata processing reported issues", extra={'input_file': input_file, 'details': metadata_result})
            
            if progress_callback:
                progress_callback("Finalizing...", 95)
            
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            shutil.move(temp_output, output_file)
            
            if progress_callback:
                progress_callback("Conversion complete!", 100)
            
            self.logger.info("Conversion finished", extra={'input_file': input_file, 'output_file': output_file})
            
            return {
                'success': True,
                'input_file': input_file,
                'output_file': output_file,
                'format': input_format,
                'quality_settings': quality_settings,
                'metadata_processed': metadata_result.get('success', False),
                'file_size': os.path.getsize(output_file)
            }
            
        except Exception as e:
            self.logger.exception("Conversion failed", extra={"input_file": input_file, "error": str(e)})
            
            if 'temp_output' in locals() and os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except OSError:
                    self.logger.warning("Could not remove temporary file %s", temp_output, exc_info=True)
            
            return {
                'success': False,
                'error': str(e),
                'input_file': input_file
            }
    
    def combine_mp3_to_m4b(
        self,
        input_files: List[str],
        output_file: str,
        asin: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        cover_file: Optional[str] = None,
        progress_callback: Callable[[str, int], None] = None,
    ) -> Dict[str, Any]:
        """Merge multiple audio files (typically MP3) into one chaptered M4B.

        Chapters are resolved via the Audible content endpoint when an ASIN is
        supplied, falling back to local sidecar files (.cue/chapters.txt/.nfo)
        and finally one chapter per source file. Global tags and (optional) cover
        art are embedded.
        """
        temp_output = None
        list_file = meta_file = None
        try:
            files = self._natural_sort([f for f in (input_files or []) if f and os.path.exists(f)])
            if not files:
                return {'success': False, 'error': 'No input files found to combine'}

            self.logger.info(
                "Combine started",
                extra={"file_count": len(files), "asin": asin, "output_file": output_file},
            )

            ffmpeg = self.helpers['ffmpeg']

            if not ffmpeg.ffmpeg_available():
                msg = "ffmpeg/ffprobe not found on PATH - install ffmpeg to enable MP3->M4B conversion"
                self.logger.error(msg)
                return {'success': False, 'error': msg, 'reason': 'ffmpeg_unavailable'}

            if progress_callback:
                progress_callback("Probing source files...", 5)

            def probe_ms(path: str) -> int:
                return int((ffmpeg.get_audio_duration(path) or 0) * 1000)

            total_ms = sum(probe_ms(f) for f in files)

            if progress_callback:
                progress_callback("Resolving chapters...", 10)
            chapters, chapter_source = self._get_chapter_provider().get_chapters(
                audio_files=files,
                asin=asin,
                probe_duration_ms=probe_ms,
            )
            self.logger.info(
                "Chapters resolved",
                extra={"source": chapter_source, "count": len(chapters)},
            )

            quality_settings = self.get_quality_settings()
            global_metadata = self._build_global_metadata(metadata)

            temp_dir = self._temp_dir_for(output_file)
            stamp = int(time.time())
            list_file = os.path.join(temp_dir, f"concat_{stamp}.txt")
            meta_file = os.path.join(temp_dir, f"ffmeta_{stamp}.txt")
            temp_output = os.path.join(temp_dir, f"temp_{stamp}_{os.path.basename(output_file)}")

            ffmpeg.write_concat_list(files, list_file)
            ffmpeg.write_chapters_metadata(meta_file, chapters, total_ms, global_metadata)

            if progress_callback:
                progress_callback("Merging audio...", 20)

            cmd = ffmpeg.build_combine_command(
                list_file=list_file,
                metadata_file=meta_file,
                output_file=temp_output,
                quality_settings=quality_settings,
                cover_file=cover_file,
            )

            conversion_result = self._run_ffmpeg_with_progress(
                cmd,
                output_file=temp_output,
                total_ms=total_ms,
                progress_callback=progress_callback,
                progress_lo=20,
                progress_hi=90,
            )

            if not conversion_result.get('success'):
                self.logger.error(
                    "Combine FFmpeg failed",
                    extra={"error": conversion_result.get('error'), "stderr": conversion_result.get('stderr')},
                )
                self._remove_quietly(temp_output)
                temp_output = None
                return conversion_result

            if progress_callback:
                progress_callback("Finalizing...", 95)

            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            shutil.move(temp_output, output_file)
            temp_output = None

            if progress_callback:
                progress_callback("Combine complete!", 100)

            self.logger.success(
                "Combine finished",
                extra={"output_file": output_file, "chapters": len(chapters), "chapter_source": chapter_source},
            )

            return {
                'success': True,
                'output_file': output_file,
                'source_files': files,
                'chapter_count': len(chapters),
                'chapter_source': chapter_source,
                'file_size': os.path.getsize(output_file),
            }

        except Exception as e:
            self.logger.exception("Combine failed", extra={"error": str(e), "output_file": output_file})
            self._remove_quietly(temp_output)
            return {'success': False, 'error': str(e)}
        finally:
            self._remove_quietly(list_file, meta_file)

    def ffmpeg_available(self) -> bool:
        """Return True when the ffmpeg toolchain (ffmpeg + ffprobe) is on PATH."""
        return bool(self.helpers.get('ffmpeg') and self.helpers['ffmpeg'].ffmpeg_available())

    def _run_ffmpeg_with_progress(
        self,
        cmd: List[str],
        output_file: str,
        total_ms: int,
        progress_callback: Callable[[str, int], None] = None,
        progress_lo: int = 20,
        progress_hi: int = 90,
    ) -> Dict[str, Any]:
        """Run an ffmpeg command that emits ``-progress`` key=value on stdout.

        Parses ``out_time_us`` against the known total duration and maps it into
        the [progress_lo, progress_hi] band, emitting a callback on each integer
        increase. This is the reliable progress path for the multi-file merge
        (the shared stderr-based reader emits nothing during the encode).
        """
        try:
            self.logger.debug("Executing ffmpeg (combine)", extra={"command": ' '.join(cmd)})
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1,
            )

            last_pct = progress_lo
            total_seconds = (total_ms / 1000.0) if total_ms else 0.0
            span = max(progress_hi - progress_lo, 1)

            if process.stdout is not None:
                for raw in process.stdout:
                    line = raw.strip()
                    cur_seconds = None
                    if line.startswith('out_time_us=') or line.startswith('out_time_ms='):
                        try:
                            cur_seconds = int(line.split('=', 1)[1]) / 1_000_000.0
                        except ValueError:
                            cur_seconds = None
                    elif line.startswith('out_time='):
                        cur_seconds = self._parse_ffmpeg_timestamp(line.split('=', 1)[1])

                    if cur_seconds is not None and total_seconds > 0:
                        frac = max(0.0, min(cur_seconds / total_seconds, 1.0))
                        pct = int(progress_lo + span * frac)
                        if pct > last_pct and pct <= progress_hi:
                            last_pct = pct
                            if progress_callback:
                                progress_callback(f"Merging audio... {pct}%", pct)
                    elif line == 'progress=end':
                        break

            _stdout, stderr = process.communicate()

            if process.returncode == 0 and os.path.exists(output_file):
                return {'success': True, 'output_file': output_file}

            if process.returncode == 0:
                return {'success': False, 'error': 'ffmpeg completed but output file is missing'}

            self.logger.error(
                "ffmpeg combine returned non-zero exit",
                extra={'return_code': process.returncode, 'stderr': (stderr or '')[-2000:]},
            )
            return {
                'success': False,
                'error': f'ffmpeg merge failed (exit {process.returncode})',
                'stderr': stderr,
            }

        except Exception as e:
            self.logger.exception("ffmpeg combine execution failed", extra={"error": str(e)})
            return {'success': False, 'error': f'ffmpeg merge execution failed: {str(e)}'}

    @staticmethod
    def _parse_ffmpeg_timestamp(value: str) -> Optional[float]:
        """Parse an ffmpeg ``HH:MM:SS.micros`` timestamp into seconds."""
        try:
            parts = value.strip().split(':')
            if len(parts) != 3:
                return None
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except (ValueError, TypeError):
            return None

    def _get_chapter_provider(self):
        """Lazily build the ChapterProvider helper."""
        provider = self.helpers.get('chapter_provider') if self.helpers else None
        if provider is None:
            from .chapter_provider import ChapterProvider
            provider = ChapterProvider()
            if isinstance(self.helpers, dict):
                self.helpers['chapter_provider'] = provider
        return provider

    @staticmethod
    def _natural_sort(paths: List[str]) -> List[str]:
        """Sort file paths so embedded numbers order naturally (2 before 10)."""
        def key(path: str):
            name = os.path.basename(path)
            return [int(tok) if tok.isdigit() else tok.lower()
                    for tok in re.split(r'(\d+)', name)]
        return sorted(paths, key=key)

    @staticmethod
    def _build_global_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Map book metadata (case-tolerant) to FFmpeg global tag keys."""
        if not metadata:
            return {}

        def pick(*keys):
            for key in keys:
                for variant in (key, key.lower(), key.title(), key.upper()):
                    if variant in metadata and metadata[variant] not in (None, '', 'N/A'):
                        return metadata[variant]
            return None

        title = pick('title', 'book_title', 'name')
        author = pick('author', 'authors', 'artist')
        narrator = pick('narrator', 'narrators')
        result = {
            'title': title,
            'album': title,
            'artist': author,
            'album_artist': author,
            'genre': pick('genre'),
            'date': pick('release_date', 'date', 'year'),
            'description': pick('summary', 'description', 'synopsis'),
            'comment': pick('summary', 'description'),
            'composer': narrator,
        }
        return {k: v for k, v in result.items() if v not in (None, '')}

    def _get_activation_bytes(self) -> Dict[str, Any]:
        """Get activation bytes from audible library service"""
        try:
            audible_service = self._get_audible_library_client()
            if audible_service:
                return audible_service.get_activation_bytes()
            else:
                self.logger.warning("Audible library service unavailable for activation bytes")
                return {
                    'success': False,
                    'error': 'Audible library service not available'
                }
        except Exception as e:
            self.logger.exception("Failed to get activation bytes", extra={"error": str(e)})
            return {
                'success': False,
                'error': f'Failed to get activation bytes: {str(e)}'
            }
    
    def _generate_output_filename(self, input_file: str) -> str:
        """Generate output filename based on input file"""
        input_path = Path(input_file)
        output_name = input_path.stem + '.m4b'
        return os.path.join(input_path.parent, output_name)
    
    def get_supported_formats(self) -> List[str]:
        """Get list of supported input formats"""
        return ['aax', 'aaxc', 'mp3', 'm4a', 'm4b', 'flac', 'ogg', 'wav']
    
    def validate_ffmpeg_installation(self) -> Dict[str, Any]:
        """Validate FFmpeg installation and available codecs"""
        try:
            return self.helpers['ffmpeg'].validate_installation()
        except Exception as e:
            return {
                'success': False,
                'error': f'FFmpeg validation failed: {str(e)}'
            }
    
    def _check_activation_bytes_availability(self) -> bool:
        """Check if activation bytes are available"""
        try:
            result = self._get_activation_bytes()
            return result.get('success', False)
        except Exception:
            self.logger.debug("Activation bytes check failed", exc_info=True)
            return False
