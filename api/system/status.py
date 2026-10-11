"""
Module Name: status.py
Author: AuralArchivist
Created: June 27, 2025
Last Modified: October 9, 2026
Description:
    Status Feed API that surfaces user-friendly operations/events feed for the
    UI. Provides a lightweight feed endpoint backed by the status service.

Location:
    /api/system/status.py

Endpoints:

GET
    /api/status/feed      Latest status events for UI consumption
    /api/status/summary   Return lightweight counts for the sidebar activity button
"""
from datetime import datetime

from flask import Blueprint, jsonify, request

from core.logging import get_module_logger
from services.container import container

logger = get_module_logger("API.System.Status")

status_api_bp = Blueprint('status_api', __name__, url_prefix='/api/status')


@status_api_bp.route('/feed', methods=['GET'])
def get_status_feed():
    """Return the latest curated status events for the UI."""
    try:
        service = container.get_status_service()
        if not service:
            return jsonify({'success': False, 'events': [], 'error': 'Status service unavailable'}), 503

        try:
            limit = int(request.args.get('limit', 20))
        except ValueError:
            limit = 20
        limit = max(1, min(limit, 100))

        events = service.get_events(limit=limit)
        return jsonify({
            'success': True,
            'events': events,
            'generated_at': datetime.utcnow().isoformat()
        })
    except Exception as exc:
        logger.error("Could not load status feed", exc_info=True)
        return jsonify({'success': False, 'events': [], 'error': str(exc)}), 500


@status_api_bp.route('/summary', methods=['GET'])
def get_status_summary():
    """Return lightweight counts for the sidebar activity button."""
    try:
        service = container.get_status_service()
        if not service:
            return jsonify({'success': False, 'running': 0, 'warnings': 0, 'errors': 0, 'latest_title': None}), 503

        events = service.get_events(limit=50)

        running = sum(1 for e in events if e.get('state') == 'running')
        warnings = sum(1 for e in events if e.get('state') not in ('running',) and e.get('level') == 'warning')
        errors = sum(1 for e in events if e.get('state') == 'failed')

        running_events = [e for e in events if e.get('state') == 'running']
        latest_title = None
        if running_events:
            latest_title = running_events[0].get('title')
        elif events:
            latest_title = events[0].get('title')

        return jsonify({
            'success': True,
            'running': running,
            'warnings': warnings,
            'errors': errors,
            'latest_title': latest_title,
            'generated_at': datetime.utcnow().isoformat(),
        })
    except Exception as exc:
        logger.error("Could not load status summary", exc_info=True)
        return jsonify({'success': False, 'running': 0, 'warnings': 0, 'errors': 0, 'latest_title': None, 'error': str(exc)}), 500
