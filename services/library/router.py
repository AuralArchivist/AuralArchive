"""
Module Name: router.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Picks the library a book is imported into: an explicit choice, then the
    library chosen for a wanted book, then the library already holding its
    series, then the routing rules, then the default library.

Location:
    /services/library/router.py
"""

import os
from typing import Any, Dict, Optional

from sqlalchemy import text

from core.logging import get_module_logger

from services.container import container

_LOGGER = get_module_logger("Service.Libraries.Router")

SECTION = 'libraries'
# Download statuses after which the destination library can no longer change
IMPORT_STARTED = ('IMPORTING', 'IMPORTED', 'SEEDING', 'SEEDING_COMPLETE')


def keep_series_together() -> bool:
    value = (container.get_config_service().get_section(SECTION) or {}).get('keep_series_together', 'true')
    return str(value).strip().lower() not in ('0', 'false', 'no', 'off')


_EMPTY = {'', 'n/a', 'na', 'none', 'null', 'unknown'}


def _field(book: Dict[str, Any], *names: str) -> str:
    for name in names:
        value = str(book.get(name) or '').strip()
        if value.lower() not in _EMPTY:
            return value
    return ''


class LibraryRouter:

    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        self.repo = container.get_library_repository()

    def resolve(self, book: Dict[str, Any], explicit_id: Optional[int] = None) -> Dict[str, Any]:
        """Returns {'library': {...}, 'reason': str, 'fallback': bool}."""
        decision = self._decide(book, explicit_id)
        lib = decision['library']
        if not lib['enabled'] or not os.path.isdir(lib['root']):
            default = self.repo.default()
            if lib['id'] != default['id']:
                problem = 'is disabled' if not lib['enabled'] else 'folder is not reachable'
                self.logger.warning("Library unavailable, using the default library",
                                    extra={"library": lib['name'], "problem": problem})
                return {'library': default, 'fallback': True,
                        'reason': f"{lib['name']} {problem}, so the default library was used"}
        return decision

    def _decide(self, book: Dict[str, Any], explicit_id: Optional[int]) -> Dict[str, Any]:
        if explicit_id is not None:
            lib = self.repo.get(explicit_id)
            if lib:
                return {'library': lib, 'reason': 'Chosen when it was added', 'fallback': False}

        if book.get('library_status') == 'wanted' and book.get('library_id'):
            lib = self.repo.get(book['library_id'])
            if lib:
                return {'library': lib, 'reason': 'Chosen for this book', 'fallback': False}

        libraries = self.repo.all()
        if len(libraries) <= 1:
            return {'library': self.repo.default(), 'reason': 'Only library', 'fallback': False}

        if keep_series_together():
            lib = self._series_library(book)
            if lib:
                series = _field(book, 'series', 'Series') or 'this series'
                return {'library': lib, 'reason': f'Other books in {series} are here', 'fallback': False}

        for rule in self.repo.rules():
            if self._rule_matches(rule, book):
                lib = self.repo.get(rule['library_id'])
                if lib:
                    return {'library': lib, 'fallback': False,
                            'reason': f"Rule: {rule['kind']} {'is' if rule['kind'] == 'language' else 'contains'} \"{rule['value']}\""}

        return {'library': self.repo.default(), 'reason': 'Default library', 'fallback': False}

    @staticmethod
    def _rule_matches(rule: Dict[str, Any], book: Dict[str, Any]) -> bool:
        wanted = rule['value'].strip().lower()
        if rule['kind'] == 'language':
            return _field(book, 'language', 'Language').lower() == wanted
        haystack = {
            'author': _field(book, 'author', 'Author', 'authors'),
            'series': _field(book, 'series', 'Series', 'series_title'),
            'narrator': _field(book, 'narrator', 'Narrator', 'narrators'),
        }.get(rule['kind'], '')
        return wanted in haystack.lower()

    def _series_library(self, book: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The library holding the most books of this series (newest wins a tie)."""
        series_asin = _field(book, 'series_asin', 'SeriesAsin')
        series = _field(book, 'series', 'Series')
        asin = _field(book, 'asin', 'ASIN').upper()
        if not series_asin and not series:
            return None
        if series_asin:
            where, params = "series_asin = :s", {'s': series_asin}
        else:
            where, params = "LOWER(series) = LOWER(:s) AND LOWER(COALESCE(author, '')) = LOWER(:a)", \
                {'s': series, 'a': _field(book, 'author', 'Author')}
        with container.get_engine().connect() as conn:
            row = conn.execute(text(
                f"SELECT library_id, COUNT(*) AS n, MAX(updated_at) AS latest FROM books "
                f"WHERE {where} AND library_id IS NOT NULL AND library_status IN ('owned', 'missing') "
                f"AND UPPER(COALESCE(asin, '')) != :asin GROUP BY library_id ORDER BY n DESC, latest DESC LIMIT 1"
            ), {**params, 'asin': asin}).fetchone()
        return self.repo.get(row[0]) if row else None

    def resolve_for_asin(self, asin: str, explicit_id: Optional[int] = None) -> Dict[str, Any]:
        return self.resolve(load_book(asin) or {'asin': asin}, explicit_id)


def load_book(asin: str) -> Optional[Dict[str, Any]]:
    if not asin:
        return None
    with container.get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT id, asin, title, author, series, series_asin, language, narrator, library_id, library_status "
            "FROM books WHERE UPPER(asin) = UPPER(:a) LIMIT 1"
        ), {'a': asin.strip()}).fetchone()
    if not row:
        return None
    return dict(zip(('id', 'asin', 'title', 'author', 'series', 'series_asin', 'language', 'narrator',
                     'library_id', 'library_status'), row))


def choose_library(asin: str, library_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """Save the library a wanted book goes to (None = decide by the rules) and
    apply it to its downloads that haven't started importing. Returns the library."""
    library = container.get_library_repository().get(library_id) if library_id not in (None, '') else None
    if library_id not in (None, '') and not library:
        raise ValueError('That library no longer exists')
    placeholders = ', '.join(f"'{status}'" for status in IMPORT_STARTED)
    with container.get_engine().begin() as conn:
        updated = conn.execute(text(
            "UPDATE books SET library_id = :l, updated_at = CURRENT_TIMESTAMP "
            "WHERE UPPER(asin) = UPPER(:a) AND library_status = 'wanted'"
        ), {'l': library and library['id'], 'a': asin.strip()}).rowcount
        if not updated:
            raise ValueError('Only wanted books can be given a library; move owned books instead')
        conn.execute(text(
            f"UPDATE download_queue SET target_library_id = :l "
            f"WHERE UPPER(book_asin) = UPPER(:a) AND status NOT IN ({placeholders})"
        ), {'l': library and library['id'], 'a': asin.strip()})
    return library
