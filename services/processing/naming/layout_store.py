"""
Module Name: layout_store.py
Author: AuralArchivist
Created: November 12, 2026
Last Modified: November 12, 2026
Description:
    Saved naming layouts: the built-ins from code plus the layouts users build,
    kept in the naming_templates table.

Location:
    /services/processing/naming/layout_store.py
"""

import json
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text

from core.logging import get_module_logger
from services.container import container

from .layout import BUILT_IN_LAYOUTS, DEFAULT_MARKETING_WORDS
from .layout_check import check

_LOGGER = get_module_logger("Service.FileNaming.Layouts")

NAME_MAX = 40


class LayoutError(ValueError):
    """A layout change that was refused, with a message for the UI."""


def _row(name: str, layout: Dict[str, Any], built_in: bool) -> Dict[str, Any]:
    return {'name': name, 'label': layout.get('label', name) if built_in else name,
            'built_in': built_in, 'layout': layout}


def list_layouts() -> List[Dict[str, Any]]:
    """Built-ins first, then saved layouts by name."""
    with container.get_engine().connect() as conn:
        saved = conn.execute(text("SELECT name, layout FROM naming_templates ORDER BY name COLLATE NOCASE")).fetchall()
    return ([_row(name, layout, True) for name, layout in BUILT_IN_LAYOUTS.items()]
            + [_row(name, json.loads(layout), False) for name, layout in saved])


def get_layout(name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    if name in BUILT_IN_LAYOUTS:
        return BUILT_IN_LAYOUTS[name]
    with container.get_engine().connect() as conn:
        stored = conn.execute(text("SELECT layout FROM naming_templates WHERE name = :n"), {'n': name}).scalar()
    return json.loads(stored) if stored else None


def _check_name(name: str) -> str:
    name = (name or '').strip()
    if not name or len(name) > NAME_MAX or '/' in name or '\\' in name:
        raise LayoutError(f'A layout name has 1 to {NAME_MAX} characters and no slashes.')
    taken = {n.lower() for n in BUILT_IN_LAYOUTS} | {b['label'].lower() for b in BUILT_IN_LAYOUTS.values()}
    if name.lower() in taken:
        raise LayoutError(f'"{name}" is a built-in layout. Pick another name.')
    return name


def save_layout(name: str, layout: Dict[str, Any], *, create: bool,
                books: Sequence[Dict[str, Any]] = ()) -> List[str]:
    """Create or update a layout. Returns the Audiobookshelf warnings; refuses on errors."""
    name = _check_name(name)
    result = check(layout, books)
    if result['errors']:
        raise LayoutError(' '.join(result['errors']))
    stored = json.dumps({'rows': layout['rows']})
    with container.get_engine().begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM naming_templates WHERE name = :n COLLATE NOCASE"),
                              {'n': name}).scalar()
        if create and exists:
            raise LayoutError(f'A layout named "{name}" already exists.')
        if not create and not exists:
            raise LayoutError(f'There is no layout named "{name}".')
        if create:
            conn.execute(text("INSERT INTO naming_templates (name, layout) VALUES (:n, :l)"),
                         {'n': name, 'l': stored})
        else:
            conn.execute(text("UPDATE naming_templates SET layout = :l, updated_at = CURRENT_TIMESTAMP "
                              "WHERE name = :n"), {'n': name, 'l': stored})
    _LOGGER.info("Saved naming layout %s", name)
    return result['warnings']


def marketing_words() -> List[str]:
    """Genre words whose subtitles are stripped from titles (Settings > Media Management)."""
    stored = (container.get_config_service().get_section('naming') or {}).get('marketing_words')
    if stored is None:
        return list(DEFAULT_MARKETING_WORDS)
    return [word.strip() for word in str(stored).split(',') if word.strip()]


def where_used(name: str) -> List[str]:
    """Places that would fall back to Simple if the layout went away."""
    used = []
    if (container.get_config_service().get_section('naming') or {}).get('naming_template') == name:
        used.append('the global naming setting')
    with container.get_engine().connect() as conn:
        libraries = conn.execute(text("SELECT name FROM libraries WHERE naming_template = :n ORDER BY name"),
                                 {'n': name}).fetchall()
    used += [f'the library "{row[0]}"' for row in libraries]
    return used


def delete_layout(name: str) -> None:
    if name in BUILT_IN_LAYOUTS:
        raise LayoutError('Built-in layouts cannot be deleted.')
    used = where_used(name)
    if used:
        raise LayoutError(f'"{name}" is still used by {", ".join(used)}. Pick another layout there first.')
    with container.get_engine().begin() as conn:
        deleted = conn.execute(text("DELETE FROM naming_templates WHERE name = :n"), {'n': name}).rowcount
    if not deleted:
        raise LayoutError(f'There is no layout named "{name}".')
    _LOGGER.info("Deleted naming layout %s", name)
