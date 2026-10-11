"""
Module Name: language_matching.py
Author: AuralArchivist
Created: June 26, 2026
Last Modified: October 9, 2026
Description:
    Language matching shared by search and author pages. The preferred
    languages themselves come from services/settings/languages.py.

Location:
    /core/language_matching.py
"""

from typing import Any, Dict, List, Optional, Set

from core.logging import get_module_logger

logger = get_module_logger("Core.LanguageMatching")

_LANGUAGE_GROUPS: List[Set[str]] = [
    {'en', 'eng', 'english'},
    {'fr', 'fre', 'fra', 'french'},
    {'de', 'ger', 'deu', 'german'},
    {'es', 'spa', 'spanish'},
    {'it', 'ita', 'italian'},
    {'pt', 'por', 'portuguese'},
    {'nl', 'dut', 'nld', 'dutch'},
    {'ru', 'rus', 'russian'},
    {'ja', 'jpn', 'japanese'},
    {'zh', 'chi', 'zho', 'chinese'},
    {'ko', 'kor', 'korean'},
    {'pl', 'pol', 'polish'},
    {'sv', 'swe', 'swedish'},
    {'no', 'nor', 'norwegian'},
    {'da', 'dan', 'danish'},
    {'fi', 'fin', 'finnish'},
]

_LANGUAGE_ALIAS_LOOKUP: Dict[str, Set[str]] = {}
for _group in _LANGUAGE_GROUPS:
    for _token in _group:
        _LANGUAGE_ALIAS_LOOKUP[_token] = _group


def normalize_language_candidates(language_value: Any) -> Set[str]:
    """Derive matching tokens for a language string to compare with preferences."""

    if language_value is None:
        return set()

    normalized = str(language_value).strip().lower()
    if not normalized:
        return set()

    candidates: Set[str] = {normalized}

    if '-' in normalized:
        candidates.add(normalized.split('-', 1)[0])

    if '(' in normalized:
        candidates.add(normalized.split('(', 1)[0].strip())

    if '/' in normalized:
        candidates.update(part.strip() for part in normalized.split('/') if part.strip())

    if ' ' in normalized:
        candidates.add(normalized.split(' ', 1)[0])

    expanded = set(candidates)
    for token in candidates:
        expanded.update(_LANGUAGE_ALIAS_LOOKUP.get(token, set()))

    return expanded


def language_allowed(language_value: Any, preferred_languages: Optional[Set[str]]) -> bool:
    """Determine whether a language value matches the preferred language configuration."""

    if not preferred_languages:
        return True

    candidates = normalize_language_candidates(language_value)
    if not candidates:
        return True

    return any(token in preferred_languages for token in candidates)


def format_language_labels(preferred_languages: Optional[Set[str]]) -> List[str]:
    """Convert language preference tokens into human-readable labels."""

    if not preferred_languages:
        return []

    labels: List[str] = []
    for token in sorted(preferred_languages):
        if token in {'english', 'en'}:
            labels.append('English')
        elif len(token) <= 3:
            labels.append(token.upper())
        else:
            labels.append(token.title())

    return labels
