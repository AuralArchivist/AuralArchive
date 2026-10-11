"""
Module Name: service.py
Author: AuralArchivist
Created: October 2, 2026
Last Modified: October 9, 2026
Description:
    Release calendar: watches (an Audible tag/category or a keyword), the
    upcoming and recent titles they find in the Audible catalog, and the
    calendar entries shown on Discover and the dashboard.

Location:
    /services/metadata/release_calendar/service.py
"""

import html
import re
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from core.language_matching import language_allowed
from services.settings.languages import get_preferred_languages
from core.logging import get_module_logger
from services.container import container

_LOGGER = get_module_logger("Service.ReleaseCalendar")

SECTION = 'release_calendar'
DEFAULTS = {'lookahead_days': 90, 'lookback_days': 30}
MAX_DAYS = 365
KINDS = ('tag', 'keyword')

PRODUCTS_URL = "https://api.audible.com/1.0/catalog/products"
RESPONSE_GROUPS = "product_desc,product_attrs,contributors,series,media,product_extended_attrs"
PAGE_SIZE = 50
MAX_PAGES = 40
PAGE_DELAY_SECONDS = 0.5


# --- Settings ----------------------------------------------------------------

def get_settings() -> Dict[str, int]:
    section = container.get_config_service().get_section(SECTION) or {}
    settings = {}
    for key, default in DEFAULTS.items():
        try:
            settings[key] = max(0, min(MAX_DAYS, int(section.get(key, default))))
        except (TypeError, ValueError):
            settings[key] = default
    return settings


def save_settings(data: Dict[str, Any]) -> Dict[str, str]:
    """Validate and save. Returns {field: error}, empty on success."""
    updates, errors = {}, {}
    for key in DEFAULTS:
        if key not in data:
            continue
        try:
            value = int(data[key])
        except (TypeError, ValueError):
            errors[key] = 'Must be a whole number'
            continue
        if not 0 <= value <= MAX_DAYS:
            errors[key] = f'Must be between 0 and {MAX_DAYS}'
            continue
        updates[f'{SECTION}.{key}'] = str(value)
    if not errors and updates and not container.get_config_service().update_multiple_config(updates):
        errors['_'] = 'Failed to save configuration'
    return errors


def window(settings: Optional[Dict[str, int]] = None) -> tuple:
    settings = settings or get_settings()
    today = date.today()
    return today - timedelta(days=settings['lookback_days']), today + timedelta(days=settings['lookahead_days'])


# --- Watches -----------------------------------------------------------------

def _watch_row(row) -> Dict[str, Any]:
    return {
        'id': row[0], 'kind': row[1], 'query': row[2], 'category_id': row[3], 'label': row[4],
        'enabled': bool(row[5]), 'last_synced_at': str(row[6]) if row[6] else None, 'titles': row[7],
    }


def list_watches(enabled_only: bool = False) -> List[Dict[str, Any]]:
    sql = """
        SELECT w.id, w.kind, w.query, w.category_id, w.label, w.enabled, w.last_synced_at,
               (SELECT COUNT(*) FROM release_calendar_hits h WHERE h.watch_id = w.id)
        FROM release_calendar_watches w
    """
    if enabled_only:
        sql += " WHERE w.enabled = 1"
    with container.get_engine().connect() as conn:
        return [_watch_row(r) for r in conn.execute(text(sql + " ORDER BY w.label COLLATE NOCASE"))]


def get_watch(watch_id: int) -> Optional[Dict[str, Any]]:
    return next((w for w in list_watches() if w['id'] == watch_id), None)


def _category_name(category_id: str) -> Optional[str]:
    with container.get_engine().connect() as conn:
        row = conn.execute(text("SELECT name FROM audible_categories WHERE audible_category_id = :c"),
                           {'c': str(category_id)}).fetchone()
    return row[0] if row else None


def _validate_watch(data: Dict[str, Any]) -> tuple:
    kind = (data.get('kind') or '').strip().lower()
    query = (data.get('query') or '').strip() or None
    category_id = str(data.get('category_id') or '').strip() or None
    if kind not in KINDS:
        return None, 'Choose Tag or Keyword'
    if kind == 'tag' and not category_id:
        return None, 'Choose a tag'
    if kind == 'keyword' and not query:
        return None, 'Enter a keyword'
    category_name = None
    if category_id:
        category_name = _category_name(category_id)
        if not category_name:
            return None, 'Unknown tag'
    if kind == 'tag':
        query = None
        label = category_name
    else:
        label = f"{query} in {category_name}" if category_name else query
    return {'kind': kind, 'query': query, 'category_id': category_id, 'label': label,
            'enabled': 1 if data.get('enabled', True) else 0}, None


