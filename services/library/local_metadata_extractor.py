"""
Module Name: local_metadata_extractor.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Reads local audio files and extracts metadata hints from tags and path
    structure to aid import matching. Supports mutagen-backed ID3/MP4 tags
    when available.

Location:
    /services/library/local_metadata_extractor.py
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from core.logging import get_module_logger

try:
    import mutagen
except ImportError:
    mutagen = None


_LOGGER = get_module_logger("Service.Import.MetadataExtractor")


class LocalMetadataExtractor:
    """Reads local audio files to extract helpful metadata hints."""

    ASIN_TAG_HINTS = (
        'asin',
        'audible_asin',
        'audiblebook',
        'audible_book_id',
        'audibleid',
        'audiblebookid',
        'audible:asin',
        'audible:item_id',
        'audibleitemid',
        'audible:itemid',
        'audible_product_id'
    )

    SERIES_TAG_HINTS = (
        'series',
        'series_name',
        'series_title',
        'audible_series',
        'audible:series',
        'mvnm'
    )

    NARRATOR_TAG_HINTS = (
        'narrator',
        'performer',
        'read_by',
        'reader',
        'spoken_by',
        'composer'
    )

    SUBTITLE_TAG_HINTS = (
        'subtitle',
        'sub_title',
        '----:com.apple.itunes:subtitle',
        '----:com.apple.iTunes:SUBTITLE'
    )

    DESCRIPTION_TAG_HINTS = (
        'description',
        '----:com.apple.itunes:description',
        '----:com.apple.iTunes:DESCRIPTION'
    )

    LANGUAGE_TAG_HINTS = (
        'language',
        'lang',
        '----:com.apple.itunes:language',
        '----:com.apple.iTunes:LANGUAGE'
    )

    ISBN_TAG_HINTS = (
        'isbn',
        'isbn10',
        'isbn13'
    )

    def __init__(self, *, logger=None) -> None:
        self.logger = logger or _LOGGER

    def extract_metadata(self, file_path: str) -> Dict[str, Any]:
        """Return best-effort metadata extracted from ID3/MP4 tags and the path."""
        info: Dict[str, Any] = {
            'source_path': file_path,
            'exists': os.path.exists(file_path),
            'asin': None,
            'title': None,
            'subtitle': None,
            'author': None,
            'album': None,
            'album_artist': None,
            'narrator': None,
            'series': None,
            'sequence': None,
            'publisher': None,
            'year': None,
            'genre': None,
            'language': None,
            'isbn': None,
            'description': None,
            'duration_seconds': None,
            'clean_title': None,
            'filename': None,
            'extension': None,
            'directory': None,
            'path_tokens': [],
            'warnings': []
        }

        try:
            path_obj = Path(file_path)
            info['filename'] = path_obj.name
            info['extension'] = path_obj.suffix.lower().lstrip('.')
            info['directory'] = str(path_obj.parent)
            info['path_tokens'] = [token for token in path_obj.parts if token not in ('.', '..')]
        except Exception as exc:
            info['warnings'].append(f'Path parsing failed: {exc}')

        if not info['exists']:
            info['warnings'].append('File is not accessible from server filesystem')
            return info

        info['clean_title'] = self._clean_title_from_filename(info['filename'] or '')

        if not mutagen:
            info['warnings'].append('mutagen not available, skipping tag extraction')
            return info

        easy_tags = self._load_mutagen_tags(file_path, easy=True)
        rich_tags = self._load_mutagen_tags(file_path, easy=False)

        if easy_tags:
            info['title'] = self._first(easy_tags.get('title'))
            info['author'] = self._first(
                easy_tags.get('author')
                or easy_tags.get('artist')
                or easy_tags.get('albumartist')
            )
            info['album'] = self._first(easy_tags.get('album'))
            info['album_artist'] = self._first(easy_tags.get('albumartist'))
            info['genre'] = self._first(easy_tags.get('genre'))
            info['publisher'] = self._first(easy_tags.get('publisher'))
            info['year'] = self._first(easy_tags.get('date') or easy_tags.get('originaldate'))
            info['subtitle'] = self._first(easy_tags.get('subtitle'))
            info['language'] = self._first(easy_tags.get('language'))
            info['isbn'] = self._first(easy_tags.get('isbn'))
            info['description'] = self._first(easy_tags.get('description'))

        if not info['title']:
            info['title'] = info['album']
        if not info['author']:
            info['author'] = info['album_artist']

        duration = self._extract_duration_seconds(rich_tags)
        if duration:
            info['duration_seconds'] = duration

        flattened_tags = self._flatten_tags(rich_tags)

        if not info['asin']:
            info['asin'] = self._extract_asin(flattened_tags)
        if not info['asin'] and info['filename']:
            asin_match = re.search(r'\[([A-Za-z0-9]{10})\]', info['filename'])
            if asin_match:
                info['asin'] = asin_match.group(1).upper()

        info['narrator'] = info['narrator'] or self._scan_for_keys(flattened_tags, self.NARRATOR_TAG_HINTS)
        info['series'] = info['series'] or self._scan_for_keys(flattened_tags, self.SERIES_TAG_HINTS)
        info['sequence'] = info['sequence'] or self._extract_sequence(flattened_tags)
        info['subtitle'] = info['subtitle'] or self._scan_for_keys(flattened_tags, self.SUBTITLE_TAG_HINTS)
        info['description'] = info['description'] or self._scan_for_keys(flattened_tags, self.DESCRIPTION_TAG_HINTS)
        info['language'] = info['language'] or self._scan_for_keys(flattened_tags, self.LANGUAGE_TAG_HINTS)
        info['isbn'] = info['isbn'] or self._scan_for_keys(flattened_tags, self.ISBN_TAG_HINTS)

        if not info['author']:
            info['author'] = self._scan_for_keys(flattened_tags, ('author', 'authors', 'writer', 'written_by'))

        if not info['narrator']:
            info['narrator'] = self._scan_for_keys(flattened_tags, ('narrator', 'performed_by', 'read_by', 'reader'))

        return info

    def _load_mutagen_tags(self, file_path: str, easy: bool) -> Optional[Any]:
        try:
            return mutagen.File(file_path, easy=easy)
        except Exception as exc:
            self.logger.debug("Unable to load %s tags for %s: %s", 'easy' if easy else 'rich', file_path, exc)
            return None

    def _extract_duration_seconds(self, rich_tags: Optional[Any]) -> Optional[float]:
        try:
            if rich_tags and getattr(rich_tags, 'info', None):
                length = getattr(rich_tags.info, 'length', None)
                if length and length > 0:
                    return float(length)
        except Exception as exc:
            self.logger.debug("Unable to read duration: %s", exc)
        return None

    def _flatten_tags(self, rich_tags: Optional[Any]) -> Dict[str, str]:
        flattened: Dict[str, str] = {}
        if not rich_tags or not getattr(rich_tags, 'tags', None):
            return flattened

        try:
            for key, value in rich_tags.tags.items():
                normalized_key = str(key).lower()
                text_value = self._serialize_tag_value(value)
                if text_value:
                    flattened[normalized_key] = text_value
        except Exception as exc:
            self.logger.debug("Failed to flatten tags: %s", exc)
        return flattened

    def _serialize_tag_value(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
            if value is None:
                return None
        if isinstance(value, (bytes, bytearray)):
            return value.decode('utf-8', errors='replace').strip('\x00').strip()
        return str(value)

    def _first(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else None
        return str(value)

    def _clean_title_from_filename(self, filename: str) -> Optional[str]:
        if not filename:
            return None
        title = os.path.splitext(filename)[0]
        title = re.sub(r'\[[^\]]*\]', ' ', title)
        title = re.sub(r'\([^)]*\)', ' ', title)
        title = title.replace('_', ' ')
        title = re.sub(r'\s+', ' ', title)
        return title.strip() or None

    def _extract_asin(self, flattened_tags: Dict[str, str]) -> Optional[str]:
        for key, value in flattened_tags.items():
            if any(hint in key for hint in self.ASIN_TAG_HINTS):
                asin_match = re.search(r'\b(B[0-9A-Z]{9}|\d{9}[\dX])\b', value.upper())
                if asin_match:
                    return asin_match.group(1)
        comment_text = flattened_tags.get('comment') or flattened_tags.get('comments')
        if comment_text:
            asin_match = re.search(r'(B[0-9A-Z]{9})', comment_text.upper())
            if asin_match:
                return asin_match.group(1)
        return None

    def _scan_for_keys(self, flattened_tags: Dict[str, str], keys: Any) -> Optional[str]:
        for key, value in flattened_tags.items():
            if any(hint in key for hint in keys):
                return value.strip()
        return None

    def _extract_sequence(self, flattened_tags: Dict[str, str]) -> Optional[str]:
        sequence_sources = (
            flattened_tags.get('series_index') or
            flattened_tags.get('seriesindex') or
            flattened_tags.get('series-part') or
            flattened_tags.get('part_number') or
            flattened_tags.get('mvin') or
            flattened_tags.get('discnumber') or
            flattened_tags.get('tracknumber')
        )
        if not sequence_sources:
            return None
        match = re.search(r'(\d+(?:[\.\-]\d+)?)', sequence_sources)
        return match.group(1) if match else None


__all__ = ['LocalMetadataExtractor']
