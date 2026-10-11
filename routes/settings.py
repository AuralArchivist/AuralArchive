"""
Module Name: settings.py
Author: AuralArchivist
Created: July 26, 2025
Last Modified: October 9, 2026
Description:
    Settings dashboard routes with tabbed AJAX views and diagnostics helpers.

Location:
    /routes/settings.py
"""

from pathlib import Path

import requests
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from services.container import container
from core.config.secrets_crypto import is_masked_value, is_sensitive_key
from core.logging import get_module_logger, set_log_level
from core.net_guard import OutboundURLRejected, validate_outbound_url
from core.paths import MEDIA_FOLDERS, get_path_resolver, validate_user_media_path
from services.library.storage_status import get_storage_report, invalidate_banner_cache

logger = get_module_logger("Routes.Settings")

SUPPORTED_CLIENTS = ('qbittorrent', 'sabnzbd', 'nzbget')

_VERSION_FILE = Path(__file__).parent.parent / "VERSION"
_APP_VERSION = _VERSION_FILE.read_text(encoding="utf-8").strip() if _VERSION_FILE.exists() else "1.0.0"


def _client_target(client_key: str, cfg: dict) -> tuple:
    """(host, port) a client config points at, normalized for comparison."""
    if client_key == 'qbittorrent':
        host, port = cfg.get('qb_host') or cfg.get('host'), cfg.get('qb_port') or cfg.get('port')
    else:
        host, port = cfg.get('host'), cfg.get('port')
    return (str(host or '').strip().lower(), str(port or '').strip())


def _prepare_client_test_config(client_key: str, candidate: dict, config_service) -> dict:
    """Make an unsaved client config safe to test.

    * The target host must pass the outbound-URL guard (no link-local /
      metadata / multicast addresses).
    * Masked or empty credentials are filled from the stored config only when
      the test targets the saved host; stored secrets are never sent to a
      different, user-typed host.
    """
    stored = (config_service.get_section(client_key) if config_service else None) or {}
    same_target = _client_target(client_key, candidate) == _client_target(client_key, stored)
    prepared = dict(candidate)
    for key, value in candidate.items():
        if is_sensitive_key(key) and (is_masked_value(value) or value in (None, '')):
            prepared[key] = stored.get(key, '') if same_target else ''

    host, port = _client_target(client_key, prepared)
    if host:
        validate_outbound_url(f"{host}:{port}" if port else host)
    return prepared


def test_client_connection(client_key: str, client_config: dict) -> dict:
    """Test a download client with the same config and client class the downloads use."""
    try:
        if client_key == 'qbittorrent':
            # Same config and client as the download pipeline, so a pass here means downloads will connect
            from integrations.download_clients.qbittorrent_client import QBittorrentClient
            from services.acquisition.client_selector import qbittorrent_config_from_section
            config = qbittorrent_config_from_section(client_config)
            result = QBittorrentClient(config).test_connection()
            if result.get('success'):
                method = 'API key' if result.get('auth_mode') == 'api_key' else 'username and password'
                message = f"qBittorrent connection successful (v{str(result.get('version') or '').lstrip('v')}, {method})"
                if result.get('auth_mode') == 'credentials':
                    message += ". Username and password sign-in is removed in AuralArchive 1.1; switch to an API key."
                return {'success': True, 'connected': True, 'message': message, 'type': client_key}
            return {'success': False, 'connected': False,
                    'message': result.get('error') or 'Connection failed', 'type': client_key}

        elif client_key == 'sabnzbd':
            from integrations.download_clients.sabnzbd_client import SABnzbdClient
            result = SABnzbdClient(client_config).test_connection()
            if result["success"]:
                version = result.get("version", "unknown")
                return {
                    'success': True,
                    'connected': True,
                    'message': f"SABnzbd connection successful (v{version})",
                    'type': client_key
                }
            else:
                return {
                    'success': False,
                    'connected': False,
                    'message': result.get("error") or "SABnzbd connection failed",
                    'type': client_key
                }

        elif client_key == 'nzbget':
            from integrations.download_clients.nzbget_client import NZBGetClient
            result = NZBGetClient(client_config).test_connection()
            if result["success"]:
                version = result.get("version", "unknown")
                return {
                    'success': True,
                    'connected': True,
                    'message': f"NZBGet connection successful (v{version})",
                    'type': client_key
                }
            else:
                return {
                    'success': False,
                    'connected': False,
                    'message': result.get("error") or "NZBGet connection failed",
                    'type': client_key
                }

        else:
            return {
                'success': False,
                'connected': False,
                'message': f"Unsupported client type: {client_key}",
                'type': client_key
            }

    except requests.exceptions.ConnectionError as e:
        return {
            'success': False,
            'connected': False,
            'message': f"Cannot connect to {client_key} server: {str(e)}",
            'type': client_key
        }
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'connected': False,
            'message': "Connection timeout",
            'type': client_key
        }
    except Exception:
        logger.warning('Download client connection test failed', exc_info=True)
        return {
            'success': False,
            'connected': False,
            'message': "Connection error",
            'type': client_key
        }