def create_watch(data: Dict[str, Any]) -> tuple:
    """Returns (watch, error)."""
    values, error = _validate_watch(data)
    if error:
        return None, error
    with container.get_engine().begin() as conn:
        result = conn.execute(text(
            "INSERT INTO release_calendar_watches (kind, query, category_id, label, enabled) "
            "VALUES (:kind, :query, :category_id, :label, :enabled)"
        ), values)
        watch_id = result.lastrowid
    return get_watch(watch_id), None


def update_watch(watch_id: int, data: Dict[str, Any]) -> tuple:
    current = get_watch(watch_id)
    if not current:
        return None, 'Watch not found'
    values, error = _validate_watch({**current, **data})
    if error:
        return None, error
    with container.get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE release_calendar_watches SET kind = :kind, query = :query, category_id = :category_id, "
            "label = :label, enabled = :enabled WHERE id = :id"
        ), {**values, 'id': watch_id})
        # A changed search makes the old results meaningless
        if (values['kind'], values['query'], values['category_id']) != (current['kind'], current['query'], current['category_id']):
            conn.execute(text("DELETE FROM release_calendar_hits WHERE watch_id = :id"), {'id': watch_id})
            conn.execute(text("UPDATE release_calendar_watches SET last_synced_at = NULL WHERE id = :id"), {'id': watch_id})
    prune()
    return get_watch(watch_id), None


def delete_watch(watch_id: int) -> bool:
    with container.get_engine().begin() as conn:
        conn.execute(text("DELETE FROM release_calendar_hits WHERE watch_id = :id"), {'id': watch_id})
        deleted = conn.execute(text("DELETE FROM release_calendar_watches WHERE id = :id"), {'id': watch_id}).rowcount
    prune()
    return bool(deleted)


# --- Catalog sync ------------------------------------------------------------

def _country_code() -> str:
    return (container.get_config_service().get_config_value('audible', 'country_code', 'us') or 'us').strip().upper()


def _search_params(watch: Dict[str, Any], page: int) -> Dict[str, Any]:
    params = {
        'num_results': PAGE_SIZE,
        'page': page,
        'products_sort_by': '-ReleaseDate',
        'response_groups': RESPONSE_GROUPS,
        'image_sizes': '500',
    }
    if watch.get('query'):
        params['keywords'] = watch['query']
    if watch.get('category_id'):
        params['category_id'] = watch['category_id']
    return params


def fetch_watch_products(watch: Dict[str, Any], start: date, end: date, session=None) -> List[Dict[str, Any]]:
    """Catalog products for a watch released between start and end.

    Results are requested newest first (far-future pre-orders on the first
    page), so paging stops once a page reaches dates before start. Catalog
    pages are numbered from 0.
    """
    from integrations.audible.catalog.catalog_search import audible_session
    session = session or audible_session(paced=False)
    country = _country_code()
    languages = get_preferred_languages()
    found = []
    for page in range(MAX_PAGES):
        response = session.get(PRODUCTS_URL, params=_search_params(watch, page), timeout=30)
        response.raise_for_status()
        products = response.json().get('products', [])
        if not products:
            break
        for product in products:
            released = product.get('release_date') or ''
            if not (start.isoformat() <= released <= end.isoformat()):
                continue
            regions = product.get('distribution_rights_region')
            if regions and country not in regions:
                continue
            if languages and not language_allowed(product.get('language'), languages):
                continue
            found.append(product)
        if min((p.get('release_date') or '9999') for p in products) < start.isoformat():
            break
        time.sleep(PAGE_DELAY_SECONDS)
    return found


_BLOCK_TAGS = re.compile(r'<\s*(br|/p|/div|/li|/h[1-6])\s*/?>', re.I)
_ANY_TAG = re.compile(r'<[^>]+>')


def clean_summary(value: Optional[str]) -> str:
    """Audible summaries are HTML; keep paragraph breaks, drop the markup."""
    if not value:
        return ''
    text_value = _ANY_TAG.sub('', _BLOCK_TAGS.sub('\n', value))
    lines = [' '.join(line.split()) for line in html.unescape(text_value).splitlines()]
    return '\n\n'.join(line for line in lines if line)


