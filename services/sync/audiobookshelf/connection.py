"""
Module Name: connection.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Audiobookshelf connection settings, the connection test, the key's
    capabilities and the shared status shown in Settings. The section is
    [audiobookshelf_server] because the config loader removes the old
    [audiobookshelf] section from the retired integration.

Location:
    /services/sync/audiobookshelf/connection.py
"""

import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from core.convert import to_bool

from integrations.audiobookshelf import AbsClient, AbsError
from integrations.audiobookshelf.client import parse_version
from services.container import container

_LOGGER = get_module_logger("Service.Audiobookshelf")

SECTION = 'audiobookshelf_server'
METADATA_MODES = ('off', 'match_fill', 'match_replace')
DEFAULTS = {
    'enabled': 'false',
    'url': '',
    'api_key': '',
    'verify_tls': 'true',
    'progress_users': 'me',
    'metadata_mode': 'off',
    'metadata_aa_authority': 'false',
    'notify_on_import': 'true',
    'next_in_series': 'true',
    'hints': 'true',
    'sync_interval_hours': '6',
}
MIN_VERSION = (2, 20, 0)
API_KEY_VERSION = (2, 26, 0)

_state_lock = threading.Lock()
_state: Dict[str, Any] = {}


def _hours(value: Any) -> int:
    try:
        return max(0, min(168, int(value)))
    except (TypeError, ValueError):
        return 6


# --- Settings ----------------------------------------------------------------

def get_settings(*, include_key: bool = False) -> Dict[str, Any]:
    section = container.get_config_service().get_section(SECTION) or {}
    raw = {key: section.get(key, default) for key, default in DEFAULTS.items()}
    settings = {
        'enabled': to_bool(raw['enabled']),
        'url': (raw['url'] or '').strip().rstrip('/'),
        'has_key': bool((raw['api_key'] or '').strip()),
        'verify_tls': to_bool(raw['verify_tls']),
        'progress_users': (raw['progress_users'] or 'me').strip(),
        'metadata_mode': raw['metadata_mode'] if raw['metadata_mode'] in METADATA_MODES else 'off',
        'metadata_aa_authority': to_bool(raw['metadata_aa_authority']),
        'notify_on_import': to_bool(raw['notify_on_import']),
        'next_in_series': to_bool(raw['next_in_series']),
        'hints': to_bool(raw['hints']),
        'sync_interval_hours': _hours(raw['sync_interval_hours']),
    }
    if include_key:
        settings['api_key'] = (raw['api_key'] or '').strip()
    return settings


def save_settings(data: Dict[str, Any]) -> Dict[str, str]:
    """Validate and save. Returns {field: error}, empty on success."""
    from core.net_guard import OutboundURLRejected, validate_outbound_url
    updates: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    if 'url' in data:
        url = (data.get('url') or '').strip().rstrip('/')
        if url:
            try:
                url = validate_outbound_url(url).rstrip('/')
            except OutboundURLRejected as exc:
                errors['url'] = str(exc)
        updates['url'] = url
    if 'api_key' in data and (data.get('api_key') or '').strip():
        updates['api_key'] = data['api_key'].strip()
    for key in ('enabled', 'verify_tls', 'metadata_aa_authority', 'notify_on_import', 'next_in_series', 'hints'):
        if key in data:
            updates[key] = 'true' if data[key] in (True, 'true', '1', 1, 'on') else 'false'
    if 'metadata_mode' in data:
        if data['metadata_mode'] not in METADATA_MODES:
            errors['metadata_mode'] = 'Unknown metadata mode'
        else:
            updates['metadata_mode'] = data['metadata_mode']
    if 'progress_users' in data:
        value = data['progress_users']
        if isinstance(value, list):
            value = ','.join(v.strip() for v in value if v and v.strip())
        updates['progress_users'] = (value or 'me').strip()
    if 'sync_interval_hours' in data:
        try:
            hours = int(data['sync_interval_hours'])
            if not 0 <= hours <= 168:
                raise ValueError
            updates['sync_interval_hours'] = str(hours)
        except (TypeError, ValueError):
            errors['sync_interval_hours'] = 'Must be a whole number of hours from 0 to 168'
    if errors:
        return errors
    if updates and not container.get_config_service().update_multiple_config({f'{SECTION}.{k}': v for k, v in updates.items()}):
        errors['_'] = 'Failed to save configuration'
    if 'url' in updates or 'api_key' in updates:
        set_state(capabilities=None, version=None)
    _reset_client()
    return errors


def is_enabled() -> bool:
    settings = get_settings()
    return settings['enabled'] and bool(settings['url']) and settings['has_key']


# --- Client ------------------------------------------------------------------

_client: Optional[AbsClient] = None
_client_signature: Optional[tuple] = None
_client_lock = threading.Lock()


def get_client() -> AbsClient:
    """Client for the saved settings. Raises AbsError when not configured."""
    global _client, _client_signature
    settings = get_settings(include_key=True)
    if not settings['url'] or not settings['api_key']:
        raise AbsError('Audiobookshelf is not configured')
    signature = (settings['url'], settings['api_key'], settings['verify_tls'])
    with _client_lock:
        if _client is None or _client_signature != signature:
            _client = AbsClient(settings['url'], settings['api_key'], verify_tls=settings['verify_tls'])
            _client_signature = signature
        return _client


