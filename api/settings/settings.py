"""
Module Name: settings.py
Author: AuralArchivist
Created: August 9, 2025
Last Modified: October 10, 2026
Description:
    Settings JSON API: live log tail, log download, update check, and
    performance metrics. The settings pages' configuration CRUD lives in
    routes/settings.py and routes/settings_tools/.

Location:
    /api/settings/settings.py

Endpoints:

GET
    /settings/api/logs/download          Download log file
    /settings/api/logs/latest            Live log tail (current session)

POST
    /settings/api/system/check-updates   Check GitHub releases for a newer version
"""

import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

import requests
from flask import Blueprint, jsonify, request, send_file, current_app

from core.logging import get_module_logger
from core import http

settings_api_bp = Blueprint('settings_api', __name__, url_prefix='/settings/api')


logger = get_module_logger("API.Settings")

_metrics_history: deque = deque(maxlen=60)

_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"
_CURRENT_VERSION = _VERSION_FILE.read_text(encoding="utf-8").strip() if _VERSION_FILE.exists() else "unknown"
_GITHUB_RELEASES_URL = "https://api.github.com/repos/AuralArchivist/AuralArchive/releases?per_page=1"

_update_check_cache: Dict[str, Any] = {}

MAX_LOG_TAIL_LINES = 800


def _version_key(version: str) -> Tuple[Tuple[int, ...], int]:
    """Sortable form of '1.2.0' or '1.2.0-Alpha'; a pre-release sorts before its release."""
    match = re.match(r'v?(\d+(?:\.\d+)*)(.*)', version.strip())
    if not match:
        return (), 0
    numbers = [int(part) for part in match.group(1).split('.')]
    while numbers and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers), 0 if match.group(2) else 1


def get_log_file_path() -> str:
    """Resolve the active log file path used by the application logger."""
    log_file_name = current_app.config.get('LOG_FILE', 'auralarchive_web.log')
    project_root = current_app.root_path

    logs_dir = os.path.join(project_root, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    preferred_path = os.path.join(logs_dir, log_file_name)
    if os.path.exists(preferred_path):
        return preferred_path

    fallback_path = os.path.join(project_root, log_file_name)
    if os.path.exists(fallback_path):
        return fallback_path

    return preferred_path


@settings_api_bp.route('/logs/latest')
def get_latest_logs():
    """Return the current server session's log tail for the live log window.

    Stateless by design: it always reads the session's log lines (from the
    session-start byte offset to EOF, capped to the last ``MAX_LOG_TAIL_LINES``)
    rather than advancing a shared cursor. This means a page reload re-renders
    the full session history instead of only the entries that arrive after the
    page is opened. When the client passes ``?after=<timestamp>`` (the last
    entry it already has) only newer entries are returned, so ongoing polls stay
    cheap while history is retained across reloads and operations.
    """
    try:
        from core.logging.config import get_session_log_info
        log_file, session_start = get_session_log_info()
        if not log_file:
            log_file = get_log_file_path()
            session_start = 0
        if session_start is None:
            session_start = 0

        after = request.args.get('after')

        new_logs = []
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as log_handle:
                log_handle.seek(0, 2)
                file_size = log_handle.tell()
                start = session_start if session_start <= file_size else 0
                log_handle.seek(start)
                lines = log_handle.readlines()

            if len(lines) > MAX_LOG_TAIL_LINES:
                lines = lines[-MAX_LOG_TAIL_LINES:]

            for line in lines:
                if not line.strip():
                    continue
                log_entry = parse_log_line(line.strip())
                if not log_entry:
                    continue
                if after and (log_entry.get('timestamp') or '') <= after:
                    continue
                new_logs.append(log_entry)

        return jsonify({'success': True, 'logs': new_logs})

    except Exception as e:
        logger.error(f"Error getting latest logs: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)', 'logs': []})


def parse_log_line(line):
    """Parse a log line into structured data."""
    if ' - ' in line:
        parts = line.split(' - ')
        if len(parts) >= 4:
            timestamp = parts[0].strip()
            level = parts[1].strip()
            logger_name = parts[2].strip()
            message = ' - '.join(parts[3:]).strip()

            return {
                'timestamp': timestamp,
                'source': logger_name,
                'level': level.lower(),
                'message': message
            }
        elif len(parts) >= 3:
            timestamp = parts[0].strip()
            level = parts[1].strip()
            message = ' - '.join(parts[2:]).strip()

            return {
                'timestamp': timestamp,
                'level': level.lower(),
                'message': message
            }

    return {
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'level': 'info',
        'message': line
    }


@settings_api_bp.route('/logs/download')
def download_logs():
    """Download log file."""
    try:
        log_file = get_log_file_path()
        if os.path.exists(log_file):
            return send_file(log_file, as_attachment=True, 
                           download_name=f'auralarchive_logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
        else:
            return jsonify({'error': 'Log file not found'}), 404
    
    except Exception as e:
        logger.error(f"Error downloading logs: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500


@settings_api_bp.route('/system/check-updates', methods=['POST'])
def check_for_updates():
    """Check GitHub releases for a newer version, refreshing the cache."""
    result = run_update_check()
    status = 200 if result.get("success") else 500
    return jsonify(result), status


def run_update_check() -> Dict[str, Any]:
    """Fetch the latest release from GitHub, update _update_check_cache, and return the result.

    Safe to call from background greenlets as well as the request handler.
    """
    global _update_check_cache
    try:
        resp = http.get(
            _GITHUB_RELEASES_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )

        if resp.status_code == 404:
            result: Dict[str, Any] = {
                "success": True,
                "updates_available": False,
                "current_version": _CURRENT_VERSION,
                "latest_version": _CURRENT_VERSION,
                "message": "No releases published yet - you are on the latest build",
                "checked_at": datetime.now().isoformat(),
            }
            _update_check_cache = result
            return result

        resp.raise_for_status()
        releases = resp.json()
        if not releases:
            result = {
                "success": True,
                "updates_available": False,
                "current_version": _CURRENT_VERSION,
                "latest_version": _CURRENT_VERSION,
                "message": "No releases published yet - you are on the latest build",
                "checked_at": datetime.now().isoformat(),
            }
            _update_check_cache = result
            return result

        data = releases[0]
        latest_version = data.get("tag_name", _CURRENT_VERSION).lstrip("v")
        updates_available = _version_key(latest_version) > _version_key(_CURRENT_VERSION)
        result = {
            "success": True,
            "updates_available": updates_available,
            "current_version": _CURRENT_VERSION,
            "latest_version": latest_version,
            "release_url": data.get("html_url", ""),
            "release_name": data.get("name", ""),
            "message": f"Update available: {latest_version}" if updates_available else "You are on the latest version",
            "checked_at": datetime.now().isoformat(),
        }
        _update_check_cache = result
        if updates_available:
            from services.notifications.events import update_available
            update_available(result)
        logger.info(
            "Update check complete",
            extra={"current": _CURRENT_VERSION, "latest": latest_version, "updates_available": updates_available},
        )
        return result

    except requests.RequestException as e:
        logger.warning(f"Could not reach GitHub to check for updates: {e.__class__.__name__}")
        return {"success": False, "error": 'Could not reach GitHub to check for updates'}
    except Exception as e:
        logger.error(f"Error checking for updates: {e}", exc_info=True)
        return {"success": False, "error": 'Internal error (details in server log)'}