# Audible lists translators as authors, named like "Name - Translator - translator"
_TRANSLATOR = re.compile(r'\s[-\u2013]\s*(translator|übersetzer|traducteur|traductor|traduttore|vertaler)\b', re.I)


def clean_people(value: Optional[str]) -> str:
    seen, kept = set(), []
    for name in (value or '').split(', '):
        name = name.strip()
        if name and not _TRANSLATOR.search(name) and name.lower() not in seen:
            seen.add(name.lower())
            kept.append(name)
    return ', '.join(kept)


def _names(people) -> str:
    return clean_people(', '.join(p.get('name', '') for p in people or [] if p.get('name')))


def _row_from_product(product: Dict[str, Any]) -> Dict[str, Any]:
    series = (product.get('series') or [{}])[0]
    images = product.get('product_images') or {}
    # publisher_summary is the full blurb; merchandising_summary is a one-line teaser
    summary = clean_summary(product.get('publisher_summary') or product.get('merchandising_summary'))
    return {
        'asin': product['asin'],
        'title': product.get('title') or 'Unknown title',
        'subtitle': product.get('subtitle'),
        'authors': _names(product.get('authors')),
        'narrators': _names(product.get('narrators')),
        'series_title': series.get('title'),
        'series_sequence': str(series['sequence']) if series.get('sequence') else None,
        'release_date': product['release_date'],
        'runtime_min': product.get('runtime_length_min'),
        'language': product.get('language'),
        'publisher': product.get('publisher_name'),
        'summary': summary[:6000],
        'cover_url': images.get('500') or next(iter(images.values()), None),
    }


