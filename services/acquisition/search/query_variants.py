"""
Module Name: query_variants.py
Author: AuralArchivist
Created: August 4, 2026
Last Modified: October 9, 2026
Description:
    Builds series/book-number query variants for manual and automatic search.

Location:
    /services/acquisition/search/query_variants.py
"""

import re
from typing import List, Optional

NUMBER_WORD_MAP = {
    'zero': 0,
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
    'eleven': 11,
    'twelve': 12,
    'thirteen': 13,
    'fourteen': 14,
    'fifteen': 15,
    'sixteen': 16,
    'seventeen': 17,
    'eighteen': 18,
    'nineteen': 19,
    'twenty': 20
}


def normalize_sequence_number(sequence_value: Optional[str]) -> Optional[str]:
    if not sequence_value:
        return None
    sequence_str = str(sequence_value).strip()
    if not sequence_str or sequence_str.upper() == 'N/A':
        return None

    digit_match = re.search(r"\d+(?:\.\d+)?", sequence_str)
    if digit_match:
        return digit_match.group(0)

    tokens = re.split(r"[^a-zA-Z]+", sequence_str.lower())
    for token in tokens:
        if token in NUMBER_WORD_MAP:
            return str(NUMBER_WORD_MAP[token])
    return None


def clean_series_name(series: Optional[str]) -> str:
    """Strip trailing noise words like 'Series' that appear in DB series fields."""
    if not series:
        return ""
    return re.sub(r"\s+Series\s*$", "", series.strip(), flags=re.IGNORECASE).strip()


def build_series_book_queries(series: Optional[str], sequence_value: Optional[str]) -> List[str]:
    if not series:
        return []
    normalized_number = normalize_sequence_number(sequence_value)
    if not normalized_number:
        return []

    base = clean_series_name(series)
    if not base:
        return []
    candidates = [
        f"{base}, Book {normalized_number}",
        f"{base} {normalized_number}",
    ]
    seen = set()
    deduped: List[str] = []
    for candidate in candidates:
        text = candidate.strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        deduped.append(text)
    return deduped


def derive_series_queries_from_title(title: Optional[str]) -> List[str]:
    """Extract series queries from a title ending in a bare number, e.g. "Summoner's Shadow 4"."""
    if not title:
        return []
    match = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)$", title.strip())
    if not match:
        return []
    return build_series_book_queries(match.group(1), match.group(2))
