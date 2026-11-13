"""
Module Name: layout.py
Author: AuralArchivist
Created: November 11, 2026
Last Modified: November 12, 2026
Description:
    Folder layouts built from blocks: rows of value and separator blocks, with
    per-block options, that render to a book's folders and file name.

Location:
    /services/processing/naming/layout.py
"""

import re
from typing import Any, Dict, List, Optional, Sequence

from .template_parser import TemplateParser

VALUE_BLOCKS = ('author', 'series', 'series_number', 'title', 'year', 'narrator', 'asin')
SEPARATORS = (' - ', '. ', ', ', ' ')
NUMBER_LABELS = ('', 'Book', 'Vol', 'Volume')
NUMBER_FOLLOWERS = (' - ', '. ', ' ', '')

DEFAULT_MARKETING_WORDS = (
    'LitRPG', 'GameLit', 'Isekai', 'Progression Fantasy', 'Cultivation', 'Deck Building',
    'Adventure', 'Saga', 'Epic',
)

# A free value is plain text that can hold spaces and punctuation; the reader needs a
# fence it can never contain to tell where it ends
FREE_VALUES = ('author', 'series', 'title', 'narrator')

_FOLDERS = [[{'type': 'author'}], [{'type': 'series'}], [{'type': 'title', 'strip': False}]]
_FILE = [{'type': 'title', 'strip': False}]

# The first two keep the names the old string templates produced, so they never clean
BUILT_IN_LAYOUTS: Dict[str, Dict[str, Any]] = {
    'simple': {'label': 'Simple', 'clean': False, 'rows': _FOLDERS, 'file': _FILE},
    'simple_asin': {
        'label': 'Simple ASIN', 'clean': False, 'rows': _FOLDERS,
        'file': [{'type': 'title', 'strip': False}, {'type': 'separator', 'text': ' '}, {'type': 'asin'}],
    },
    'audiobookshelf': {
        'label': 'Audiobookshelf', 'clean': True,
        'rows': [[{'type': 'author'}], [{'type': 'series'}],
                 [{'type': 'series_number', 'pad': 2, 'follow': ' - '}, {'type': 'title'}]],
        'file': _FILE,
    },
}

_values = TemplateParser()


def strip_marketing(title: str, words: Sequence[str]) -> str:
    """Drop subtitle parts that only advertise the genre: "All the Skills: A Deck-Building
    LitRPG" becomes "All the Skills". A leading "Book One" in such a part is kept."""
    parts = re.split(r'(:\s+|\s+-\s+)', title)
    if len(parts) < 3:
        return title
    plain = [re.sub(r'[-\s]+', ' ', w).strip().lower() for w in words if w.strip()]
    kept = [parts[0]]
    for sep, part in zip(parts[1::2], parts[2::2]):
        flat = re.sub(r'[-\s]+', ' ', part).lower()
        if any(re.search(rf'\b{re.escape(w)}\b', flat) for w in plain):
            label = re.match(r'(?:book|vol(?:ume)?|part)\s+[\w.#-]+', part, re.IGNORECASE)
            if label:
                kept += [sep, label.group(0)]
            continue
        kept += [sep, part]
    return ''.join(kept).strip()


def clean_value(value: str) -> str:
    """Make a value safe to read back: no brackets, slashes or " - " inside it."""
    value = value.replace('‘', "'").replace('’', "'").replace('“', '"').replace('”', '"')
    value = value.translate(str.maketrans('[]{}', '()()', ''))
    value = value.replace('/', '-').replace('\\', '-')
    value = re.sub(r'\s+-\s+', ', ', value)
    return re.sub(r'\s+', ' ', value).strip(' ,')


def plain_number(number: str) -> str:
    """Undo padding: "05" -> "5", "01.5" -> "1.5", "09-10" -> "9-10"."""
    return re.sub(r'(?<![.\d])0+(?=\d)', '', number)


def format_number(number: str, pad: int) -> str:
    """Pad each whole number but not decimals: "1.5" -> "01.5", "9-10" -> "09-10"."""
    if not pad:
        return number
    return re.sub(r'(?<![.\d])\d+', lambda m: m.group(0).zfill(pad), number)


def _author(book: Dict[str, Any], all_authors: bool) -> str:
    stored = book.get('AuthorName') or book.get('author') or book.get('Author') or book.get('authors')
    if isinstance(stored, list):
        names = [str(name) for name in stored if name]
    else:
        names = [str(stored)] if stored else []
    if not names:
        return 'Unknown Author'
    if all_authors:
        return ', '.join(names)
    return re.split(r'\s*(?:,|;|&|\band\b)\s*', names[0])[0]


def block_text(block: Dict[str, Any], book: Dict[str, Any], clean: bool,
               words: Sequence[str] = DEFAULT_MARKETING_WORDS) -> str:
    """The text a value block writes for a book, or '' when the book has no value."""
    kind = block['type']
    if kind == 'author':
        value = _author(book, block.get('all', True))
    else:
        value = str(_values._get_variable_value(kind, book) or '').strip()
    if value == 'N/A' or not value:
        return ''
    if kind == 'title' and block.get('strip', True):
        value = strip_marketing(value, words)
    if clean and kind in FREE_VALUES:
        value = clean_value(value)
    if kind == 'series_number':
        number = format_number(value, block.get('pad', 0))
        label = block.get('label', '')
        return f'{label} {number}' if label else number
    if kind == 'year' and block.get('brackets'):
        return f'({value})'
    if kind == 'narrator' and block.get('braces', True):
        return f'{{{value}}}'
    if kind == 'asin' and block.get('brackets', True):
        return f'[{value}]'
    return value


def render_row(blocks: List[Dict[str, Any]], book: Dict[str, Any], clean: bool = False,
               words: Sequence[str] = DEFAULT_MARKETING_WORDS) -> str:
    # Separators and a number's follower only land between two values that are both present
    text = ''
    pending = ''
    for block in blocks:
        if block['type'] == 'separator':
            if text:
                pending = block['text']
            continue
        value = block_text(block, book, clean, words)
        if not value:
            continue
        text += pending + value
        pending = block.get('follow', '') if block['type'] == 'series_number' else ''
    return text.strip()


def _book_folder(blocks: List[Dict[str, Any]], book: Dict[str, Any], clean: bool,
                 words: Sequence[str]) -> str:
    """A book folder without the title falls back to the title when one of its values is missing."""
    if not any(b['type'] in ('title', 'asin') for b in blocks):
        values = [b for b in blocks if b['type'] != 'separator']
        if any(not block_text(b, book, clean, words) for b in values):
            return block_text({'type': 'title'}, book, clean, words)
    return render_row(blocks, book, clean, words)


def render(layout: Dict[str, Any], book: Dict[str, Any],
           words: Optional[Sequence[str]] = None) -> List[str]:
    """Folder names in order, then the file name (without extension). Empty rows drop out."""
    words = DEFAULT_MARKETING_WORDS if words is None else words
    clean = layout.get('clean', True)
    rows = layout['rows']
    folders = [render_row(blocks, book, clean, words) for blocks in rows[:-1]]
    folders.append(_book_folder(rows[-1], book, clean, words))
    file_blocks = layout.get('file') or _FILE
    return [f for f in folders if f] + [render_row(file_blocks, book, False, words)]


def uses_asin(layout: Dict[str, Any]) -> bool:
    blocks = [b for row in layout['rows'] for b in row] + list(layout.get('file') or [])
    return any(b['type'] == 'asin' for b in blocks)
