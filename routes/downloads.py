"""
Module Name: downloads.py
Author: AuralArchivist
Created: July 20, 2025
Last Modified: October 9, 2026
Description:
    Downloads dashboard routes for pipeline status and recent import activity.

Location:
    /routes/downloads.py
"""

from flask import Blueprint, render_template
from flask_login import login_required

from services.container import container
from core.logging import get_module_logger

downloads_bp = Blueprint('downloads', __name__)

logger = get_module_logger("Routes.Downloads")


@downloads_bp.route('')
@downloads_bp.route('/')
@login_required
def downloads_page():
    """Render the downloads dashboard with pipeline and streaming activity."""
    dm_service = container.get_download_management_service()

    pipeline_items = dm_service.get_queue(limit=50)
    service_status = dm_service.get_service_status()

    def _recent_completed(max_items: int = 5):
        statuses = ['IMPORTED', 'SEEDING', 'SEEDING_COMPLETE']
        collected = []
        for status in statuses:
            try:
                collected.extend(dm_service.get_queue(status_filter=status))
            except Exception as exc:
                logger.warning("Failed to fetch recent items for status %s: %s", status, exc)
        collected.sort(
            key=lambda item: (
                (item.get('completed_at') or item.get('updated_at') or item.get('queued_at') or ''),
            ),
            reverse=True,
        )
        return collected[:max_items]

    completed_items = _recent_completed()

    return render_template(
        'downloads.html',
        title='Downloads',
        pipeline_items=pipeline_items,
        service_status=service_status,
        completed_items=completed_items
    )