def _reset_client() -> None:
    global _client, _client_signature
    with _client_lock:
        _client, _client_signature = None, None


# --- Test and capabilities -----------------------------------------------------

def capabilities_from_user(user: Dict[str, Any]) -> Dict[str, Any]:
    user_type = user.get('type') or 'user'
    permissions = user.get('permissions') or {}
    is_admin = user_type in ('root', 'admin')
    return {
        'user': user.get('username'),
        'user_id': user.get('id'),
        'type': user_type,
        'is_admin': is_admin,
        'can_update': bool(permissions.get('update')) or is_admin,
        'legacy_key': bool(user.get('isOldToken')),
    }


def test_connection(url: Optional[str] = None, api_key: Optional[str] = None,
                    verify_tls: Optional[bool] = None) -> Dict[str, Any]:
    """Check a URL and key (or the saved ones). Never raises."""
    from core.net_guard import OutboundURLRejected, validate_outbound_url
    saved = get_settings(include_key=True)
    url = (url or saved['url'] or '').strip().rstrip('/')
    api_key = (api_key or '').strip()
    from core.config import secrets_crypto
    if not api_key or secrets_crypto.is_masked_value(api_key):
        api_key = saved['api_key']
    verify = saved['verify_tls'] if verify_tls is None else bool(verify_tls)
    if not url or not api_key:
        return {'success': False, 'error': 'Enter the server address and an API key'}
    try:
        url = validate_outbound_url(url).rstrip('/')
    except OutboundURLRejected as exc:
        return {'success': False, 'error': str(exc)}

    client = AbsClient(url, api_key, verify_tls=verify)
    from integrations.audiobookshelf import AbsNotFound
    try:
        try:
            status = client.status()
        except AbsNotFound:
            return {'success': False, 'error': "That address answered, but it isn't an Audiobookshelf server"}
        if status.get('app') not in (None, 'audiobookshelf') or 'serverVersion' not in status:
            return {'success': False, 'error': "That address answered, but it isn't an Audiobookshelf server"}
        version = status.get('serverVersion') or '0'
        if parse_version(version) < MIN_VERSION:
            return {'success': False, 'error': f'Audiobookshelf {version} is too old; 2.20 or newer is needed'}
        user = client.me()
        libraries = client.libraries()
    except AbsError as exc:
        return {'success': False, 'error': str(exc)}

    caps = capabilities_from_user(user)
    warnings: List[str] = []
    if caps['legacy_key']:
        warnings.append("This is a legacy user token that never expires. Create an API key in "
                        "Audiobookshelf under Settings › API Keys and use that instead.")
    if parse_version(version) < API_KEY_VERSION:
        warnings.append(f"Audiobookshelf {version} predates API keys (2.26). Consider updating it.")
    if not caps['is_admin']:
        warnings.append("The key's user isn't an admin, so AuralArchive can't ask Audiobookshelf to scan "
                        "new books or read other users' progress.")
    if not caps['can_update']:
        warnings.append("The key's user can't edit items, so metadata matching is unavailable.")

    result = {
        'success': True,
        'version': version,
        'capabilities': caps,
        'warnings': warnings,
        'libraries': [_library_summary(lib) for lib in libraries if lib.get('mediaType', 'book') == 'book'],
    }
    # Only the saved credentials define the connection's capabilities
    if url == saved['url'] and api_key == saved['api_key']:
        set_state(version=version, capabilities=caps, last_test=_now(), last_error=None)
    return result


def _library_summary(lib: Dict[str, Any]) -> Dict[str, Any]:
    settings = lib.get('settings') or {}
    return {
        'id': lib.get('id'),
        'name': lib.get('name'),
        'folders': [{'id': f.get('id'), 'path': f.get('fullPath') or f.get('path')} for f in lib.get('folders') or []],
        'disable_watcher': bool(settings.get('disableWatcher')),
        'metadata_precedence': settings.get('metadataPrecedence') or [],
    }


def capabilities() -> Dict[str, Any]:
    """Capabilities of the saved key, from the last test or sync."""
    caps = get_state().get('capabilities')
    if caps is None and is_enabled():
        try:
            caps = capabilities_from_user(get_client().me())
            set_state(capabilities=caps)
        except AbsError as exc:
            set_state(last_error=str(exc))
            caps = None
    return caps or {}


# --- Shared status ---------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def set_state(**values) -> None:
    with _state_lock:
        _state.update(values)


def get_state() -> Dict[str, Any]:
    with _state_lock:
        return dict(_state)


def record_error(message: str) -> None:
    """Remember the last failure, and raise one notification for a rejected key."""
    set_state(last_error=message, last_error_at=_now())


def notify_auth_failure() -> None:
    from services.notifications import TYPE_AUDIOBOOKSHELF, notify
    notify(TYPE_AUDIOBOOKSHELF, "Audiobookshelf key rejected",
           "Audiobookshelf no longer accepts the saved API key. Syncing is paused until it's replaced.",
           link='/settings#audiobookshelf', level='error', dedupe_key='abs_auth_failed')


def clear_auth_failure() -> None:
    from services.notifications import resolve
    resolve('abs_auth_failed')
