"""
Module Name: asin_tag_embedder.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Embeds ASIN metadata into supported audio containers so downstream
    scanners can associate imported files with Audible catalog records.
    Supports ID3 (MP3) and MP4/M4B atoms via mutagen when available.

Location:
    /services/processing/filing/asin_tag_embedder.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.logging import get_module_logger

try:
    from mutagen.id3 import ID3, TXXX, ID3NoHeaderError
    from mutagen.mp4 import MP4, AtomDataType, MP4FreeForm
except ImportError:
    ID3 = None
    TXXX = None
    ID3NoHeaderError = None
    MP4 = None
    MP4FreeForm = None
    AtomDataType = None


_LOGGER = get_module_logger("Service.Import.AsinTagEmbedder")


class AsinTagEmbedder:
    """Embed Audible ASIN metadata inside supported audio containers."""

    MP3_EXTENSIONS = {'.mp3'}
    MP4_EXTENSIONS = {'.m4b', '.m4a', '.mp4'}

    def __init__(self, *, logger=None) -> None:
        self.logger = logger or _LOGGER

    def embed_asin(self, file_path: str, asin: Optional[str]) -> bool:
        """Write ASIN tags to ``file_path`` if the format supports it."""
        if not asin:
            return False

        extension = Path(file_path).suffix.lower()

        if extension in self.MP3_EXTENSIONS:
            return self._embed_id3_asin(file_path, asin)
        if extension in self.MP4_EXTENSIONS:
            return self._embed_mp4_asin(file_path, asin)

        self.logger.debug("Skipping ASIN embedding for %s (unsupported extension)", file_path)
        return False


    def _embed_id3_asin(self, file_path: str, asin: str) -> bool:
        if ID3 is None or TXXX is None:
            self.logger.debug("mutagen.id3 not available; cannot embed ASIN")
            return False

        try:
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()

            tags.delall('TXXX:ASIN')
            tags.delall('TXXX:audible_asin')
            tags.add(TXXX(encoding=3, desc='ASIN', text=[asin]))
            tags.add(TXXX(encoding=3, desc='audible_asin', text=[asin]))
            tags.save(file_path, v2_version=3)
            self.logger.debug("Embedded ASIN %s via ID3 into %s", asin, file_path)
            return True
        except Exception as exc:
            self.logger.warning("Unable to embed ASIN tag via ID3 for %s: %s", file_path, exc, exc_info=True)
            return False

    def _embed_mp4_asin(self, file_path: str, asin: str) -> bool:
        if MP4 is None:
            self.logger.debug("mutagen.mp4 not available; cannot embed ASIN")
            return False

        try:
            tags = MP4(file_path)
            value = MP4FreeForm(asin.encode('utf-8'), dataformat=AtomDataType.UTF8)
            tags['----:com.apple.iTunes:ASIN'] = [value]
            tags['----:com.apple.iTunes:audible_asin'] = [value]
            tags.save()
            self.logger.debug("Embedded ASIN %s via MP4 atoms into %s", asin, file_path)
            return True
        except Exception as exc:
            self.logger.warning("Unable to embed ASIN tag via MP4 atoms for %s: %s", file_path, exc, exc_info=True)
            return False


__all__ = ['AsinTagEmbedder']
