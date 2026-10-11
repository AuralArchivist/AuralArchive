"""
Module Name: ffmpeg_handler.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Builds and executes FFmpeg commands for audiobook conversion workflows,
    including voucher handling for AAX/AAXC formats.

Location:
    /services/processing/conversion/ffmpeg_handler.py
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import audible

from core.logging import get_module_logger
from integrations.audible.voucher import decrypt_license_response


class FFmpegHandler:
    """Universal Handler for FFmpeg operations supporting AAX and AAXC formats"""

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.Conversion.FFmpegHandler")
    
    # --- Format detection and key extraction ---
    
    def detect_audio_format(self, input_file: str) -> str:
        """Detect if file is AAX, AAXC, or other format"""
        try:
            ext = Path(input_file).suffix.lower()
            if ext == '.aax':
                return 'aax'
            elif ext == '.aaxc':
                return 'aaxc'
            else:
                return 'other'
        except Exception as e:
            self.logger.exception("Error detecting format", extra={"input_file": input_file, "error": str(e)})
            return 'unknown'
    
    def find_voucher_file(self, aaxc_file: str) -> Optional[str]:
        """Find matching voucher file using multiple discovery strategies"""
        try:
            directory = os.path.dirname(aaxc_file)
            base_name = Path(aaxc_file).stem
            
            patterns = [
                f"{base_name}.voucher",
                f"{base_name}.json", 
                aaxc_file.replace('.aaxc', '.voucher'),
                aaxc_file.replace('.aaxc', '.json')
            ]
            
            for pattern in patterns:
                if os.path.exists(pattern):
                    if self.is_valid_voucher(pattern):
                        self.logger.info("Found voucher file", extra={"voucher_file": pattern, "aaxc_file": aaxc_file})
                        return pattern
            
            if directory:
                for file in os.listdir(directory):
                    if file.endswith(('.json', '.voucher')):
                        voucher_path = os.path.join(directory, file)
                        if self.is_valid_voucher(voucher_path):
                            if self.voucher_matches_file(voucher_path, aaxc_file):
                                self.logger.info("Found matching voucher by content", extra={"voucher_file": voucher_path, "aaxc_file": aaxc_file})
                                return voucher_path

            self.logger.debug("No voucher file discovered automatically", extra={"aaxc_file": aaxc_file})
            return None
            
        except Exception as e:
            self.logger.exception("Error finding voucher file", extra={"aaxc_file": aaxc_file, "error": str(e)})
            return None
    
    def is_valid_voucher(self, file_path: str) -> bool:
        """Check if file contains valid voucher structure"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            if not isinstance(data, dict):
                return False
                
            content_license = data.get('content_license', {})
            license_response = content_license.get('license_response', {})
            
            has_key = 'key' in license_response and license_response['key']
            has_iv = 'iv' in license_response and license_response['iv']
            
            return bool(has_key and has_iv)
            
        except (OSError, ValueError, AttributeError) as e:
            self.logger.debug("File is not a valid voucher", extra={"voucher_file": file_path, "error": str(e)})
            return False
    
    def voucher_matches_file(self, voucher_path: str, aaxc_file: str) -> bool:
        """Check if voucher ASIN matches the audio file"""
        try:
            with open(voucher_path, 'r', encoding='utf-8') as f:
                voucher_data = json.load(f)
            
            voucher_asin = voucher_data.get('content_license', {}).get('asin')
            
            if not voucher_asin:
                return True
            
            filename = os.path.basename(aaxc_file)
            asin_match = re.search(r'[Bb]0[A-Z0-9]{8}', filename)
            
            if asin_match:
                file_asin = asin_match.group()
                return file_asin.upper() == voucher_asin.upper()
            
            return True
            
        except (OSError, ValueError, AttributeError) as e:
            self.logger.debug("Error checking voucher match", extra={"voucher_file": voucher_path, "aaxc_file": aaxc_file, "error": str(e)})
            return True
    
    def extract_voucher_keys(self, voucher_file: str) -> Tuple[str, str]:
        """Extract audible_key and audible_iv from voucher file"""
        try:
            with open(voucher_file, 'r', encoding='utf-8') as f:
                voucher_data = json.load(f)

            if isinstance(voucher_data, str):
                try:
                    voucher_data = json.loads(voucher_data)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Voucher {voucher_file} contains string payload but cannot be decoded: {exc}"
                    ) from exc

            content_license = voucher_data.get('content_license') or voucher_data.get('contentLicense')
            if content_license is None:
                raise ValueError("Voucher missing content_license section")

            license_response = content_license.get('license_response') or content_license.get('licenseResponse')

            if isinstance(license_response, dict) and 'key' in license_response and 'iv' in license_response:
                key = license_response['key']
                iv = license_response['iv']
                if not key or not iv:
                    raise ValueError("Key or IV is empty in voucher file")
                self.logger.debug("Extracted keys from voucher JSON", extra={"voucher_file": voucher_file})
                return key, iv

            if not isinstance(license_response, str):
                raise ValueError("Voucher license_response section is not a string or object with keys")

            asin = content_license.get('asin') or content_license.get('ASIN')
            if not asin:
                raise ValueError("Voucher missing ASIN for decryption")

            decrypted = self._decrypt_voucher_payload(asin, license_response)
            key = decrypted.get('key')
            iv = decrypted.get('iv')

            if not key or not iv:
                raise ValueError("Decrypted voucher did not contain key/iv")

            self.logger.debug("Decrypted keys from voucher", extra={"voucher_file": voucher_file})
            return key, iv

        except KeyError as e:
            raise ValueError(f"Invalid voucher structure - missing {e}")
        except Exception as e:
            raise ValueError(f"Error extracting keys from voucher {voucher_file}: {e}")

    def _decrypt_voucher_payload(self, asin: str, encrypted_voucher: str) -> Dict[str, str]:
        """Decrypt the license_response payload to recover audible_key/iv."""
        try:
            from core.paths import resolve_audible_auth_file
            from core.config.secrets_crypto import get_audible_auth_password
            auth = audible.Authenticator.from_file(
                resolve_audible_auth_file(), password=get_audible_auth_password()
            )
        except Exception as exc:
            raise ValueError(f"Failed to load Audible auth for voucher decryption: {exc}")

        return decrypt_license_response(auth, asin, encrypted_voucher)

    def determine_conversion_method(self, input_file: str) -> Tuple[str, Optional[str]]:
        """Determine which decryption method to use and return (method, voucher_file)"""
        format_type = self.detect_audio_format(input_file)
        
        if format_type == 'aax':
            self.logger.info("AAX file detected - using activation bytes", extra={"input_file": input_file})
            return 'activation_bytes', None
        
        elif format_type == 'aaxc':
            voucher_file = self.find_voucher_file(input_file)
            if voucher_file:
                self.logger.debug("AAXC voucher detected during discovery", extra={"input_file": input_file, "voucher_file": voucher_file})
                return 'voucher_keys', voucher_file
            else:
                self.logger.debug("AAXC voucher not discovered; will try activation bytes fallback if provided", extra={"input_file": input_file})
                return 'activation_bytes_fallback', None
        
        elif format_type == 'other':
            self.logger.info("Non-DRM audio file detected - no decryption needed", extra={"input_file": input_file})
            return 'no_drm', None
        
        else:
            self.logger.error("Unknown audio format", extra={"input_file": input_file})
            return 'unknown', None
    
    def validate_installation(self) -> Dict[str, Any]:
        """Validate FFmpeg installation and available codecs"""
        try:
            ffmpeg_path = shutil.which('ffmpeg')
            ffprobe_path = shutil.which('ffprobe')

            missing_binaries = []
            if not ffmpeg_path:
                missing_binaries.append('ffmpeg')
            if not ffprobe_path:
                missing_binaries.append('ffprobe')

            if missing_binaries:
                missing_text = ', '.join(missing_binaries)
                return {
                    'success': False,
                    'error': f'Missing required binaries in PATH: {missing_text}',
                    'ffmpeg_available': bool(ffmpeg_path),
                    'ffprobe_available': bool(ffprobe_path),
                }

            result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return {
                    'success': False,
                    'error': 'FFmpeg not found or not working',
                    'ffmpeg_available': False
                }

            ffprobe_result = subprocess.run(['ffprobe', '-version'], capture_output=True, text=True, timeout=10)
            if ffprobe_result.returncode != 0:
                return {
                    'success': False,
                    'error': 'FFprobe not found or not working',
                    'ffmpeg_available': True,
                    'ffprobe_available': False,
                }
            
            version_match = re.search(r'ffmpeg version ([^\s]+)', result.stdout)
            version = version_match.group(1) if version_match else 'unknown'
            
            codecs_result = subprocess.run(['ffmpeg', '-codecs'], capture_output=True, text=True, timeout=10)
            available_codecs = self._parse_available_codecs(codecs_result.stdout)
            
            has_libfdk_aac = 'libfdk_aac' in available_codecs
            has_aac = 'aac' in available_codecs
            
            return {
                'success': True,
                'ffmpeg_available': True,
                'ffprobe_available': True,
                'ffmpeg_path': ffmpeg_path,
                'ffprobe_path': ffprobe_path,
                'version': version,
                'available_codecs': available_codecs,
                'has_libfdk_aac': has_libfdk_aac,
                'has_aac': has_aac,
                'recommended_codec': 'libfdk_aac' if has_libfdk_aac else 'aac',
                'quality_note': 'Optimal quality available' if has_libfdk_aac else 'Good quality available'
            }
            
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'FFmpeg validation timed out',
                'ffmpeg_available': False
            }
        except FileNotFoundError:
            return {
                'success': False,
                'error': 'FFmpeg/FFprobe not found in PATH',
                'ffmpeg_available': False,
                'ffprobe_available': False,
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'FFmpeg validation failed: {str(e)}',
                'ffmpeg_available': False,
                'ffprobe_available': False,
            }
    
    def _parse_available_codecs(self, codecs_output: str) -> List[str]:
        """Parse FFmpeg codecs output to get available audio codecs"""
        codecs = []
        for line in codecs_output.split('\n'):
            if 'A' in line[:6]:
                match = re.search(r'\s+(\w+)\s+', line[6:])
                if match:
                    codecs.append(match.group(1))
        return codecs
    
    # --- Universal command building ---
    
    def build_conversion_command(
        self,
        input_file: str,
        output_file: str,
        quality_settings: Dict[str, Any],
        activation_bytes: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        voucher_file: Optional[str] = None
    ) -> List[str]:
        """Build FFmpeg command with universal AAX/AAXC support
        
        Automatically detects format and uses appropriate decryption method:
        - AAX files: activation_bytes
        - AAXC files: voucher keys (audible_key/audible_iv)
        - Non-DRM: direct conversion
        """
        
        method, detected_voucher = self.determine_conversion_method(input_file)

        voucher_to_use = voucher_file or detected_voucher
        if voucher_file and method != 'voucher_keys':
            if voucher_to_use and os.path.exists(voucher_to_use):
                self.logger.info(
                    "Explicit voucher supplied; forcing voucher-based conversion",
                    extra={"input_file": input_file, "voucher_file": voucher_to_use}
                )
                method = 'voucher_keys'
            else:
                self.logger.warning(
                    "Voucher path provided but not found; keeping detected method",
                    extra={"input_file": input_file, "voucher_file": voucher_file, "method": method}
                )
        
        try:
            return self._build_command_for_method(
                input_file, output_file, quality_settings, 
                method, activation_bytes, voucher_to_use, metadata
            )
        except Exception as e:
            self.logger.exception("Failed to build conversion command", extra={"input_file": input_file, "output_file": output_file, "error": str(e)})
            raise
    
    def _build_command_for_method(
        self,
        input_file: str,
        output_file: str,
        quality_settings: Dict[str, Any],
        method: str,
        activation_bytes: Optional[str] = None,
        voucher_file: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Build FFmpeg command for specific conversion method"""
        
        cmd = ['ffmpeg', '-y']
        
        if method == 'activation_bytes':
            if not activation_bytes:
                raise ValueError("Activation bytes required for AAX conversion but not provided")
            cmd.extend(['-activation_bytes', activation_bytes])
            self.logger.debug("Using activation bytes for AAX decryption", extra={"input_file": input_file})
            
        elif method == 'voucher_keys':
            if not voucher_file:
                raise ValueError("Voucher file required for AAXC conversion but not found")
            key, iv = self.extract_voucher_keys(voucher_file)
            cmd.extend(['-audible_key', key, '-audible_iv', iv])
            self.logger.debug("Using voucher keys for AAXC decryption", extra={"input_file": input_file, "voucher_file": voucher_file})
            
        elif method == 'activation_bytes_fallback':
            if not activation_bytes:
                raise ValueError("Activation bytes required for AAXC fallback but not provided")
            cmd.extend(['-activation_bytes', activation_bytes])
            self.logger.warning("Using activation bytes fallback for AAXC (voucher missing)", extra={"input_file": input_file})
            
        elif method == 'no_drm':
            self.logger.debug("No DRM decryption needed", extra={"input_file": input_file})
            
        else:
            raise ValueError(f"Unknown conversion method: {method}")
        
        cmd.extend(['-i', input_file])
        
        output_ext = Path(output_file).suffix.lower()
        
        if output_ext in ['.m4b', '.m4a']:
            cmd.extend(['-c', 'copy'])
            cmd.extend(['-map_metadata', '0'])
            cmd.extend(['-map_chapters', '0'])
            self.logger.debug("Using stream copy for M4B/M4A output (fast conversion)", extra={"output_file": output_file})
            
        else:
            codec = quality_settings.get('codec', 'aac')
            cmd.extend(['-c:a', codec])
            
            bitrate = quality_settings.get('bitrate', '128k')
            cmd.extend(['-b:a', bitrate])
            
            sample_rate = self._sample_rate_for(codec, quality_settings.get('sample_rate', '44100'))
            if sample_rate:
                cmd.extend(['-ar', str(sample_rate)])
            
            channels = quality_settings.get('channels', '2')
            if channels:
                cmd.extend(['-ac', str(channels)])
            
            if quality_settings.get('preserve_metadata', True):
                cmd.extend(['-map_metadata', '0'])
            
            if quality_settings.get('preserve_chapters', True):
                cmd.extend(['-map_chapters', '0'])
                
            self.logger.debug("Using re-encoding", extra={"codec": codec, "bitrate": bitrate, "output_file": output_file})
        
        if metadata:
            for key, value in metadata.items():
                if value and key in self._get_ffmpeg_metadata_mapping():
                    ffmpeg_key = self._get_ffmpeg_metadata_mapping()[key]
                    escaped_value = str(value).replace('"', '\\"')
                    cmd.extend(['-metadata', f'{ffmpeg_key}={escaped_value}'])
        
        if output_ext == '.m4b':
            cmd.extend(['-f', 'mp4'])
        
        cmd.extend(['-nostats', '-progress', 'pipe:1'])

        cmd.append(output_file)

        return cmd
    
    SECRET_ARGS = {'-activation_bytes', '-audible_key', '-audible_iv'}

    def _mask_secrets(self, cmd: List[str]) -> List[str]:
        """Copy of an ffmpeg command with decryption keys replaced, for logging."""
        return ['***' if i and cmd[i - 1] in self.SECRET_ARGS else arg for i, arg in enumerate(cmd)]

    OPUS_SAMPLE_RATES = {'8000', '12000', '16000', '24000', '48000'}

    def _sample_rate_for(self, codec: str, sample_rate: Any) -> Optional[str]:
        """Sample rate to pass with -ar, or None to let ffmpeg pick one.

        Opus only supports 8/12/16/24/48 kHz; any other rate is dropped so
        ffmpeg resamples to 48 kHz instead of failing.
        """
        if not sample_rate:
            return None
        if 'opus' in str(codec).lower() and str(sample_rate) not in self.OPUS_SAMPLE_RATES:
            return None
        return str(sample_rate)

    def _get_ffmpeg_metadata_mapping(self) -> Dict[str, str]:
        """Get mapping of standard metadata keys to FFmpeg metadata keys"""
        return {
            'title': 'title',
            'artist': 'artist',
            'album': 'album',
            'albumartist': 'album_artist',
            'date': 'date',
            'year': 'year',
            'genre': 'genre',
            'comment': 'comment',
            'description': 'description',
            'synopsis': 'synopsis'
        }
    
    # --- Multi-file combine (MP3 -> single chaptered M4B) ---

    def ffmpeg_available(self) -> bool:
        """Return True only when both ffmpeg and ffprobe are on PATH.

        The bundled Docker image ships both binaries; bare-metal installs may
        not. Callers use this to fail fast / degrade gracefully instead of
        raising a confusing FileNotFoundError mid-conversion.
        """
        return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))

    def write_concat_list(self, audio_files: List[str], list_path: str) -> str:
        """Write an FFmpeg concat-demuxer list file for the given audio files.

        Paths are absolute and single-quote escaped per the concat demuxer rules
        (a literal single quote becomes ``'\\''``). Use with ``-f concat -safe 0``.
        """
        with open(list_path, 'w', encoding='utf-8') as fh:
            for path in audio_files:
                abs_path = os.path.abspath(path)
                escaped = abs_path.replace("'", "'\\''")
                fh.write(f"file '{escaped}'\n")
        return list_path

    @staticmethod
    def _escape_ffmetadata(value: str) -> str:
        """Escape a value for an FFMETADATA1 file (=, ;, #, \\ and newlines)."""
        text = str(value)
        for ch in ('\\', '=', ';', '#'):
            text = text.replace(ch, '\\' + ch)
        return text.replace('\n', '\\\n')

    def write_chapters_metadata(
        self,
        meta_path: str,
        chapters: List[Dict[str, Any]],
        total_duration_ms: int,
        global_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Write an FFMETADATA1 file with global tags and [CHAPTER] blocks."""
        lines = [';FFMETADATA1']

        for key, value in (global_metadata or {}).items():
            if value is None or value == '':
                continue
            lines.append(f"{key}={self._escape_ffmetadata(value)}")

        ordered = sorted(chapters or [], key=lambda c: int(c.get('start_ms', 0)))
        for idx, chapter in enumerate(ordered):
            start = max(int(chapter.get('start_ms', 0)), 0)
            if idx + 1 < len(ordered):
                end = max(int(ordered[idx + 1].get('start_ms', 0)), start + 1)
            else:
                end = max(int(total_duration_ms or 0), start + 1)
            title = chapter.get('title') or f'Chapter {idx + 1}'
            lines.append('[CHAPTER]')
            lines.append('TIMEBASE=1/1000')
            lines.append(f'START={start}')
            lines.append(f'END={end}')
            lines.append(f'title={self._escape_ffmetadata(title)}')

        with open(meta_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
        return meta_path

    def build_combine_command(
        self,
        list_file: str,
        metadata_file: str,
        output_file: str,
        quality_settings: Dict[str, Any],
        cover_file: Optional[str] = None,
    ) -> List[str]:
        """Build the FFmpeg command to merge a concat list into a chaptered M4B.

        Inputs: 0 = concat list (audio), 1 = FFMETADATA file, 2 = optional cover.
        Audio is re-encoded (MP3 cannot be stream-copied into an MP4/M4B
        container); chapters and global tags come from the metadata input.
        """
        codec = quality_settings.get('codec', 'aac')
        bitrate = quality_settings.get('bitrate', '64k')
        sample_rate = str(quality_settings.get('sample_rate', '') or '')
        channels = str(quality_settings.get('channels', '') or '')

        cmd: List[str] = ['ffmpeg', '-y']
        cmd.extend(['-f', 'concat', '-safe', '0', '-i', list_file])
        cmd.extend(['-i', metadata_file])

        has_cover = bool(cover_file and os.path.exists(cover_file))
        if has_cover:
            cmd.extend(['-i', cover_file])

        cmd.extend(['-map', '0:a'])
        if has_cover:
            cmd.extend(['-map', '2:v'])
        cmd.extend(['-map_metadata', '1', '-map_chapters', '1'])

        if str(codec).lower() == 'copy':
            cmd.extend(['-c:a', 'copy'])
        else:
            cmd.extend(['-c:a', codec, '-b:a', str(bitrate)])
            sample_rate = self._sample_rate_for(codec, sample_rate)
            if sample_rate:
                cmd.extend(['-ar', str(sample_rate)])
            if channels:
                cmd.extend(['-ac', channels])

        if has_cover:
            cmd.extend(['-c:v', 'copy', '-disposition:v:0', 'attached_pic'])

        cmd.extend(['-f', 'mp4'])
        cmd.extend(['-nostats', '-progress', 'pipe:1'])
        cmd.append(output_file)
        return cmd

    def get_audio_duration(self, input_file: str) -> float:
        """Get duration of audio file in seconds"""
        try:
            cmd = [
                'ffprobe', 
                '-v', 'quiet',
                '-show_entries', 'format=duration',
                '-of', 'csv=p=0',
                input_file
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                duration_str = result.stdout.strip()
                return float(duration_str) if duration_str else 0.0
            else:
                self.logger.warning("Could not get duration", extra={"input_file": input_file, "stderr": result.stderr})
                return 0.0
                
        except Exception as e:
            self.logger.exception("Error getting duration", extra={"input_file": input_file, "error": str(e)})
            return 0.0
    
    def extract_metadata(self, input_file: str) -> Dict[str, Any]:
        """Extract metadata from input file using FFprobe"""
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                input_file
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                import json
                probe_data = json.loads(result.stdout)
                
                format_tags = probe_data.get('format', {}).get('tags', {})
                
                normalized_tags = {}
                for key, value in format_tags.items():
                    normalized_key = key.lower().replace('-', '_')
                    normalized_tags[normalized_key] = value
                
                return {
                    'duration': float(probe_data.get('format', {}).get('duration', 0)),
                    'bitrate': int(probe_data.get('format', {}).get('bit_rate', 0)),
                    'tags': normalized_tags,
                    'streams': probe_data.get('streams', [])
                }
            else:
                self.logger.warning("Could not extract metadata", extra={"input_file": input_file, "stderr": result.stderr})
                return {}
                
        except Exception as e:
            self.logger.exception("Error extracting metadata", extra={"input_file": input_file, "error": str(e)})
            return {}
    