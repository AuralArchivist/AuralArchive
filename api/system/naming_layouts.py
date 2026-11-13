"""
Module Name: naming_layouts.py
Author: AuralArchivist
Created: November 12, 2026
Last Modified: November 12, 2026
Description:
    Naming layouts API: list, save and delete the folder layouts users build
    from blocks, and check one with previews on real books.

Location:
    /api/system/naming_layouts.py

Endpoints:

GET
    /api/naming/layouts                 Built-in and saved layouts, where each is used, block choices

POST
    /api/naming/layouts                 Create {name, layout}
    /api/naming/layouts/check           {layout} errors, warnings and previews on sample books

PUT
    /api/naming/layouts/<name>          Update {layout}

DELETE
    /api/naming/layouts/<name>          Delete (refused while a library or the global setting uses it)
"""

from typing import Any, Dict, List

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from core.logging import get_module_logger
from services.container import container
from services.processing.naming.layout import NUMBER_FOLLOWERS, NUMBER_LABELS, SEPARATORS, render
from services.processing.naming.layout_check import abs_read, check
from services.processing.naming.layout_reader import read_folders
from services.processing.naming.layout_store import (
    LayoutError, delete_layout, list_layouts, marketing_words, save_layout, where_used,
)

naming_layouts_api = Blueprint('naming_layouts_api', __name__, url_prefix='/api/naming/layouts')
logger = get_module_logger("API.System.NamingLayouts")

# Stand-ins for the cases a library may not have yet
EXAMPLE_BOOKS = {
    'number': {'Title': 'The First Spell', 'Author': 'Sample Author', 'Series': 'Sample Saga', 'Sequence': '5',
               'Narrator': 'Sample Narrator', 'ASIN': 'B000000005', 'release_date': '2024-05-01'},
    'decimal': {'Title': 'The Short Story', 'Author': 'Sample Author', 'Series': 'Sample Saga', 'Sequence': '1.5',
                'ASIN': 'B000000015', 'release_date': '2023-02-01'},
    'bundle': {'Title': 'Sample Saga: Books 9-10', 'Author': 'Sample Author', 'Series': 'Sample Saga',
               'Sequence': '9-10', 'release_date': '2025-01-01'},
    'standalone': {'Title': 'A Book on Its Own', 'Author': 'Sample Author', 'Series': '', 'ASIN': 'B000000099',
                   'release_date': '2021-08-01'},
    'authors': {'Title': 'Written Together', 'Author': 'First Writer, Second Writer', 'Series': '',
                'release_date': '2022-03-01'},
}

_SAMPLE_QUERIES = {
    'number': "series NOT IN ('', 'N/A') AND sequence GLOB '[0-9]*' AND sequence NOT GLOB '*[^0-9]*'",
    'decimal': "series NOT IN ('', 'N/A') AND sequence GLOB '*[0-9].[0-9]*'",
    'bundle': "series NOT IN ('', 'N/A') AND sequence GLOB '*[0-9]-[0-9]*'",
    'standalone': "(series IS NULL OR series IN ('', 'N/A'))",
    'authors': "author LIKE '%,%'",
}


@naming_layouts_api.errorhandler(LayoutError)
def _layout_error(exc):
    return jsonify({'success': False, 'error': str(exc)}), 400


def sample_books() -> List[Dict[str, Any]]:
    """One real book per tricky case where the library has one, an example otherwise."""
    books = []
    with container.get_engine().connect() as conn:
        for case, where in _SAMPLE_QUERIES.items():
            row = conn.execute(text(
                "SELECT title, author, series, sequence, narrator, asin, release_date FROM books "
                f"WHERE {where} ORDER BY id LIMIT 1"
            )).fetchone()
            if row:
                books.append({'Title': row[0], 'Author': row[1], 'Series': row[2], 'Sequence': row[3],
                              'Narrator': row[4], 'ASIN': row[5], 'release_date': row[6], 'example': False})
            else:
                books.append({**EXAMPLE_BOOKS[case], 'example': True})
    return books


def _preview(layout: Dict[str, Any], books: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    words = marketing_words()
    previews = []
    for book in books:
        parts = render({'clean': True, **layout}, book, words)
        folders = parts[:-1]
        ours = read_folders(layout, folders)
        theirs = abs_read(folders)
        previews.append({
            'title': book.get('Title'),
            'example': book.get('example', False),
            'path': '/'.join(folders),
            'file': f'{parts[-1]}.m4b',
            'auralarchive_reads': {k: v for k, v in ours.items() if k != 'matched' and v},
            'audiobookshelf_reads': {k: v for k, v in theirs.items() if v},
        })
    return previews


@naming_layouts_api.route('', methods=['GET'])
def get_layouts():
    layouts = list_layouts()
    for entry in layouts:
        entry['used_by'] = where_used(entry['name'])
    return jsonify({
        'success': True,
        'layouts': layouts,
        'choices': {'separators': list(SEPARATORS), 'labels': list(NUMBER_LABELS),
                    'followers': list(NUMBER_FOLLOWERS)},
    })


@naming_layouts_api.route('/check', methods=['POST'])
def check_layout():
    layout = (request.get_json(silent=True) or {}).get('layout')
    books = sample_books()
    result = check(layout, books)
    previews = [] if result['errors'] else _preview(layout, books)
    return jsonify({'success': True, **result, 'previews': previews})


@naming_layouts_api.route('', methods=['POST'])
def create_layout():
    data = request.get_json(silent=True) or {}
    warnings = save_layout(data.get('name', ''), data.get('layout'), create=True, books=sample_books())
    return jsonify({'success': True, 'warnings': warnings})


@naming_layouts_api.route('/<name>', methods=['PUT'])
def update_layout(name: str):
    data = request.get_json(silent=True) or {}
    warnings = save_layout(name, data.get('layout'), create=False, books=sample_books())
    return jsonify({'success': True, 'warnings': warnings})


@naming_layouts_api.route('/<name>', methods=['DELETE'])
def remove_layout(name: str):
    delete_layout(name)
    return jsonify({'success': True})
