"""
Module Name: client.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Thin client for the Audiobookshelf REST API (written against v2.37.1,
    see dev/archive/AudiobookshelfAPI). Only the endpoints AuralArchive needs
    can be called, so nothing here can delete, restore or reconfigure the
    server.

Location:
    /integrations/audiobookshelf/client.py
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from core.logging import get_module_logger
from core.http import HttpSession

_LOGGER = get_module_logger("Integration.Audiobookshelf")

DEFAULT_TIMEOUT = 10
_ID = r'[A-Za-z0-9_\-]+'

# (method, path pattern) pairs the client may call
ALLOWED = [(method, re.compile(f'^{pattern}$')) for method, pattern in (
    ('GET', '/status'),
    ('GET', '/api/me'),
    ('GET', '/api/me/progress'),
    ('GET', '/api/libraries'),
    ('GET', f'/api/libraries/{_ID}'),
    ('GET', f'/api/libraries/{_ID}/items'),
    ('GET', f'/api/items/{_ID}'),
    ('GET', '/api/users'),
    ('GET', f'/api/users/{_ID}'),
    ('POST', '/api/watcher/update'),
    ('POST', f'/api/libraries/{_ID}/scan'),
    ('POST', f'/api/items/{_ID}/match'),
    ('POST', '/api/items/batch/quickmatch'),
    ('PATCH', f'/api/items/{_ID}/media'),
)]


class AbsError(Exception):
    """Any Audiobookshelf failure."""


class AbsUnavailable(AbsError):
    """The server couldn't be reached, timed out or returned a server error."""


class AbsAuthError(AbsError):
    """401: the key is wrong, revoked or expired."""


class AbsForbidden(AbsError):
    """403: the key's user lacks a permission (often admin)."""


class AbsNotFound(AbsError):
    """404."""


class AbsClient:

    def __init__(self, base_url: str, api_key: str, *, verify_tls: bool = True,
                 timeout: float = DEFAULT_TIMEOUT, logger=None):
        self.base_url = (base_url or '').rstrip('/')
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.logger = logger or _LOGGER
        self.session = HttpSession(headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'})

    # --- Transport ---

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                 json: Any = None, auth: bool = True) -> Any:
        if not any(m == method and pattern.match(path) for m, pattern in ALLOWED):
            raise AbsError(f'Endpoint not allowed: {method} {path}')
        url = f'{self.base_url}{path}'
        headers = None if auth else {'Authorization': None}
        try:
            resp = self.session.request(method, url, params=params, json=json, headers=headers,
                                        timeout=self.timeout, verify=self.verify_tls)
        except requests.exceptions.SSLError as exc:
            raise AbsUnavailable(f'TLS certificate problem: {exc}') from exc
        except requests.exceptions.ConnectionError as exc:
            raise AbsUnavailable(f"Can't connect to {self.base_url}: {_short(exc)}") from exc
        except requests.exceptions.Timeout as exc:
            raise AbsUnavailable(f'{self.base_url} did not answer within {self.timeout} seconds') from exc
        except requests.exceptions.RequestException as exc:
            raise AbsUnavailable(str(exc)) from exc

        self.logger.debug("Audiobookshelf request", extra={"method": method, "path": path, "status": resp.status_code})
        if resp.status_code == 401:
            raise AbsAuthError('Audiobookshelf rejected the API key')
        if resp.status_code == 403:
            raise AbsForbidden('The API key\'s user is not allowed to do this (admin may be required)')
        if resp.status_code == 404:
            raise AbsNotFound(f'Not found: {path}')
        if resp.status_code == 429 or resp.status_code >= 500:
            raise AbsUnavailable(f'Audiobookshelf returned {resp.status_code}')
        if resp.status_code >= 400:
            raise AbsError(f'Audiobookshelf returned {resp.status_code}: {resp.text[:200]}')
        if not resp.content or resp.headers.get('content-type', '').split(';')[0] != 'application/json':
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    # --- Server and user ---

    def status(self) -> Dict[str, Any]:
        return self._request('GET', '/status', auth=False) or {}

    def me(self) -> Dict[str, Any]:
        return self._request('GET', '/api/me') or {}

    def my_progress(self) -> List[Dict[str, Any]]:
        return (self._request('GET', '/api/me/progress') or {}).get('mediaProgress') or []

    def users(self) -> List[Dict[str, Any]]:
        return (self._request('GET', '/api/users') or {}).get('users') or []

    def user(self, user_id: str) -> Dict[str, Any]:
        return self._request('GET', f'/api/users/{quote(user_id)}') or {}

    # --- Libraries and items ---

    def libraries(self) -> List[Dict[str, Any]]:
        return (self._request('GET', '/api/libraries') or {}).get('libraries') or []

    def library(self, library_id: str) -> Dict[str, Any]:
        return self._request('GET', f'/api/libraries/{quote(library_id)}') or {}

    def library_items(self, library_id: str, *, page: int = 0, limit: int = 500,
                      sort: Optional[str] = None, desc: bool = False) -> Dict[str, Any]:
        params: Dict[str, Any] = {'limit': limit, 'page': page, 'minified': 1}
        if sort:
            params['sort'] = sort
        if desc:
            params['desc'] = 1
        return self._request('GET', f'/api/libraries/{quote(library_id)}/items', params=params) or {}

    def all_library_items(self, library_id: str, page_size: int = 500) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = 0
        while True:
            data = self.library_items(library_id, page=page, limit=page_size)
            batch = data.get('results') or []
            items.extend(batch)
            if len(batch) < page_size or len(items) >= int(data.get('total') or 0):
                return items
            page += 1

    def item(self, item_id: str, *, expanded: bool = False) -> Dict[str, Any]:
        params = {'expanded': 1} if expanded else None
        return self._request('GET', f'/api/items/{quote(item_id)}', params=params) or {}

    # --- Writes ---

    def watcher_update(self, library_id: str, path: str, change: str = 'add',
                       old_path: Optional[str] = None) -> None:
        body = {'libraryId': library_id, 'path': path, 'type': change}
        if old_path:
            body['oldPath'] = old_path
        self._request('POST', '/api/watcher/update', json=body)

    def scan_library(self, library_id: str) -> None:
        self._request('POST', f'/api/libraries/{quote(library_id)}/scan')

    def match_item(self, item_id: str, *, provider: str, asin: str,
                   override_details: bool = False, override_cover: bool = False) -> Dict[str, Any]:
        body = {'provider': provider, 'asin': asin,
                'overrideDetails': bool(override_details), 'overrideCover': bool(override_cover)}
        return self._request('POST', f'/api/items/{quote(item_id)}/match', json=body) or {}

    def batch_quickmatch(self, item_ids: List[str], *, provider: str,
                         override_details: bool = False, override_cover: bool = False) -> None:
        body = {'libraryItemIds': list(item_ids),
                'options': {'provider': provider, 'overrideDetails': bool(override_details),
                            'overrideCover': bool(override_cover)}}
        self._request('POST', '/api/items/batch/quickmatch', json=body)

    def patch_media(self, item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request('PATCH', f'/api/items/{quote(item_id)}/media', json=payload) or {}


def _short(exc: Exception) -> str:
    text = str(exc)
    match = re.search(r'\[Errno -?\d+\] ([^\'")]+)', text)
    return match.group(1) if match else text[:160]


def parse_version(version: str) -> tuple:
    parts = re.findall(r'\d+', version or '')
    return tuple(int(p) for p in parts[:3]) or (0,)
