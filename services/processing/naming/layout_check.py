"""
Module Name: layout_check.py
Author: AuralArchivist
Created: November 12, 2026
Last Modified: November 13, 2026
Description:
    Checks a block layout before it is saved: errors for layouts AuralArchive
    could not read back, warnings for folders Audiobookshelf would read differently.

Location:
    /services/processing/naming/layout_check.py
"""

import re
from typing import Any, Dict, List, Optional, Sequence

from .layout import (
    FREE_VALUES, NUMBER_FOLLOWERS, NUMBER_LABELS, SEPARATORS, VALUE_BLOCKS, plain_number, render,
)
from .template_parser import TemplateParser

MAX_ROWS = 5
LABEL_MAX = 20

NAMES = {
    'author': 'Author', 'series': 'Series', 'series_number': 'Series number', 'title': 'Title',
    'year': 'Year', 'narrator': 'Narrator', 'asin': 'ASIN', 'separator': 'Separator',
}

_ABS_LABEL = r'(?:vol(?:ume)?\.?|book)'


def abs_read(parts: Sequence[str]) -> Dict[str, Optional[str]]:
    """What Audiobookshelf takes from an Author/Series/Book path, following its documented
    folder rules: sequence first ("1 - ", "1. ") or later ("Vol 1", "Book 1"), then year,
    title, {narrator} and [ASIN]."""
    parts = [p for p in parts if p]
    read: Dict[str, Optional[str]] = {'author': None, 'series': None, 'sequence': None,
                                      'year': None, 'title': None, 'narrator': None, 'asin': None}
    if not parts:
        return read
    if len(parts) >= 3:
        read['author'], read['series'] = parts[-3], parts[-2]
    elif len(parts) == 2:
        read['author'] = parts[-2]
    name = parts[-1]
    asin = re.search(r'\[([A-Z0-9]{10})\]', name)
    if asin:
        read['asin'] = asin.group(1)
        name = name.replace(asin.group(0), ' ')
    narrator = re.search(r'\{([^{}]+)\}', name)
    if narrator:
        read['narrator'] = narrator.group(1).strip()
        name = name.replace(narrator.group(0), ' ')
    name = re.sub(r'\s+', ' ', name).strip()
    first = re.match(rf'^(?:{_ABS_LABEL}\s*)?(\d+(?:\.\d+)?)(?:\s+-\s+|\.\s+)(.+)$', name, re.IGNORECASE)
    # "1994 - Title" is a year, not book 1994
    if first and re.fullmatch(r'\d{4}', first.group(1)) and not re.match(_ABS_LABEL, name, re.IGNORECASE):
        first = None
    if first:
        read['sequence'], name = first.group(1), first.group(2)
    else:
        later = re.search(rf'(?:\s+-\s+)?\b{_ABS_LABEL}\s+(\d+(?:\.\d+)?)\b', name, re.IGNORECASE)
        if later:
            read['sequence'] = later.group(1)
            name = (name[:later.start()] + name[later.end():]).strip(' -')
    year = re.match(r'^(\d{4})\s+-\s+(.+)$', name)
    if year:
        read['year'], name = year.group(1), year.group(2)
    read['title'] = name.strip() or None
    if read['sequence']:
        read['sequence'] = plain_number(read['sequence'])
    return read


def _structure_errors(layout: Any) -> List[str]:
    if not isinstance(layout, dict) or not isinstance(layout.get('rows'), list) or not layout['rows']:
        return ['A layout needs at least one folder.']
    if len(layout['rows']) > MAX_ROWS:
        return [f'A layout can have at most {MAX_ROWS} folders.']
    errors = []
    for number, row in enumerate(layout['rows'], 1):
        if not isinstance(row, list) or not any(isinstance(b, dict) and b.get('type') in VALUE_BLOCKS for b in row):
            errors.append(f'Folder {number} needs at least one value block.')
            continue
        for block in row:
            errors += _block_errors(block, number)
    return errors


