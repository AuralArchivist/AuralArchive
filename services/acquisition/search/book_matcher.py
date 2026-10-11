"""
Module Name: book_matcher.py
Author: AuralArchivist
Created: June 9, 2026
Last Modified: October 9, 2026
Description:
    Series and book number matching for search results.

Location:
    /services/acquisition/search/book_matcher.py
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


# --- Patterns ----------------------------------------------------------------

_EXPLICIT_NUM = re.compile(
    r'\b(?:book|bk|vol(?:ume)?|part|episode|ep)\.?\s+(\d+(?:\.\d+)?)'
    r'|#\s*(\d+(?:\.\d+)?)',
    re.IGNORECASE,
)

_TRAILING_NUM = re.compile(r'(?<!\d)(\d+(?:\.\d+)?)\s*$')

_SERIES_QUALIFIER = re.compile(
    r'\b(?:level|tier|type|stage|grade|rank|class|arc|season|version|v)\s+\d+(?:\.\d+)?\s*$',
    re.IGNORECASE,
)

_BUNDLE = re.compile(
    r'\b(?:'
    r'books?\s+\d+\s*[-\u2013\u2014]\s*\d+'
    r'|volumes?\s+\d+\s*[-\u2013\u2014]\s*\d+'
    r'|parts?\s+\d+\s*[-\u2013\u2014]\s*\d+'
    r'|complete\s+series'
    r'|omnibus'
    r'|box\s+set'
    r'|bundle'
    r'|collection'
    r')\b',
    re.IGNORECASE,
)

_SERIES_NOISE = re.compile(r'\s+Series\s*$', re.IGNORECASE)

_WORD_NUMBERS: Dict[str, float] = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
    'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
    'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
    'eighteen': 18, 'nineteen': 19, 'twenty': 20,
}

_ROMAN: Dict[str, float] = {
    'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
    'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
    'XI': 11, 'XII': 12, 'XIII': 13, 'XIV': 14, 'XV': 15,
    'XVI': 16, 'XVII': 17, 'XVIII': 18, 'XIX': 19, 'XX': 20,
    'XXI': 21, 'XXII': 22, 'XXIII': 23, 'XXIV': 24, 'XXV': 25,
    'XXVI': 26, 'XXVII': 27, 'XXVIII': 28, 'XXIX': 29, 'XXX': 30,
}
_ROMAN_PATTERN = re.compile(
    r'\b(?:book|vol(?:ume)?|part)\.?\s+(' + '|'.join(_ROMAN) + r')\b',
    re.IGNORECASE,
)


# --- Data class --------------------------------------------------------------

@dataclass
class BookComponents:
    series_name: Optional[str]
    book_number: Optional[float]
    is_bundle: bool
    source: str


# --- Matcher -----------------------------------------------------------------

class BookMatcher:
    """
    Extracts and compares book components for audiobook search matching.

    Usage:
        matcher = BookMatcher()
        query_comps  = matcher.extract_from_title(search_title)
        result_comps = matcher.extract_from_result(result_dict)
        status = matcher.compare_numbers(query_comps.book_number, result_comps.book_number)
    """

    # --- Public extraction API ---

    def extract_from_result(self, result: Dict[str, Any]) -> BookComponents:
        """
        Two-path extraction.

        Path A - structured (MAM, any provider that sets result['series'] /
        result['sequence']): uses those fields directly.

        Path B - extracted: falls back to parsing result['title'].
        """
        series_field = result.get('series')
        sequence_field = result.get('sequence')
        title = result.get('title') or ''

        if series_field or sequence_field:
            # --- Path A: structured ---

            book_number: Optional[float] = None
            if sequence_field is not None:
                book_number = self._parse_float(str(sequence_field))
            if book_number is None:
                book_number = self._extract_book_number(title)

            return BookComponents(
                series_name=self._clean_series(series_field),
                book_number=book_number,
                is_bundle=self._is_bundle(title),
                source='structured',
            )

        # --- Path B: extracted ---

        return self.extract_from_title(title)

    def extract_from_title(self, title: str) -> BookComponents:
        """Extract components from a raw title string."""
        if not title:
            return BookComponents(None, None, False, 'extracted')

        core = self._strip_subtitle(title)

        book_number = self._extract_book_number(core)
        series_name = self._extract_series_name(core, book_number)

        return BookComponents(
            series_name=series_name,
            book_number=book_number,
            is_bundle=self._is_bundle(title),
            source='extracted',
        )

    # --- Number comparison ---

    @staticmethod
    def compare_numbers(a: Optional[float], b: Optional[float]) -> str:
        """
        Returns one of:
          'exact' - both present and equal (within float epsilon)
          'mismatch' - both present but differ
          'missing' - at least one is None
        """
        if a is None or b is None:
            return 'missing'
        return 'exact' if abs(a - b) < 0.001 else 'mismatch'

    @staticmethod
    def from_known_components(series_name: Optional[str], sequence_value: Optional[Any]) -> Optional['BookComponents']:
        """Build BookComponents from a book's own Series/Sequence DB fields,
        bypassing title-string parsing entirely.

        The expected book number for a search is a property of which book is
        being searched for, not something that should be re-derived from
        whichever query-string text happened to surface a given result -
        title-parsed numbers vary per query variant, this doesn't.

        Returns None if there's no usable number - callers should fall back
        to extract_from_title()/extract_from_result() in that case, same as
        extract_from_result()'s own structured path degrades to parsing.
        """
        book_number = BookMatcher._parse_float(str(sequence_value)) if sequence_value else None
        if book_number is None:
            return None
        return BookComponents(
            series_name=BookMatcher._clean_series(series_name),
            book_number=book_number,
            is_bundle=False,
            source='structured',
        )

    # --- Internal helpers ---

    @staticmethod
    def _parse_float(text: str) -> Optional[float]:
        try:
            return float(text.strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _is_bundle(title: str) -> bool:
        return bool(_BUNDLE.search(title))

    @staticmethod
    def _clean_series(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return _SERIES_NOISE.sub('', name.strip()).strip() or None

    @staticmethod
    def _strip_subtitle(title: str) -> str:
        """Remove subtitle after ': ' or ' - ' when it follows content."""
        stripped = re.sub(r':\s+.+$', '', title).strip()
        if len(stripped) > 5:
            return stripped
        return title

    def _extract_book_number(self, text: str) -> Optional[float]:
        """
        Try multiple strategies in order of confidence.
        Returns float or None.
        """
        if not text:
            return None

        m = _EXPLICIT_NUM.search(text)
        if m:
            num_str = m.group(1) or m.group(2)
            result = self._parse_float(num_str)
            if result is not None:
                return result

        m = _ROMAN_PATTERN.search(text)
        if m:
            roman = m.group(1).upper()
            if roman in _ROMAN:
                return float(_ROMAN[roman])

        m = re.search(
            r'\b(?:book|vol(?:ume)?|part)\.?\s+(\w+)\b',
            text, re.IGNORECASE
        )
        if m:
            word = m.group(1).lower()
            if word in _WORD_NUMBERS:
                return float(_WORD_NUMBERS[word])

        words = text.split()
        if len(words) >= 2 and not _SERIES_QUALIFIER.search(text):
            m = _TRAILING_NUM.search(text.rstrip())
            if m:
                result = self._parse_float(m.group(1))
                if result is not None:
                    return result

        return None

    def _extract_series_name(self, title: str, book_number: Optional[float]) -> Optional[str]:
        """
        Return everything before the book-number marker, cleaned up.
        Falls back to the full title if no number marker found.
        """
        if not title:
            return None

        cleaned = re.sub(
            r'[,:\s]*\b(?:book|bk|vol(?:ume)?|part|episode|ep)\.?\s+\d[\d.]*.*$',
            '', title, flags=re.IGNORECASE,
        ).strip()

        if cleaned == title and book_number is not None:
            num_str = str(int(book_number)) if book_number == int(book_number) else str(book_number)
            cleaned = re.sub(r'[,:\s]*' + re.escape(num_str) + r'\s*$', '', title).strip()

        cleaned = self._clean_series(cleaned) or cleaned
        return cleaned if cleaned else None