def _cache_cover(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    from services.metadata.covers.helpers import cache_image
    return cache_image(url) or url


def sync_watch(watch_id: int) -> int:
    """Refresh one watch's titles. Returns how many titles it found."""
    watch = get_watch(watch_id)
    if not watch:
        return 0
    start, end = window()
    products = [p for p in fetch_watch_products(watch, start, end) if p.get('asin')]
    with container.get_engine().connect() as conn:
        known_covers = dict(conn.execute(text("SELECT asin, cover_url FROM release_calendar")).fetchall())
    # Covers are cached before the write transaction: the image cache must not wait on its lock
    rows = []
    for product in products:
        row = _row_from_product(product)
        cached = known_covers.get(row['asin'])
        row['cover_url'] = cached if cached and cached.startswith('/') else _cache_cover(row['cover_url'])
        rows.append(row)
    with container.get_engine().begin() as conn:
        for row in rows:
            conn.execute(text("""
                INSERT INTO release_calendar (asin, title, subtitle, authors, narrators, series_title,
                    series_sequence, release_date, runtime_min, language, publisher, summary, cover_url)
                VALUES (:asin, :title, :subtitle, :authors, :narrators, :series_title, :series_sequence,
                    :release_date, :runtime_min, :language, :publisher, :summary, :cover_url)
                ON CONFLICT(asin) DO UPDATE SET title = excluded.title, subtitle = excluded.subtitle,
                    authors = excluded.authors, narrators = excluded.narrators,
                    series_title = excluded.series_title, series_sequence = excluded.series_sequence,
                    release_date = excluded.release_date, runtime_min = excluded.runtime_min,
                    language = excluded.language, publisher = excluded.publisher,
                    summary = excluded.summary, cover_url = excluded.cover_url,
                    updated_at = CURRENT_TIMESTAMP
            """), row)
        conn.execute(text("DELETE FROM release_calendar_hits WHERE watch_id = :id"), {'id': watch_id})
        for product in products:
            conn.execute(text("INSERT OR IGNORE INTO release_calendar_hits (asin, watch_id) VALUES (:a, :w)"),
                         {'a': product['asin'], 'w': watch_id})
        conn.execute(text("UPDATE release_calendar_watches SET last_synced_at = CURRENT_TIMESTAMP WHERE id = :id"),
                     {'id': watch_id})
    prune()
    _LOGGER.info("Release calendar watch synced", extra={"watch": watch['label'], "titles": len(products)})
    return len(products)


def prune() -> int:
    """Drop titles outside the window and titles no watch found."""
    start, _ = window()
    with container.get_engine().begin() as conn:
        conn.execute(text(
            "DELETE FROM release_calendar_hits WHERE watch_id NOT IN (SELECT id FROM release_calendar_watches)"
        ))
        return conn.execute(text(
            "DELETE FROM release_calendar WHERE release_date < :start "
            "OR asin NOT IN (SELECT asin FROM release_calendar_hits)"
        ), {'start': start.isoformat()}).rowcount


# --- Calendar entries --------------------------------------------------------

def entries(start: date, end: date, watch_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Calendar entries between start and end: titles found by watches plus
    the user's Wanted books that release in that range."""
    params = {'start': start.isoformat(), 'end': end.isoformat()}
    watch_filter = ""
    if watch_id is not None:
        watch_filter = "AND c.asin IN (SELECT asin FROM release_calendar_hits WHERE watch_id = :watch)"
        params['watch'] = watch_id
    with container.get_engine().connect() as conn:
        found = conn.execute(text(f"""
            SELECT c.asin, c.title, c.subtitle, c.authors, c.narrators, c.series_title, c.series_sequence,
                   c.release_date, c.runtime_min, c.language, c.publisher, c.summary, c.cover_url,
                   (SELECT GROUP_CONCAT(h.watch_id) FROM release_calendar_hits h WHERE h.asin = c.asin),
                   b.id, LOWER(b.library_status)
            FROM release_calendar c
            LEFT JOIN books b ON b.asin = c.asin
            WHERE c.release_date BETWEEN :start AND :end {watch_filter}
        """), params).fetchall()
        wanted = [] if watch_id is not None else conn.execute(text("""
            SELECT b.asin, b.title, NULL, b.author, b.narrator, b.series, b.sequence, b.release_date,
                   NULL, b.language, b.publisher, b.summary, b.cover_image, NULL, b.id, LOWER(b.library_status)
            FROM books b
            WHERE LOWER(b.library_status) = 'wanted' AND b.release_date BETWEEN :start AND :end
              AND (b.asin IS NULL OR b.asin NOT IN (SELECT asin FROM release_calendar))
        """), params).fetchall()

    result = []
    for row in list(found) + list(wanted):
        series = row[5] if row[5] and row[5] != 'N/A' else None
        result.append({
            'asin': row[0], 'title': row[1], 'subtitle': row[2], 'authors': clean_people(row[3]), 'narrators': row[4],
            'series': series, 'sequence': row[6] if series else None, 'release_date': row[7],
            'runtime_min': row[8], 'language': row[9], 'publisher': row[10], 'summary': clean_summary(row[11]),
            'cover_url': row[12],
            'watch_ids': [int(w) for w in (row[13] or '').split(',') if w],
            'book_id': row[14], 'library_status': row[15],
        })
    result.sort(key=lambda e: (e['release_date'], (e['title'] or '').lower()))
    return result


# --- Notifications -----------------------------------------------------------

def _summary(items: List[Dict[str, Any]], limit: int = 3) -> str:
    titles = [i['title'] for i in items[:limit]]
    more = len(items) - len(titles)
    return ', '.join(titles) + (f" and {more} more" if more > 0 else '')


def check_notifications() -> None:
    """Raise "releasing today" and "this week" alerts. Safe to call often:
    each alert is keyed to its day or week, so it is raised once."""
    from services.notifications.notification_service import TYPE_RELEASE_CALENDAR, notify
    if not list_watches(enabled_only=True) and not _has_wanted_releases():
        return
    today = date.today()
    todays = entries(today, today)
    if todays:
        notify(TYPE_RELEASE_CALENDAR, f"Releasing today: {len(todays)} title{'s' if len(todays) != 1 else ''}",
               _summary(todays), link='/calendar', level='info',
               dedupe_key=f"calendar_today:{today.isoformat()}")
    monday = today - timedelta(days=today.weekday())
    week = entries(monday, monday + timedelta(days=6))
    if week:
        notify(TYPE_RELEASE_CALENDAR, f"This week: {len(week)} new release{'s' if len(week) != 1 else ''}",
               _summary(week), link='/calendar', level='info',
               dedupe_key=f"calendar_week:{monday.isoformat()}")


def _has_wanted_releases() -> bool:
    with container.get_engine().connect() as conn:
        return bool(conn.execute(text(
            "SELECT 1 FROM books WHERE LOWER(library_status) = 'wanted' AND release_date >= :today LIMIT 1"
        ), {'today': date.today().isoformat()}).fetchone())
