"""
Module Name: calendar.py
Author: AuralArchivist
Created: October 2, 2026
Last Modified: October 9, 2026
Description:
    Release calendar API: calendar entries, watches and settings.

Location:
    /api/discovery/calendar.py

Endpoints:

GET
    /api/calendar                    Entries between ?start= and ?end= (YYYY-MM-DD), optional ?watch=
    /api/calendar/watches            List watches
    /api/calendar/settings           Look-ahead/look-back settings and the job's schedule

POST
    /api/calendar/watches            Create a watch {kind: tag|keyword, query, category_id}
    /api/calendar/settings           Save look-ahead/look-back
    /api/calendar/refresh            Run the release calendar job now

PUT
    /api/calendar/watches/<id>       Update a watch

DELETE
    /api/calendar/watches/<id>       Delete a watch and its titles
"""

from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from core.logging import get_module_logger
from services.metadata.release_calendar import service as release_calendar
from services.container import container

calendar_api = Blueprint('calendar_api', __name__, url_prefix='/api/calendar')
logger = get_module_logger("API.Discovery.Calendar")

MAX_RANGE_DAYS = 400


def _parse_date(value, fallback):
    if not value:
        return fallback
    return date.fromisoformat(str(value))


@calendar_api.route('', methods=['GET'])
def get_entries():
    today = date.today()
    try:
        start = _parse_date(request.args.get('start'), today.replace(day=1))
        end = _parse_date(request.args.get('end'), start + timedelta(days=41))
        watch = request.args.get('watch', type=int)
    except ValueError:
        return jsonify({'success': False, 'error': 'Dates must be YYYY-MM-DD'}), 400
    if end < start or (end - start).days > MAX_RANGE_DAYS:
        return jsonify({'success': False, 'error': f'Range must be 0-{MAX_RANGE_DAYS} days'}), 400
    return jsonify({
        'success': True,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'entries': release_calendar.entries(start, end, watch),
        'watches': release_calendar.list_watches(),
    })


@calendar_api.route('/watches', methods=['GET'])
def list_watches():
    return jsonify({'success': True, 'watches': release_calendar.list_watches()})


@calendar_api.route('/watches', methods=['POST'])
def create_watch():
    watch, error = release_calendar.create_watch(request.get_json(silent=True) or {})
    if error:
        return jsonify({'success': False, 'error': error}), 400
    return jsonify({'success': True, 'watch': watch}), 201


@calendar_api.route('/watches/<int:watch_id>', methods=['PUT'])
def update_watch(watch_id):
    watch, error = release_calendar.update_watch(watch_id, request.get_json(silent=True) or {})
    if error:
        return jsonify({'success': False, 'error': error}), 404 if error == 'Watch not found' else 400
    return jsonify({'success': True, 'watch': watch})


@calendar_api.route('/watches/<int:watch_id>', methods=['DELETE'])
def delete_watch(watch_id):
    if not release_calendar.delete_watch(watch_id):
        return jsonify({'success': False, 'error': 'Watch not found'}), 404
    return jsonify({'success': True})


def _schedule():
    status = container.get_metadata_refresh_scheduler().get_status()
    return status.get('jobs', {}).get('calendar', {}), status.get('running') == 'calendar'


@calendar_api.route('/settings', methods=['GET'])
def get_settings():
    job, running = _schedule()
    return jsonify({'success': True, 'settings': release_calendar.get_settings(), 'job': job, 'running': running})


@calendar_api.route('/settings', methods=['POST'])
def save_settings():
    errors = release_calendar.save_settings(request.get_json(silent=True) or {})
    if errors:
        return jsonify({'success': False, 'errors': errors, 'error': next(iter(errors.values()))}), 400
    release_calendar.prune()
    return jsonify({'success': True, 'settings': release_calendar.get_settings()})


@calendar_api.route('/refresh', methods=['POST'])
def refresh():
    if not release_calendar.list_watches(enabled_only=True):
        return jsonify({'success': False, 'error': 'Add a watch first'}), 400
    state = container.get_metadata_refresh_scheduler().request_run('calendar')
    return jsonify({'success': True, 'state': state})