def _build_client_config_from_payload(client_key: str, payload: dict) -> dict:
    """Normalize client payload from UI into config-style dictionary."""
    normalized = {
        'enabled': payload.get('enabled', True),
        'auto_download': payload.get('auto_download', False)
    }

    if client_key == 'qbittorrent':
        raw_mappings = payload.get('path_mappings')
        mappings = []

        if isinstance(raw_mappings, list):
            for entry in raw_mappings:
                if not isinstance(entry, dict):
                    continue
                remote = str(entry.get('remote', '')).strip()
                local = str(entry.get('local', '')).strip()
                if remote or local:
                    mappings.append({'remote': remote, 'local': local})
        elif isinstance(raw_mappings, str) and raw_mappings.strip():
            for chunk in raw_mappings.split(';'):
                if '|' not in chunk:
                    continue
                remote, local = chunk.split('|', 1)
                remote = remote.strip()
                local = local.strip()
                if remote or local:
                    mappings.append({'remote': remote, 'local': local})

        normalized.update({
            'qb_host': payload.get('host') or payload.get('qb_host') or '127.0.0.1',
            'qb_port': int(payload.get('port', payload.get('qb_port', 8080)) or 8080),
            'qb_auth_mode': payload.get('auth_mode') or payload.get('qb_auth_mode') or 'credentials',
            'qb_api_key': payload.get('api_key') or payload.get('qb_api_key') or '',
            'qb_username': payload.get('username') or payload.get('qb_username') or '',
            'qb_password': payload.get('password') or payload.get('qb_password') or '',
            'category': payload.get('category') or payload.get('qb_category') or 'auralarchive'
        })
        # Only the selected sign-in method is kept, so the other can't be used by mistake
        if normalized['qb_auth_mode'] == 'api_key':
            normalized.update({'qb_username': '', 'qb_password': ''})
        else:
            normalized['qb_api_key'] = ''

        for index, mapping in enumerate(mappings, start=1):
            normalized[f'path_mapping_{index}_qb_path'] = mapping.get('remote', '')
            normalized[f'path_mapping_{index}_host_path'] = mapping.get('local', '')
    elif client_key == 'sabnzbd':
        normalized.update({
            'host': payload.get('host') or '127.0.0.1',
            'port': int(payload.get('port', 8080) or 8080),
            'api_key': payload.get('api_key') or '',
            'url_base': payload.get('url_base') or '/api',
            'use_ssl': bool(payload.get('use_ssl', False)),
            'verify_cert': bool(payload.get('verify_cert', True)),
            'category': payload.get('category') or '',
            'remote_path': payload.get('remote_path') or '',
            'local_path': payload.get('local_path') or '',
        })
    elif client_key == 'nzbget':
        normalized.update({
            'host': payload.get('host') or '127.0.0.1',
            'port': int(payload.get('port', 6789) or 6789),
            'username': payload.get('username') or '',
            'password': payload.get('password') or '',
            'use_ssl': bool(payload.get('use_ssl', False)),
            'verify_cert': bool(payload.get('verify_cert', True)),
            'category': payload.get('category') or '',
        })

    return normalized


from routes.settings_tools.optimize_database import handle_optimize_database
from routes.settings_tools.repair_database import handle_repair_database
from routes.settings_tools.get_config import handle_get_config
from routes.settings_tools.search_preferences import handle_get_search_preferences, handle_update_search_preferences

settings_bp = Blueprint('settings', __name__)


