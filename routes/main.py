"""
Module Name: main.py
Author: AuralArchivist
Created: July 23, 2025
Last Modified: October 9, 2026
Description:
    Dashboard routes for landing page, library stats API, and download snapshots.

Location:
    /routes/main.py
"""

from typing import Any, Dict, List

from flask import Blueprint, jsonify, render_template
from flask_login import login_required

from services.container import container
from core.logging import get_module_logger
from core.text import runtime_to_minutes

main_bp = Blueprint('main', __name__)
logger = get_module_logger("Routes.Main")

def _get_download_snapshot(limit: int = 3) -> Dict[str, Any]:
    """Fetch a small snapshot of active downloads."""
    snapshot: Dict[str, Any] = {'connected': False, 'downloads': [], 'error': None}
    try:
        config_service = container.get_config_service()
        if not config_service.get_config_bool('qbittorrent', 'enabled', False):
            snapshot['error'] = 'Download client not configured'
            return snapshot

        # The download pipeline's client: same sign-in, and its connection is reused
        selector = container.get_download_management_service().client_selector
        qb_client = selector.get_client('qbittorrent')
        if not qb_client:
            snapshot['error'] = 'Unable to connect to download client'
            return snapshot

        snapshot['connected'] = True
        torrents_result = qb_client.get_torrents()
        if torrents_result.get('success'):
            torrents = torrents_result.get('torrents', [])
            filtered = [
                torrent for torrent in torrents
                if (torrent.get('category') or '').lower() == 'auralarchive'
            ]
            filtered.sort(key=lambda item: item.get('added_on', 0) or 0, reverse=True)
            snapshot['downloads'] = filtered[:limit]
        else:
            snapshot['error'] = torrents_result.get('error', 'Unable to fetch downloads')

    except Exception as exc:
        snapshot['error'] = str(exc)
        logger.debug("Download snapshot unavailable: %s", exc)

    return snapshot


@main_bp.route('/api/dashboard/download-snapshot')
@login_required
def api_download_snapshot():
    """JSON endpoint - live download queue snapshot for dashboard refresh."""
    return jsonify(_get_download_snapshot(limit=4))


@main_bp.route('/')
@login_required
def index():
    """Dashboard landing page."""
    return dashboard()

@main_bp.route('/api/library/stats')
@login_required
def api_library_stats():
    """API endpoint for library statistics used by MediaVault JS."""
    try:
        db_service = container.get_database_service()
        books = db_service.books.get_all_books()
        
        authors = set()
        total_hours = 0
        
        for book in books:
            if book.get('Author'):
                authors.add(book['Author'])
            
            total_hours += runtime_to_minutes(book.get('Runtime')) / 60
        
        return jsonify({
            'totalBooks': len(books),
            'totalAuthors': len(authors),
            'totalHours': int(total_hours)
        })
    
    except Exception as e:
        logger.error(f"Error getting library stats: {e}", exc_info=True)
        return jsonify({
            'totalBooks': 0,
            'totalAuthors': 0,
            'totalHours': 0
        })

@main_bp.route('/dashboard')
@login_required
def dashboard():
    """Render the dashboard with recent activity."""
    library_stats: Dict[str, Any] = {}
    activity_stats: Dict[str, Any] = {}
    recent_books: List[Dict[str, Any]] = []

    try:
        db_service = container.get_database_service()
        library_stats = db_service.stats.get_library_stats() or {}
        activity_stats = db_service.stats.get_recent_activity_stats(7) or {}
        recent_books = db_service.books.get_recent_books(limit=6)
    except Exception as exc:
        logger.error(f"Error loading dashboard data: {exc}", exc_info=True)

    download_snapshot = _get_download_snapshot(limit=4)

    return render_template(
        'dashboard.html',
        title='Dashboard - AuralArchive',
        library_stats=library_stats,
        activity_stats=activity_stats,
        recent_books=recent_books,
        download_snapshot=download_snapshot
    )
