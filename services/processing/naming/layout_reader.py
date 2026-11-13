"""
Module Name: layout_reader.py
Author: AuralArchivist
Created: November 12, 2026
Last Modified: November 12, 2026
Description:
    Reads a book's folders back into author, series, number, title, year,
    narrator and ASIN, using the block layout that wrote them.

Location:
    /services/processing/naming/layout_reader.py
"""

import re
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence

from .layout import plain_number
from .layout_check import abs_read

FIELDS = ('author', 'series', 'series_number', 'title', 'year', 'narrator', 'asin')

# Author and title always write something (Unknown Author / Unknown Title), the rest can be empty
_ALWAYS = ('author', 'title')


def _value_pattern(block: Dict[str, Any]) -> str:
    kind = block['type']
    if kind == 'series_number':
        label = block.get('label', '')
        prefix = re.escape(label) + r'\s' if label else ''
        return prefix + r'(?P<series_number>\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)'
    if kind == 'year':
        return r'\((?P<year>\d{4})\)' if block.get('brackets') else r'(?P<year>\d{4})'
    if kind == 'asin':
        return r'\[(?P<asin>[A-Z0-9]{10})\]' if block.get('brackets', True) else r'(?P<asin>[A-Z0-9]{10})'
    if kind == 'narrator' and block.get('braces', True):
        return r'\{(?P<narrator>[^{}]+)\}'
    return rf'(?P<{kind}>.+?)'


def _row_pattern(row: List[Dict[str, Any]]) -> Optional[re.Pattern]:
    pieces = []
    fence = ''
    required_before = False
    seen = set()
    for block in row:
        if block['type'] == 'separator':
            fence = block['text']
            continue
        if block['type'] in seen:
            return None
        seen.add(block['type'])
        lead = re.escape(fence) if fence and pieces else ''
        if lead and not required_before:
            lead = f'(?:{lead})?'
        piece = lead + _value_pattern(block)
        if block['type'] in _ALWAYS:
            required_before = True
            pieces.append(piece)
        else:
            pieces.append(f'(?:{piece})?')
        fence = block.get('follow', '') if block['type'] == 'series_number' else ''
    return re.compile(''.join(pieces))


def _read_row(row: List[Dict[str, Any]], name: str, last: bool) -> Optional[Dict[str, str]]:
    pattern = _row_pattern(row)
    match = pattern.fullmatch(name) if pattern else None
    values = {k: v for k, v in match.groupdict().items() if v} if match else None
    # Mirrors the renderer: a book folder without the title is the title when any of its values is missing
    if last and not any(b['type'] in ('title', 'asin') for b in row):
        wanted = {b['type'] for b in row if b['type'] != 'separator'}
        if values is None or not wanted <= values.keys():
            return {'title': name}
    return values


def _droppable(rows: List[List[Dict[str, Any]]]) -> List[int]:
    return [i for i, row in enumerate(rows[:-1]) if not any(b['type'] in _ALWAYS for b in row)]


def read_folders(layout: Dict[str, Any], parts: Sequence[str]) -> Dict[str, Any]:
    """Values read from the folder names under the library root (book folder last).
    'matched' is False when the folders do not fit the layout and the general
    Audiobookshelf rules were used instead."""
    rows = layout['rows']
    parts = [p for p in parts if p][-len(rows):]
    missing = len(rows) - len(parts)
    if 0 <= missing:
        for dropped in combinations(_droppable(rows), missing):
            kept = [row for i, row in enumerate(rows) if i not in dropped]
            values: Dict[str, str] = {}
            for index, (row, name) in enumerate(zip(kept, parts)):
                read = _read_row(row, name, index == len(kept) - 1)
                if read is None:
                    break
                values.update(read)
            else:
                if 'series_number' in values:
                    values['series_number'] = plain_number(values['series_number'])
                return {**{f: values.get(f) for f in FIELDS}, 'matched': True}
    general = abs_read(parts)
    return {'author': general['author'], 'series': general['series'], 'series_number': general['sequence'],
            'title': general['title'], 'year': general['year'], 'narrator': general['narrator'],
            'asin': general['asin'], 'matched': False}