@settings_bp.route('/api/metadata-refresh', methods=['GET'])
def get_metadata_refresh_settings():
    """Scheduled metadata refresh settings, schedule and live progress."""
    try:
        return jsonify({'success': True, **container.get_metadata_refresh_scheduler().get_status()})
    except Exception as e:
        logger.error(f"Error reading metadata refresh status: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@settings_bp.route('/api/metadata-refresh', methods=['POST'])
def save_metadata_refresh_settings():
    """Save {enabled, authors_interval_days, series_interval_days,
    books_interval_days, request_delay_seconds}; intervals in days, 0 = off."""
    try:
        scheduler = container.get_metadata_refresh_scheduler()
        errors = scheduler.save_settings(request.get_json() or {})
        if errors:
            return jsonify({'success': False, 'message': '; '.join(errors.values()), 'field_errors': errors}), 400
        return jsonify({'success': True, 'message': 'Metadata refresh settings saved', **scheduler.get_status()})
    except Exception as e:
        logger.error(f"Error saving metadata refresh settings: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@settings_bp.route('/api/metadata-refresh/run', methods=['POST'])
def run_metadata_refresh():
    """Run one refresh job now. Body: {job: authors|series|books}."""
    try:
        from services.metadata.refresh import JOBS
        job = str((request.get_json() or {}).get('job') or '')
        if job not in JOBS:
            return jsonify({'success': False, 'error': f'Unknown job: {job}'}), 400
        scheduler = container.get_metadata_refresh_scheduler()
        state = scheduler.request_run(job)
        return jsonify({'success': True, 'state': state, **scheduler.get_status()})
    except Exception as e:
        logger.error(f"Error starting metadata refresh: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@settings_bp.route('/api/metadata-refresh/library/apply-missing', methods=['POST'])
def apply_blocked_missing_books():
    """User confirmed a library check result that was held back because it
    would mark too many books missing at once: re-run it without the limit."""
    try:
        scheduler = container.get_metadata_refresh_scheduler()
        state = scheduler.apply_blocked_missing()
        return jsonify({'success': True, 'state': state, **scheduler.get_status()})
    except Exception as e:
        logger.error(f"Error applying blocked missing books: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@settings_bp.route('/api/metadata-refresh/stop', methods=['POST'])
def stop_metadata_refresh():
    """Stop the running refresh after its current item and clear the queue."""
    try:
        scheduler = container.get_metadata_refresh_scheduler()
        stopping = scheduler.request_stop()
        return jsonify({'success': True, 'stopping': stopping, **scheduler.get_status()})
    except Exception as e:
        logger.error(f"Error stopping metadata refresh: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@settings_bp.route('/api/storage', methods=['GET'])
def get_storage_settings():
    """Every storage location with its health and any notices."""
    try:
        return jsonify({'success': True, **get_storage_report()})
    except Exception as e:
        logger.error(f"Error building storage report: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@settings_bp.route('/api/storage', methods=['POST'])
def save_storage_settings():
    """Save media folder locations to [paths]. Body: {library, downloads,
    import, work}; '' = built-in folder. Only folders present are touched."""
    try:
        data = request.get_json() or {}
        updates, field_errors = {}, {}
        for folder in MEDIA_FOLDERS:
            if folder not in data:
                continue
            value, error = validate_user_media_path(folder, str(data.get(folder) or ''))
            if error:
                field_errors[folder] = error
            elif value is not None:
                updates[f'paths.{folder}'] = value

        if field_errors:
            return jsonify({
                'success': False,
                'message': '; '.join(field_errors.values()),
                'field_errors': field_errors,
            }), 400

        if updates and not container.get_config_service().update_multiple_config(updates):
            return jsonify({'success': False, 'error': 'Failed to save configuration'}), 500

        invalidate_banner_cache()
        logger.info("Storage locations saved", extra={"updates": updates})
        if 'paths.library' in updates:
            try:
                container.get_metadata_refresh_scheduler().request_run('library')
            except Exception as exc:
                logger.warning(f"Could not queue library check after library change: {exc}")
        return jsonify({'success': True, 'message': 'Storage locations saved', **get_storage_report()})
    except Exception as e:
        logger.error(f"Error saving storage settings: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


# --- Main settings page ------------------------------------------------------

@settings_bp.route('/')
@login_required
def settings_page():
    """Display the modern professional settings page."""
    try:
        return render_template('settings.html',
                             title='Settings - AuralArchive',
                             app_version=_APP_VERSION)
    
    except Exception as e:
        logger.error(f"Error loading settings page: {e}", exc_info=True)
        return render_template('settings.html',
                             title='Settings - AuralArchive',
                             app_version=_APP_VERSION)

@settings_bp.route('/hidden-titles')
@login_required
def hidden_titles_page():
    """Display every hidden/unmonitored title across the library for bulk review."""
    return render_template('hidden_titles.html', title='Hidden Titles - AuralArchive')

# --- AJAX tab content routes -------------------------------------------------

def _get_sample_books_for_preview(limit: int = 3):
    """Return sample book metadata for naming template previews."""
    try:
        db_service = container.get_database_service()
        books = db_service.books.get_all_books()
    except Exception:
        logger.warning("Unable to fetch books for naming preview", exc_info=True)
        books = []

    if not books:
        return [
            {
                'title': 'The Name of the Wind',
                'author': 'Patrick Rothfuss',
                'series': 'The Kingkiller Chronicle',
                'series_number': '1',
                'year': 2007,
                'narrator': 'Nick Podehl',
                'asin': 'B002UZMLXM',
                'ASIN': 'B002UZMLXM',
                'publisher': 'DAW Books',
                'RuntimeLengthMin': 27610
            },
            {
                'title': 'Project Hail Mary',
                'author': 'Andy Weir',
                'series': None,
                'series_number': None,
                'year': 2021,
                'narrator': 'Ray Porter',
                'asin': 'B08G9PRS1K',
                'ASIN': 'B08G9PRS1K',
                'publisher': 'Audible Studios',
                'RuntimeLengthMin': 16420
            },
            {
                'title': 'Rhythm of War',
                'author': 'Brandon Sanderson',
                'series': 'The Stormlight Archive',
                'series_number': '4',
                'year': 2020,
                'narrator': 'Michael Kramer, Kate Reading',
                'asin': 'B086WP794Z',
                'ASIN': 'B086WP794Z',
                'publisher': 'Tor Books',
                'RuntimeLengthMin': 57910
            }
        ][:limit]

    sample_books = []
    for book in books[:limit]:
        asin_value = book.get('asin') or book.get('ASIN')

        authors = (
            book.get('AuthorName')
            or book.get('author')
            or book.get('Author')
            or book.get('authors')
        )

        if isinstance(authors, list):
            author_name = authors[0] if authors else 'Unknown Author'
        else:
            author_name = authors or 'Unknown Author'

        sample_books.append({
            'title': book.get('Title', book.get('title', 'Unknown Title')),
            'author': author_name,
            'series': book.get('SeriesName', book.get('series')),
            'series_number': book.get('book_number'),
            'year': book.get('release_date'),
            'narrator': book.get('narrator_name'),
            'asin': asin_value,
            'ASIN': asin_value,
            'publisher': book.get('publisher_name', book.get('publisher')),
            'RuntimeLengthMin': book.get('runtime_length_min') or book.get('RuntimeLengthMin')
        })

    return sample_books


# --- Download client management API routes -----------------------------------

@settings_bp.route('/api/clients', methods=['GET'])
def get_clients():
    """Get all configured download clients"""
    try:
        config_service = container.get_config_service()
        config_data = config_service.list_config()
        
        clients = {}
        
        qbittorrent_config = config_data.get('qbittorrent', {})
        qb_signed_in = (qbittorrent_config.get('qb_api_key') if qbittorrent_config.get('qb_auth_mode') == 'api_key'
                        else qbittorrent_config.get('qb_username'))
        if qbittorrent_config.get('qb_host') and qb_signed_in:
            clients['qbittorrent'] = {
                'name': 'qBittorrent',
                'type': 'qbittorrent',
                'configured': True,
                'host': qbittorrent_config.get('qb_host', ''),
                'port': qbittorrent_config.get('qb_port', '8082'),
                'username': qbittorrent_config.get('qb_username', ''),
                'auto_download': config_service.get_config_bool('qbittorrent', 'auto_download', False)
            }
        
        sabnzbd_config = config_data.get('sabnzbd', {})
        if sabnzbd_config.get('host') and sabnzbd_config.get('api_key'):
            clients['sabnzbd'] = {
                'name': 'SABnzbd',
                'type': 'sabnzbd',
                'configured': True,
                'host': sabnzbd_config.get('host', ''),
                'port': sabnzbd_config.get('port', '8080'),
                'api_key': sabnzbd_config.get('api_key', ''),
                'url_base': sabnzbd_config.get('url_base', '/sabnzbd/api'),
                'use_ssl': sabnzbd_config.get('use_ssl', False),
                'verify_cert': sabnzbd_config.get('verify_cert', True),
                'category': sabnzbd_config.get('category', ''),
                'remote_path': sabnzbd_config.get('remote_path', ''),
                'local_path': sabnzbd_config.get('local_path', ''),
                'auto_download': config_service.get_config_bool('sabnzbd', 'auto_download', False)
            }

        nzbget_config = config_data.get('nzbget', {})
        if nzbget_config.get('host') and nzbget_config.get('username'):
            clients['nzbget'] = {
                'name': 'NZBGet',
                'type': 'nzbget',
                'configured': True,
                'host': nzbget_config.get('host', ''),
                'port': nzbget_config.get('port', '6789'),
                'username': nzbget_config.get('username', ''),
                'use_ssl': nzbget_config.get('use_ssl', False),
                'verify_cert': nzbget_config.get('verify_cert', True),
                'category': nzbget_config.get('category', ''),
                'auto_download': config_service.get_config_bool('nzbget', 'auto_download', False)
            }

        return jsonify({
            'success': True,
            'clients': clients
        })
        
    except Exception as e:
        logger.error(f"Error getting clients: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500

@settings_bp.route('/api/clients/<client_key>/test', methods=['POST'])
def test_client(client_key):
    """Test a download client connection"""
    try:
        payload = request.get_json(silent=True) or {}
        config_service = container.get_config_service()

        if payload:
            client_config = _build_client_config_from_payload(client_key, payload)
            try:
                client_config = _prepare_client_test_config(client_key, client_config, config_service)
            except OutboundURLRejected as exc:
                return jsonify({
                    'success': False,
                    'test_result': {'message': f'Invalid client address: {exc}', 'status': 'invalid_host'},
                    'error': f'Invalid client address: {exc}'
                }), 400
        else:
            if not config_service:
                return jsonify({
                    'success': False,
                    'error': 'Config service not available'
                }), 500
            client_config = config_service.get_section(client_key)

        if not client_config or not client_config.get('enabled', True):
            return jsonify({
                'success': False,
                'test_result': {
                    'message': f'Client {client_key} not found or not enabled',
                    'status': 'not_configured'
                },
                'error': f'Client {client_key} not configured'
            }), 404

        test_result = test_client_connection(client_key, client_config)
        
        return jsonify({
            'success': True,
            'test_result': test_result,
            'message': f'{client_key.title()} connection test completed'
        })
        
    except Exception as e:
        logger.error(f"Error testing client {client_key}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500

@settings_bp.route('/api/clients/<client_key>', methods=['PUT'])
def update_client(client_key):
    """Update a download client configuration"""
    try:
        data = request.get_json() or {}

        if client_key not in SUPPORTED_CLIENTS:
            return jsonify({
                'success': False,
                'error': f'Invalid client: {client_key}'
            }), 400

        config_service = container.get_config_service()

        client_config = _build_client_config_from_payload(client_key, data)

        if not client_config:
            return jsonify({
                'success': False,
                'error': 'No configuration values supplied'
            }), 400

        if client_key == 'qbittorrent':
            config_parser = config_service.load_config()
            section_name = client_key.lower()

            preserved_values = {}
            if config_parser.has_section(section_name):
                for option, value in config_parser.items(section_name):
                    option_lower = option.lower()
                    if option_lower in {'download_path', 'download_path_remote', 'path_mappings'}:
                        continue
                    if option_lower.startswith('path_mapping_'):
                        continue
                    preserved_values[option_lower] = value

            merged_values = {**preserved_values}
            for key, value in client_config.items():
                if isinstance(value, (list, dict)):
                    continue
                if is_sensitive_key(key) and is_masked_value(value):
                    continue  # placeholder from the UI: keep the stored secret
                merged_values[key.lower()] = value

            if not config_service.replace_section(section_name, merged_values):
                return jsonify({
                    'success': False,
                    'error': 'Failed to update client configuration'
                }), 500
        else:
            if not config_service.update_section(client_key, client_config):
                return jsonify({
                    'success': False,
                    'error': 'Failed to update client configuration'
                }), 500


        logger.info(f"Updated {client_key} client configuration")
        
        return jsonify({
            'success': True,
            'message': f'{client_key.title()} configuration updated successfully'
        })
        
    except Exception as e:
        logger.error(f"Error updating client {client_key}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500

@settings_bp.route('/api/clients/<client_key>/toggle', methods=['POST'])
def toggle_client(client_key):
    """Toggle a download client's auto_download setting"""
    try:
        if client_key not in SUPPORTED_CLIENTS:
            return jsonify({
                'success': False,
                'error': f'Invalid client: {client_key}'
            }), 400

        config_service = container.get_config_service()

        current_auto_download = config_service.get_config_bool(client_key, 'auto_download', False)
        new_auto_download = not current_auto_download

        logger.info("Toggling %s auto_download from %s to %s", client_key, current_auto_download, new_auto_download)

        if not config_service.update_config(client_key, 'auto_download', new_auto_download):
            return jsonify({
                'success': False,
                'error': f'Failed to toggle {client_key} auto-download setting'
            }), 500

        logger.info("Successfully toggled %s auto_download = %s", client_key, new_auto_download)
        
        return jsonify({
            'success': True,
            'message': f'Client {client_key} auto-download {"enabled" if new_auto_download else "disabled"}',
            'auto_download': new_auto_download
        })
        
    except Exception as e:
        logger.error(f"Error toggling client {client_key}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500

@settings_bp.route('/api/clients/<client_key>', methods=['DELETE'])
def delete_client(client_key):
    """Delete a download client configuration"""
    try:
        if client_key not in SUPPORTED_CLIENTS:
            return jsonify({
                'success': False,
                'error': f'Invalid client: {client_key}'
            }), 400
        
        config_service = container.get_config_service()

        if config_service.remove_section(client_key):
            logger.info(f"Deleted {client_key} client configuration")
            return jsonify({
                'success': True,
                'message': f'{client_key.title()} client configuration deleted successfully'
            })

        return jsonify({
            'success': False,
            'error': f'Failed to delete {client_key} client configuration'
        }), 500
            
    except Exception as e:
        logger.error(f"Error deleting client {client_key}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500

# --- API route registration --------------------------------------------------


settings_bp.add_url_rule('/database/optimize', 'optimize_database', handle_optimize_database, methods=['POST'])
settings_bp.add_url_rule('/database/repair', 'repair_database', handle_repair_database, methods=['POST'])

settings_bp.add_url_rule('/config', 'get_config', handle_get_config, methods=['GET'])


@settings_bp.route('/config/update', methods=['POST'])
def update_configuration_values():
    """Persist configuration updates received from the settings UI."""
    try:
        payload = request.get_json(silent=True) or {}

        updates_map = payload.get('updates')
        if not updates_map and 'section' in payload:
            updates_map = {
                str(payload.get('section')): payload.get('values', {})
            }

        if not updates_map or not isinstance(updates_map, dict):
            return jsonify({
                'success': False,
                'error': 'No configuration changes supplied'
            }), 400

        def _normalize(value):
            if isinstance(value, bool):
                return 'true' if value else 'false'
            if value is None:
                return ''
            if isinstance(value, (int, float)):
                return str(value)
            return str(value)

        flattened_updates = {}
        touched_sections = set()

        for section, values in updates_map.items():
            if not section or not isinstance(values, dict):
                continue

            section_name = str(section).strip().lower()
            if not section_name:
                continue

            for key, value in values.items():
                if key is None:
                    continue

                option_name = str(key).strip().lower()
                if not option_name:
                    continue

                config_key = f"{section_name}.{option_name}"
                flattened_updates[config_key] = _normalize(value)
                touched_sections.add(section_name)

        if not flattened_updates:
            return jsonify({
                'success': False,
                'error': 'No valid configuration keys supplied'
            }), 400

        config_service = container.get_config_service()
        success = config_service.update_multiple_config(flattened_updates)

        if not success:
            return jsonify({
                'success': False,
                'error': 'Failed to persist configuration changes'
            }), 500

        logger.info("Updated configuration sections: %s", ', '.join(sorted(touched_sections)))

        new_log_level = flattened_updates.get('application.log_level')
        if new_log_level:
            try:
                applied = set_log_level(new_log_level)
                logger.info("Applied log level at runtime: %s", applied)
            except Exception as level_exc:
                logger.warning("Could not apply log level %s at runtime: %s", new_log_level, level_exc)

        return jsonify({
            'success': True,
            'updated': len(flattened_updates),
            'sections': sorted(touched_sections)
        })

    except Exception as exc:
        logger.error(f"Error updating configuration: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


# --- File naming routes ------------------------------------------------------

@settings_bp.route('/naming-templates', methods=['GET'])
def get_naming_templates():
    """Return available naming templates with preview examples."""
    try:
        file_naming_service = container.get_file_naming_service()
        templates = file_naming_service.get_available_templates()
        sample_books = _get_sample_books_for_preview()

        base_path = get_path_resolver().get_library_dir()

        template_payload = []
        label_map = {
            'simple': 'Simple',
            'simple_asin': 'Simple ASIN'
        }
        for template_name in templates:
            example_entries = []
            for book in sample_books:
                try:
                    preview_path = file_naming_service.generate_file_path(book, base_path, template_name, 'm4b')

                    example_entries.append({
                        'title': book.get('title'),
                        'author': book.get('author'),
                        'series': book.get('series'),
                        'preview': preview_path
                    })
                except Exception as preview_error:
                    logger.debug(f"Failed to build preview for template {template_name}: {preview_error}")
                    continue

            template_payload.append({
                'name': template_name,
                'label': label_map.get(template_name, template_name.replace('_', ' ').title()),
                'examples': example_entries
            })

        return jsonify({
            'success': True,
            'templates': template_payload
        })

    except Exception as exc:
        logger.error(f"Error loading naming templates: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


# --- Search management API endpoints -----------------------------------------

@settings_bp.route('/api/search/preferences', methods=['GET'])
def get_search_preferences():
    """Get search/quality preferences (search_preferences DB table)."""
    return handle_get_search_preferences()

@settings_bp.route('/api/search/preferences', methods=['POST'])
def update_search_preferences():
    """Update search/quality preferences (search_preferences DB table)."""
    return handle_update_search_preferences()

# --- Media management API routes ---------------------------------------------

@settings_bp.route('/api/media-management', methods=['GET'])
def get_media_management_config():
    """Get current media management configuration."""
    try:
        config_service = container.get_config_service()
        
        naming_config = config_service.get_section('naming')
        import_config = config_service.get_section('import')
        audible_config = config_service.get_section('audible')
        conversion_config = config_service.get_section('conversion')

        def coerce_bool(value, default=False):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {'true', '1', 'yes', 'on'}:
                    return True
                if lowered in {'false', '0', 'no', 'off'}:
                    return False
            return bool(default)

        def coerce_int(value, default=1):
            try:
                return int(value)
            except (TypeError, ValueError):
                return int(default)
        
        return jsonify({
            'success': True,
            'config': {
                'naming_template': naming_config.get('naming_template', 'simple'),
                'verify_after_import': import_config.get('verify_after_import', True),
                'create_backup_on_error': import_config.get('create_backup_on_error', True),
                'delete_source_after_import': import_config.get('delete_source_after_import', False),
                'overwrite_existing_files': coerce_bool(import_config.get('overwrite_existing_files'), False),
                'audible_downloads': {
                    'format': (audible_config.get('download_format') or 'aaxc') if audible_config else 'aaxc',
                    'quality': (audible_config.get('download_quality') or 'best') if audible_config else 'best',
                    'aax_fallback': coerce_bool(audible_config.get('aax_fallback_enabled'), True) if audible_config else True,
                    'save_voucher': coerce_bool(audible_config.get('save_voucher'), True) if audible_config else True,
                    'include_cover': coerce_bool(audible_config.get('include_cover'), False) if audible_config else False,
                    'include_chapters': coerce_bool(audible_config.get('include_chapters'), False) if audible_config else False,
                    'include_pdf': coerce_bool(audible_config.get('include_pdf'), False) if audible_config else False,
                    'concurrent_downloads': coerce_int(audible_config.get('concurrent_downloads'), 1) if audible_config else 1
                },
                'conversion': {
                    'convert_mp3_to_m4b': coerce_bool(conversion_config.get('convert_mp3_to_m4b'), False) if conversion_config else False,
                    'codec': (conversion_config.get('quality.codec') or 'aac') if conversion_config else 'aac',
                    'bitrate': (conversion_config.get('quality.bitrate') or '64k') if conversion_config else '64k',
                    'channels': (conversion_config.get('quality.channels') or '1') if conversion_config else '1',
                    'sample_rate': (conversion_config.get('quality.sample_rate') or '22050') if conversion_config else '22050'
                }
            }
        })
    except Exception as e:
        logger.error(f"Error getting media management config: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500

@settings_bp.route('/api/media-management', methods=['POST'])
def save_media_management_config():
    """Save media management configuration."""
    try:
        config_service = container.get_config_service()
        file_naming_service = container.get_file_naming_service()
        
        data = request.get_json() or {}

        def to_bool_str(value, default=False):
            return str(value if value is not None else default).lower()

        def to_int_str(value, default=1):
            try:
                return str(int(value))
            except (TypeError, ValueError):
                return str(default)

        # Only the groups a form sends are written, so one form's save can't reset another's
        updates = {}
        if 'naming_template' in data:
            template = data.get('naming_template') or 'simple'
            if template == 'custom':
                custom_template = data.get('custom_template', '')
                if not custom_template:
                    return jsonify({
                        'success': False,
                        'message': 'Custom template cannot be empty'
                    }), 400

                is_valid, errors = file_naming_service.validate_template(custom_template)
                if not is_valid:
                    return jsonify({
                        'success': False,
                        'message': 'Invalid custom template',
                        'errors': errors
                    }), 400

                template = custom_template
            updates['naming.naming_template'] = template

        for key, default in (('verify_after_import', True), ('create_backup_on_error', True),
                             ('delete_source_after_import', False), ('overwrite_existing_files', False)):
            if key in data:
                updates[f'import.{key}'] = to_bool_str(data.get(key), default)

        audible_payload = data.get('audible_downloads')
        if audible_payload:
            updates.update({
                'audible.download_format': (audible_payload.get('format') or 'aaxc'),
                'audible.download_quality': (audible_payload.get('quality') or 'best'),
                'audible.aax_fallback_enabled': to_bool_str(audible_payload.get('aax_fallback'), True),
                'audible.save_voucher': to_bool_str(audible_payload.get('save_voucher'), True),
                'audible.include_cover': to_bool_str(audible_payload.get('include_cover'), False),
                'audible.include_chapters': to_bool_str(audible_payload.get('include_chapters'), False),
                'audible.include_pdf': to_bool_str(audible_payload.get('include_pdf'), False),
                'audible.concurrent_downloads': to_int_str(audible_payload.get('concurrent_downloads'), 1),
            })

        conversion_payload = data.get('conversion')
        if conversion_payload:
            updates.update({
                'conversion.convert_mp3_to_m4b': to_bool_str(conversion_payload.get('convert_mp3_to_m4b'), False),
                'conversion.quality.codec': str(conversion_payload.get('codec') or 'aac'),
                'conversion.quality.bitrate': str(conversion_payload.get('bitrate') or '64k'),
                'conversion.quality.channels': str(conversion_payload.get('channels') or '1'),
                'conversion.quality.sample_rate': str(conversion_payload.get('sample_rate') or '22050'),
            })

        if not updates:
            return jsonify({'success': False, 'message': 'Nothing to save'}), 400

        success = config_service.update_multiple_config(updates)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Media management settings saved successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to save configuration'
            }), 500
        
    except Exception as e:
        logger.error(f"Error saving media management config: {e}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500

@settings_bp.route('/api/media-management/preview', methods=['POST'])
def preview_naming_template():
    """Generate preview examples for naming template."""
    try:
        file_naming_service = container.get_file_naming_service()

        data = request.get_json()
        template = data.get('template', 'simple')

        sample_books = _get_sample_books_for_preview()

        examples = []
        for book in sample_books:
            try:
                path = file_naming_service.generate_filename(book, template, 'm4b')
                examples.append({
                    'title': book['title'],
                    'author': book['author'],
                    'series': book.get('series'),
                    'path': path
                })
            except Exception as e:
                logger.warning(f"Error generating preview for {book['title']}: {e}")
                continue
        
        if not examples:
            return jsonify({
                'success': False,
                'message': 'Could not generate preview examples'
            }), 400
        
        return jsonify({
            'success': True,
            'examples': examples
        })
        
    except Exception as e:
        logger.error(f"Error generating preview: {e}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500

# --- Download management settings routes -------------------------------------

@settings_bp.route('/api/download-management', methods=['GET'])
def get_download_management_config():
    """Get current download management configuration."""
    try:
        config_service = container.get_config_service()
        
        dm_config = config_service.get_section('download_management')

        def get_bool(key: str, default: bool) -> bool:
            value = dm_config.get(key, default)
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            return str(value).strip().lower() == 'true'

        def get_int(key: str, default: int) -> int:
            value = dm_config.get(key, default)
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def get_str(key: str, default: str) -> str:
            value = dm_config.get(key, default)
            if value is None:
                return default
            return str(value)

        def get_float(key: str, default: float) -> float:
            value = dm_config.get(key, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        return jsonify({
            'success': True,
            'config': {
                'seeding_enabled': get_bool('seeding_enabled', True),

                'delete_source_after_import': get_bool('delete_source_after_import', False),


                'retry_search_max': get_int('retry_search_max', 3),
                'retry_download_max': get_int('retry_download_max', 2),
                'retry_conversion_max': get_int('retry_conversion_max', 1),
                'retry_import_max': get_int('retry_import_max', 2),
                'retry_backoff_minutes': get_int('retry_backoff_minutes', 30),

                'monitoring_interval': get_int('monitoring_interval', 2),
                'auto_start_monitoring': get_bool('auto_start_monitoring', True),
                'monitor_seeding': get_bool('monitor_seeding', True),

                'max_concurrent_downloads': get_int('max_concurrent_downloads', 3),
                'queue_priority_default': get_int('queue_priority_default', 5),

                'minimum_free_space': get_int('minimum_free_space', 100),
                'free_space_unit': get_str('free_space_unit', 'MB'),
                'skip_free_space_check': get_bool('skip_free_space_check', False),

                'ratio_protection_enabled': get_bool('ratio_protection_enabled', False),
                'ratio_floor': get_float('ratio_floor', 1.0),
                'prefer_freeleech': get_bool('prefer_freeleech', False),
                'hold_non_freeleech': get_bool('hold_non_freeleech', False),
                'freeleech_hold_days': get_int('freeleech_hold_days', 14),
                'duplicate_grab_protection': get_bool('duplicate_grab_protection', True)
            }
        })
    except Exception as e:
        logger.error(f"Error getting download management config: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@settings_bp.route('/api/download-management', methods=['POST'])
def save_download_management_config():
    """Save download management configuration."""
    try:
        config_service = container.get_config_service()
        data = request.get_json()
        
        updates = {
            'download_management.seeding_enabled': str(data.get('seeding_enabled', True)).lower(),
            
            'download_management.delete_source_after_import': str(data.get('delete_source_after_import', False)).lower(),
            
            'download_management.retry_search_max': str(data.get('retry_search_max', 3)),
            'download_management.retry_download_max': str(data.get('retry_download_max', 2)),
            'download_management.retry_conversion_max': str(data.get('retry_conversion_max', 1)),
            'download_management.retry_import_max': str(data.get('retry_import_max', 2)),
            'download_management.retry_backoff_minutes': str(data.get('retry_backoff_minutes', 30)),
            
            'download_management.monitoring_interval': str(data.get('monitoring_interval', 2)),
            'download_management.auto_start_monitoring': str(data.get('auto_start_monitoring', True)).lower(),
            'download_management.monitor_seeding': str(data.get('monitor_seeding', True)).lower(),
            
            'download_management.max_concurrent_downloads': str(data.get('max_concurrent_downloads', 3)),
            'download_management.queue_priority_default': str(data.get('queue_priority_default', 5)),

            'download_management.minimum_free_space': str(data.get('minimum_free_space', 100)),
            'download_management.free_space_unit': data.get('free_space_unit', 'MB'),
            'download_management.skip_free_space_check': str(data.get('skip_free_space_check', False)).lower(),

            'download_management.ratio_protection_enabled': str(data.get('ratio_protection_enabled', False)).lower(),
            'download_management.ratio_floor': str(data.get('ratio_floor', 1.0)),
            'download_management.prefer_freeleech': str(data.get('prefer_freeleech', False)).lower(),
            'download_management.hold_non_freeleech': str(data.get('hold_non_freeleech', False)).lower(),
            'download_management.freeleech_hold_days': str(data.get('freeleech_hold_days', 14)),
            'download_management.duplicate_grab_protection': str(data.get('duplicate_grab_protection', True)).lower()
        }
        
        success = config_service.update_multiple_config(updates)
        
        if success:

            return jsonify({
                'success': True,
                'message': 'Download management settings saved successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to save configuration'
            }), 500
        
    except Exception as e:
        logger.error(f"Error saving download management config: {e}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


logger.success("Settings module started successfully")