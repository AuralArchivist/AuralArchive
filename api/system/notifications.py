"""
Module Name: notifications.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 9, 2026
Description:
    JSON API for the header notification centre (bell): list, mark read,
    dismiss. Notifications are raised by services (see services/notifications).

Location:
    /api/system/notifications.py

Endpoints:

GET
    /api/notifications                             List notifications and unread count

POST
    /api/notifications/<notification_id>/dismiss   Dismiss a notification
    /api/notifications/<notification_id>/read      Mark a notification as read
    /api/notifications/dismiss-all                 Dismiss all notifications
    /api/notifications/read-all                    Mark all notifications as read
"""

from flask import Blueprint, jsonify, request

from core.logging import get_module_logger
from services import notifications

logger = get_module_logger("API.Notifications")

notifications_api_bp = Blueprint('notifications_api', __name__, url_prefix='/api/notifications')


def _state(**extra):
    return jsonify({'success': True, 'unread': notifications.unread_count(), **extra})


@notifications_api_bp.route('', methods=['GET'])
def list_notifications():
    try:
        limit = request.args.get('limit', 50, type=int)
        return _state(items=notifications.list_notifications(limit))
    except Exception as exc:
        logger.error("Failed to list notifications: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@notifications_api_bp.route('/<int:notification_id>/read', methods=['POST'])
def mark_read(notification_id: int):
    notifications.mark_read(notification_id)
    return _state()


@notifications_api_bp.route('/read-all', methods=['POST'])
def mark_all_read():
    notifications.mark_all_read()
    return _state()


@notifications_api_bp.route('/<int:notification_id>/dismiss', methods=['POST'])
def dismiss(notification_id: int):
    notifications.dismiss(notification_id)
    return _state()


@notifications_api_bp.route('/dismiss-all', methods=['POST'])
def dismiss_all():
    notifications.dismiss_all()
    return _state()