def _block_errors(block: Any, row: int) -> List[str]:
    kind = block.get('type') if isinstance(block, dict) else None
    if kind == 'separator':
        return [] if block.get('text') in SEPARATORS else [f'Folder {row}: unknown separator.']
    if kind not in VALUE_BLOCKS:
        return [f'Folder {row}: unknown block.']
    if kind != 'series_number':
        return []
    errors = []
    if block.get('pad', 0) not in (0, 2, 3):
        errors.append(f'Folder {row}: padding must be none, 2 or 3 digits.')
    if block.get('follow', '') not in NUMBER_FOLLOWERS:
        errors.append(f'Folder {row}: the series number can be followed by " - ", ". ", a space or nothing.')
    label = block.get('label', '')
    if label not in NUMBER_LABELS:
        if not isinstance(label, str) or len(label) > LABEL_MAX or re.search(r'[/\\\[\]{}()]', label) \
                or any(sep.strip() and sep in label for sep in SEPARATORS) or label != label.strip():
            errors.append(f'Folder {row}: a custom label can be up to {LABEL_MAX} characters, '
                          'without slashes, brackets or separators.')
    return errors


def _bracketed(block: Dict[str, Any]) -> bool:
    kind = block['type']
    return ((kind == 'narrator' and block.get('braces', True)) or (kind == 'asin' and block.get('brackets', True))
            or (kind == 'year' and bool(block.get('brackets'))))


def _free(block: Dict[str, Any]) -> bool:
    return block['type'] in FREE_VALUES and not _bracketed(block)


def _fence_errors(row: List[Dict[str, Any]], number: int) -> List[str]:
    """Two values next to each other need something between them the reader can find."""
    errors = []
    previous = None
    fence = ''
    for block in row:
        if block['type'] == 'separator':
            fence = block['text']
            continue
        if previous is not None:
            if _bracketed(previous) or _bracketed(block):
                pass
            elif not fence:
                errors.append(f'Folder {number}: {NAMES[previous["type"]]} and {NAMES[block["type"]]} touch. '
                              'Add a separator between them.')
            elif fence != ' - ' and _free(previous) and _free(block):
                errors.append(f'Folder {number}: "{fence}" between {NAMES[previous["type"]]} and '
                              f'{NAMES[block["type"]]} can also appear inside them. Use " - " instead.')
        previous = block
        fence = block.get('follow', '') if block['type'] == 'series_number' else ''
    return errors


def check(layout: Any, books: Sequence[Dict[str, Any]] = ()) -> Dict[str, List[str]]:
    """{'errors': [...], 'warnings': [...]}. Errors block saving; warnings explain how
    Audiobookshelf will read the folders, using the given sample books."""
    errors = _structure_errors(layout)
    if errors:
        return {'errors': errors, 'warnings': []}
    rows = layout['rows']
    for number, row in enumerate(rows, 1):
        errors += _fence_errors(row, number)
    last = rows[-1]
    kinds = {b['type'] for b in last}
    if not (kinds & {'title', 'asin'} or {'series_number', 'series'} <= kinds):
        errors.append('The last folder is the book folder: it needs the Title or the ASIN, '
                      'or the Series number together with the Series, so every book gets its own folder.')
    if errors:
        return {'errors': errors, 'warnings': []}
    return {'errors': [], 'warnings': _abs_warnings(layout, books)}


def _abs_warnings(layout: Dict[str, Any], books: Sequence[Dict[str, Any]]) -> List[str]:
    rows = layout['rows']
    warnings = []
    if [b['type'] for b in rows[0] if b['type'] != 'separator'] != ['author']:
        warnings.append('Audiobookshelf expects the first folder to be the author on its own.')
    if len(rows) > 3:
        warnings.append('Audiobookshelf reads at most Author / Series / Book; deeper folders are not used.')
    if len(rows) == 3 and [b['type'] for b in rows[1] if b['type'] != 'separator'] != ['series']:
        warnings.append('Audiobookshelf reads the middle folder as the series name.')
    for book in books:
        warning = _sequence_warning(layout, book)
        if warning:
            warnings.append(warning)
            break
    return warnings


def _sequence_warning(layout: Dict[str, Any], book: Dict[str, Any]) -> Optional[str]:
    number = TemplateParser()._get_series_number(book)
    # No layout makes Audiobookshelf read a bundle range (9-10) as a book number
    if not number or '-' in number or not any(b['type'] == 'series_number' for b in layout['rows'][-1]):
        return None
    folders = render(layout, book)[:-1]
    seen = abs_read(folders).get('sequence')
    if seen == plain_number(number.split('-')[0]):
        return None
    folder = folders[-1] if folders else ''
    return (f'Audiobookshelf reads a book number only when " - " or ". " follows it at the start, '
            f'or as "Vol 5" / "Book 5" later on. From "{folder}" it would see '
            f'{"book " + seen if seen else "no book number"}.')
