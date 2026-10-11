"""
Module Name: chapter_provider.py
Author: AuralArchivist
Created: June 16, 2026
Last Modified: October 9, 2026
Description:
    Resolves chapter markers for a multi-file audiobook that is about to be
    merged into a single M4B. Tries the authoritative Audible content endpoint
    first, then local sidecar files (.cue / chapters.txt / .nfo), and finally
    falls back to one chapter per source file derived from probed durations.

Location:
    /services/processing/conversion/chapter_provider.py

Notes:
    - Chapter timings are absolute positions on the *combined* timeline (the
      merged file is expected to equal the full audiobook), expressed in ms.
    - A chapter is a dict: {'start_ms': int, 'title': str}. End offsets are
      computed by the consumer from the next chapter / total duration.
"""

import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging import get_module_logger


class ChapterProvider:
    """Resolve chapter markers from Audible, local sidecar files, or per-file."""

    _CUE_EXTS = ('.cue',)
    _TEXT_CHAPTER_NAMES = ('chapters.txt', 'chapter.txt')

    _TS_LINE = re.compile(
        r'^\s*\[?\(?\s*'
        r'(?:(?P<h>\d{1,2}):)?(?P<m>\d{1,2}):(?P<s>\d{2})(?:[.,](?P<ms>\d{1,3}))?'
        r'\s*[\]\)]?\s*[-–:.\s]\s*(?P<title>.+?)\s*$'
    )

    def __init__(self, *, logger=None, audible_api_helper=None):
        self.logger = logger or get_module_logger("Service.Conversion.ChapterProvider")
        self._audible_api_helper = audible_api_helper

    # --- Public API ---

    def get_chapters(
        self,
        audio_files: List[str],
        asin: Optional[str] = None,
        probe_duration_ms: Optional[Callable[[str], int]] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Return ``(chapters, source)`` for the given ordered audio files.

        Returns:
            A tuple of (list of {'start_ms', 'title'} sorted by start, source
            label). Returns ``([], 'none')`` only if everything fails.
        """
        if asin:
            try:
                chapters = self._from_audible(asin)
                if chapters:
                    self.logger.info(
                        "Using Audible chapters",
                        extra={"asin": asin, "count": len(chapters)},
                    )
                    return chapters, 'audible'
            except Exception as exc:
                self.logger.warning(
                    "Audible chapter lookup failed; falling back to local sources",
                    extra={"asin": asin, "error": str(exc)},
                )

        try:
            chapters = self._from_local_files(audio_files)
            if chapters:
                self.logger.info("Using local sidecar chapters", extra={"count": len(chapters)})
                return chapters, 'local'
        except Exception as exc:
            self.logger.warning("Local chapter parse failed", extra={"error": str(exc)}, exc_info=True)

        if probe_duration_ms is not None and len(audio_files) > 0:
            try:
                chapters = self._per_file(audio_files, probe_duration_ms)
                if chapters:
                    self.logger.info("Using per-file chapters", extra={"count": len(chapters)})
                    return chapters, 'per_file'
            except Exception as exc:
                self.logger.warning("Per-file chapter derivation failed", extra={"error": str(exc)}, exc_info=True)

        self.logger.warning("No chapters could be resolved")
        return [], 'none'

    # --- Strategy 1 - Audible ---

    def _get_audible_api_helper(self):
        if self._audible_api_helper is None:
            from integrations.audible.metadata_sync.audible_api_helper import AudibleApiHelper
            self._audible_api_helper = AudibleApiHelper()
        return self._audible_api_helper

    def _from_audible(self, asin: str) -> List[Dict[str, Any]]:
        helper = self._get_audible_api_helper()
        if not helper or not helper.is_available():
            self.logger.debug("Audible API helper unavailable for chapter lookup")
            return []

        chapter_info = helper.get_chapter_info(asin) or {}
        raw_chapters = chapter_info.get('chapters') or []

        chapters: List[Dict[str, Any]] = []
        for idx, ch in enumerate(raw_chapters):
            start = ch.get('start_offset_ms')
            if start is None:
                start_sec = ch.get('start_offset_sec')
                start = int(float(start_sec) * 1000) if start_sec is not None else None
            if start is None:
                continue
            title = (ch.get('title') or f'Chapter {idx + 1}').strip()
            chapters.append({'start_ms': int(start), 'title': title})

        return self._normalize(chapters)

    # --- Strategy 2 - local sidecar files ---

    def _from_local_files(self, audio_files: List[str]) -> List[Dict[str, Any]]:
        search_dirs = {os.path.dirname(os.path.abspath(f)) for f in audio_files if f}
        for directory in search_dirs:
            if not directory or not os.path.isdir(directory):
                continue
            try:
                entries = sorted(os.listdir(directory))
            except OSError:
                continue

            for name in entries:
                if name.lower().endswith(self._CUE_EXTS):
                    chapters = self._parse_cue(os.path.join(directory, name))
                    if chapters:
                        return chapters
            for name in entries:
                if name.lower() in self._TEXT_CHAPTER_NAMES:
                    chapters = self._parse_timestamp_lines(os.path.join(directory, name))
                    if chapters:
                        return chapters
            for name in entries:
                if name.lower().endswith('.nfo'):
                    chapters = self._parse_timestamp_lines(os.path.join(directory, name))
                    if chapters:
                        return chapters
        return []

    def _parse_cue(self, path: str) -> List[Dict[str, Any]]:
        """Parse a .cue sheet into absolute chapter markers."""
        chapters: List[Dict[str, Any]] = []
        current_title: Optional[str] = None
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                lines = fh.readlines()
        except OSError:
            return []

        for line in lines:
            stripped = line.strip()
            title_match = re.match(r'TITLE\s+"?(.+?)"?$', stripped, re.IGNORECASE)
            if title_match:
                current_title = title_match.group(1).strip()
                continue
            index_match = re.match(r'INDEX\s+01\s+(\d+):(\d{2}):(\d{2})', stripped, re.IGNORECASE)
            if index_match:
                minutes, seconds, frames = (int(g) for g in index_match.groups())
                start_ms = (minutes * 60 + seconds) * 1000 + int(frames * 1000 / 75)
                chapters.append({
                    'start_ms': start_ms,
                    'title': current_title or f'Chapter {len(chapters) + 1}',
                })
                current_title = None

        return self._normalize(chapters)

    def _parse_timestamp_lines(self, path: str) -> List[Dict[str, Any]]:
        """Parse 'HH:MM:SS Title' style chapter lines from a text/nfo file."""
        chapters: List[Dict[str, Any]] = []
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                lines = fh.readlines()
        except OSError:
            return []

        for line in lines:
            match = self._TS_LINE.match(line)
            if not match:
                continue
            hours = int(match.group('h') or 0)
            minutes = int(match.group('m'))
            seconds = int(match.group('s'))
            millis_raw = match.group('ms') or '0'
            millis = int(millis_raw.ljust(3, '0')[:3])
            start_ms = ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis
            title = match.group('title').strip().strip('"')
            if title:
                chapters.append({'start_ms': start_ms, 'title': title})

        if len(chapters) < 2:
            return []
        return self._normalize(chapters)

    # --- Strategy 3 - per file ---

    def _per_file(
        self,
        audio_files: List[str],
        probe_duration_ms: Callable[[str], int],
    ) -> List[Dict[str, Any]]:
        chapters: List[Dict[str, Any]] = []
        cursor_ms = 0
        for idx, path in enumerate(audio_files):
            chapters.append({
                'start_ms': cursor_ms,
                'title': self._title_from_filename(path, idx),
            })
            cursor_ms += max(int(probe_duration_ms(path) or 0), 0)
        return self._normalize(chapters)

    @staticmethod
    def _title_from_filename(path: str, idx: int) -> str:
        stem = os.path.splitext(os.path.basename(path))[0]
        cleaned = re.sub(r'^\s*\d{1,3}\s*[-_.)\]]\s*', '', stem).strip()
        return cleaned or f'Chapter {idx + 1}'

    # --- Helpers ---

    @staticmethod
    def _normalize(chapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort by start, drop duplicates/negatives, ensure a marker at 0 ms."""
        cleaned = [
            {'start_ms': int(c['start_ms']), 'title': str(c.get('title') or '').strip()}
            for c in chapters
            if c.get('start_ms') is not None and int(c['start_ms']) >= 0
        ]
        if not cleaned:
            return []
        cleaned.sort(key=lambda c: c['start_ms'])

        deduped: List[Dict[str, Any]] = []
        seen_starts = set()
        for chapter in cleaned:
            if chapter['start_ms'] in seen_starts:
                continue
            seen_starts.add(chapter['start_ms'])
            if not chapter['title']:
                chapter['title'] = f'Chapter {len(deduped) + 1}'
            deduped.append(chapter)
        return deduped
