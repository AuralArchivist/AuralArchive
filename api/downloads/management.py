"""
Module Name: management.py
Author: AuralArchivist
Created: June 30, 2025
Last Modified: October 9, 2026
Description:
    REST API for download queue management and service control. Supports queue
    CRUD, pause/resume, stage-aware retry of failed items, bulk operations,
    statistics, and monitoring controls across the configured download
    clients (qBittorrent, SABnzbd, NZBGet) and the Audible pipeline.

Location:
    /api/downloads/management.py

Endpoints:

GET
    /api/downloads/history                      Recent imported and seeding items, newest first
    /api/downloads/queue                        Get all queue items
    /api/downloads/queue/<download_id>          Get specific download
    /api/downloads/status                       Get service status

POST
    /api/downloads/queue                        Add book to download queue
    /api/downloads/queue/<download_id>/pause    Pause download
    /api/downloads/queue/<download_id>/resume   Resume download
    /api/downloads/queue/<download_id>/retry    Retry failed download
    /api/downloads/queue/bulk/cancel            Cancel multiple downloads
    /api/downloads/queue/bulk/retry             Retry multiple downloads
    /api/downloads/queue/clear                  Clear queue entries
    /api/downloads/service/start                Start monitoring service
    /api/downloads/service/stop                 Stop monitoring service

DELETE
    /api/downloads/queue/<download_id>          Cancel/remove download
    /api/downloads/queue/<download_id>/remove   Permanently remove a download from the queue
"""

from typing import Any, Dict, Optional

from flask import Blueprint, request, jsonify

from services.container import container
from core.logging import get_module_logger

logger = get_module_logger("API.Download.Management")

MAX_BULK_IDS = 500

_RETRY_TARGETS = {
    'SEARCH_FAILED': 'QUEUED',
    'DOWNLOAD_FAILED': 'QUEUED',
    'AUDIBLE_DOWNLOAD_FAILED': 'QUEUED',
    'FAILED': 'QUEUED',
    'CONVERSION_FAILED': 'COMPLETE',
    'IMPORT_FAILED': None,
}
RETRYABLE_STATUSES = tuple(_RETRY_TARGETS)


def _retry_target_status(download: Dict[str, Any]) -> Optional[str]:
    """Return the status a failed download should be reset to, or None if not retryable."""
    status = download.get('status')
    if status not in _RETRY_TARGETS:
        return None
    if status == 'IMPORT_FAILED':
        return 'CONVERTED' if download.get('converted_file_path') else 'COMPLETE'
    return _RETRY_TARGETS[status]


def _reset_for_retry(dm_service, download: Dict[str, Any], target_status: str) -> None:
    dm_service.queue_manager.update_download(download['id'], {
        'status': target_status,
        'retry_count': 0,
        'next_retry_at': None,
        'last_error': None
    })
    dm_service.event_emitter.emit_state_changed(
        download['id'],
        target_status,
        'Manual retry initiated'
    )


def _parse_int_arg(name: str, default: int, minimum: int, maximum: int) -> int:
    """Parse an integer query arg, falling back to default and clamping to range."""
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _parse_id_list(raw: Any) -> Optional[list]:
    """Validate a client-supplied download_ids list; None if malformed."""
    if not isinstance(raw, list) or len(raw) > MAX_BULK_IDS:
        return None
    try:
        return [int(item) for item in raw]
    except (TypeError, ValueError):
        return None

download_management_bp = Blueprint('download_management', __name__)


# --- Queue management endpoints ----------------------------------------------

@download_management_bp.route('/queue', methods=['POST'])
def add_to_queue():
    """Add a book to the download queue.

    Request JSON:
        {
            "book_asin": "B00XXXXXX",          # Required: Book ASIN
            "search_result_id": 123,            # Optional: Pre-selected search result
            "priority": 5,                      # Optional: Queue priority (1-10, default 5)
            "seeding_enabled": true,            # Optional: Override seeding config
            "delete_source": false              # Optional: Override delete source config
        }

    Returns:
        {
            "success": true,
            "download_id": 123,
            "message": "Added to queue"
        }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No JSON data provided'
            }), 400
        
        book_asin = data.get('book_asin')
        if not book_asin:
            return jsonify({
                'success': False,
                'error': 'book_asin is required'
            }), 400
        
        dm_service = container.get_download_management_service()

        result = dm_service.add_to_queue(
            book_asin,
            search_result_id=data.get('search_result_id'),
            priority=data.get('priority', 5),
            seeding_enabled=data.get('seeding_enabled'),
            delete_source=data.get('delete_source')
        )

        return jsonify(result), 200 if result.get('success') else 400

    except Exception as e:
        logger.error(f"Error adding to download queue: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500

@download_management_bp.route('/queue', methods=['GET'])
def get_queue():
    """Get all download queue items with optional filtering.

    Query Parameters:
        - status: Filter by status (e.g., QUEUED, DOWNLOADING, COMPLETE)
        - limit: Maximum number of results (default: 100)
        - offset: Pagination offset (default: 0)

    Returns:
        {
            "success": true,
            "downloads": [
                {
                    "id": 123,
                    "book_asin": "B00XXXXXX",
                    "status": "DOWNLOADING",
                    "progress": 45,
                    "created_at": "2025-10-28T10:30:00",
                    ...
                }
            ],
            "total": 25,
            "limit": 100,
            "offset": 0
        }
    """
    try:
        status_filter = request.args.get('status')
        limit = _parse_int_arg('limit', 100, 1, 1000)
        offset = _parse_int_arg('offset', 0, 0, 1_000_000)
        
        dm_service = container.get_download_management_service()
        
        queue_items = dm_service.get_queue(
            status_filter=status_filter,
            limit=limit,
            offset=offset
        )
        
        return jsonify({
            'success': True,
            'downloads': queue_items,
            'total': len(queue_items),
            'limit': limit,
            'offset': offset
        })
        
    except Exception as e:
        logger.error(f"Error getting download queue: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/history', methods=['GET'])
def get_history():
    """
    Get recent history items (IMPORTED, SEEDING, SEEDING_COMPLETE) sorted by
    most recent completion time, with cover images resolved.

    Query Parameters:
    - limit: Maximum number of results (default: 10)
    """
    try:
        limit = _parse_int_arg('limit', 10, 1, 500)
        dm_service = container.get_download_management_service()

        history_statuses = ['IMPORTED', 'SEEDING', 'SEEDING_COMPLETE']
        collected = []
        for status in history_statuses:
            try:
                collected.extend(dm_service.get_queue(status_filter=status))
            except Exception as exc:
                logger.warning("Failed to fetch history for status %s: %s", status, exc)

        collected.sort(
            key=lambda item: (
                item.get('completed_at') or item.get('updated_at') or item.get('queued_at') or ''
            ),
            reverse=True,
        )

        return jsonify({
            'success': True,
            'downloads': collected[:limit],
            'total': len(collected),
        })

    except Exception as e:
        logger.error(f"Error getting download history: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@download_management_bp.route('/queue/<int:download_id>', methods=['GET'])
def get_download(download_id: int):
    """Get details for a specific download.

    Returns:
        {
            "success": true,
            "download": {
                "id": 123,
                "book_asin": "B00XXXXXX",
                "status": "DOWNLOADING",
                "progress": 45,
                "download_speed": "2.5 MB/s",
                "eta": "5 minutes",
                ...
            }
        }
    """
    try:
        dm_service = container.get_download_management_service()
        download = dm_service.queue_manager.get_download(download_id)
        
        if not download:
            return jsonify({
                'success': False,
                'error': 'Download not found'
            }), 404
        
        return jsonify({
            'success': True,
            'download': download
        })
        
    except Exception as e:
        logger.error(f"Error getting download {download_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/queue/<int:download_id>', methods=['DELETE'])
def cancel_download(download_id: int):
    """Cancel and remove a download from the queue.

    Returns:
        {
            "success": true,
            "message": "Download cancelled"
        }
    """
    try:
        dm_service = container.get_download_management_service()
        result = dm_service.cancel_download(download_id)
        
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
        
    except Exception as e:
        logger.error(f"Error cancelling download {download_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/queue/<int:download_id>/remove', methods=['DELETE'])
def remove_download(download_id: int):
    """Permanently remove a single download from the queue (e.g. a failed
    download), without affecting any other items in the queue.

    Returns:
        {
            "success": true,
            "message": "Download removed from queue"
        }
    """
    try:
        dm_service = container.get_download_management_service()
        result = dm_service.remove_download(download_id)

        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400

    except Exception as e:
        logger.error(f"Error removing download {download_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/queue/<int:download_id>/pause', methods=['POST'])
def pause_download(download_id: int):
    """Pause an active download.

    Note: Only works during DOWNLOADING state.

    Returns:
        {
            "success": true,
            "message": "Download paused"
        }
    """
    try:
        dm_service = container.get_download_management_service()
        result = dm_service.pause_download(download_id)
        
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
        
    except Exception as e:
        logger.error(f"Error pausing download {download_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/queue/<int:download_id>/resume', methods=['POST'])
def resume_download(download_id: int):
    """Resume a paused download.

    Returns:
        {
            "success": true,
            "message": "Download resumed"
        }
    """
    try:
        dm_service = container.get_download_management_service()
        result = dm_service.resume_download(download_id)
        
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
        
    except Exception as e:
        logger.error(f"Error resuming download {download_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/queue/<int:download_id>/retry', methods=['POST'])
def retry_download(download_id: int):
    """Retry a failed download.

    Resets retry counters and moves download back to appropriate stage.

    Returns:
        {
            "success": true,
            "message": "Download retry initiated"
        }
    """
    try:
        dm_service = container.get_download_management_service()
        
        download = dm_service.queue_manager.get_download(download_id)
        if not download:
            return jsonify({
                'success': False,
                'error': 'Download not found'
            }), 404
        
        target_status = _retry_target_status(download)
        if not target_status:
            return jsonify({
                'success': False,
                'error': f"Cannot retry download in {download['status']} state"
            }), 400

        _reset_for_retry(dm_service, download, target_status)
        
        return jsonify({
            'success': True,
            'message': 'Download retry initiated'
        })
        
    except Exception as e:
        logger.error(f"Error retrying download {download_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


# --- Status & statistics endpoints -------------------------------------------

@download_management_bp.route('/status', methods=['GET'])
def get_service_status():
    """Get download management service status.

    Returns:
        {
            "success": true,
            "status": {
                "monitoring_active": true,
                "polling_interval": 2,
                "queue_statistics": {
                    "total": 25,
                    "queued": 5,
                    "downloading": 3,
                    "complete": 12,
                    "failed": 2
                },
                "active_downloads": 3
            }
        }
    """
    try:
        dm_service = container.get_download_management_service()
        status = dm_service.get_service_status()
        
        return jsonify({
            'success': True,
            'status': status
        })
        
    except Exception as e:
        logger.error(f"Error getting service status: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


# --- Service control endpoints -----------------------------------------------

@download_management_bp.route('/service/start', methods=['POST'])
def start_monitoring():
    """Start the download monitoring service.

    Returns:
        {
            "success": true,
            "message": "Monitoring service started"
        }
    """
    try:
        dm_service = container.get_download_management_service()
        dm_service.start_monitoring()
        
        return jsonify({
            'success': True,
            'message': 'Monitoring service started'
        })
        
    except Exception as e:
        logger.error(f"Error starting monitoring service: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/service/stop', methods=['POST'])
def stop_monitoring():
    """Stop the download monitoring service.

    Note: Active downloads will not be monitored until service is restarted.

    Returns:
        {
            "success": true,
            "message": "Monitoring service stopped"
        }
    """
    try:
        dm_service = container.get_download_management_service()
        dm_service.stop_monitoring()
        
        return jsonify({
            'success': True,
            'message': 'Monitoring service stopped'
        })
        
    except Exception as e:
        logger.error(f"Error stopping monitoring service: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


# --- Bulk operations ---------------------------------------------------------

@download_management_bp.route('/queue/bulk/cancel', methods=['POST'])
def bulk_cancel():
    """Cancel multiple downloads.

    Request JSON:
        {
            "download_ids": [123, 124, 125]
        }

    Returns:
        {
            "success": true,
            "results": {
                "123": {"success": true, "message": "Download cancelled"},
                "124": {"success": false, "message": "Download not found"},
                ...
            }
        }
    """
    try:
        data = request.get_json(silent=True) or {}
        download_ids = _parse_id_list(data.get('download_ids'))

        if not download_ids:
            return jsonify({
                'success': False,
                'error': f'download_ids must be a non-empty array of up to {MAX_BULK_IDS} integers'
            }), 400
        
        dm_service = container.get_download_management_service()
        results = {}
        
        for download_id in download_ids:
            try:
                result = dm_service.cancel_download(download_id)
                results[str(download_id)] = result
            except Exception:
                logger.exception("Bulk cancel failed for download %s", download_id)
                results[str(download_id)] = {
                    'success': False,
                    'error': 'Internal error (details in server log)'
                }
        
        return jsonify({
            'success': True,
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Error in bulk cancel: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/queue/bulk/retry', methods=['POST'])
def bulk_retry():
    """Retry multiple failed downloads.

    Request JSON:
        {
            "download_ids": [123, 124, 125]  # Optional: retry specific IDs
        }

        If no IDs provided, retries all failed downloads.

    Returns:
        {
            "success": true,
            "retried": 5,
            "results": {...}
        }
    """
    try:
        data = request.get_json(silent=True) or {}
        raw_ids = data.get('download_ids')
        dm_service = container.get_download_management_service()

        if raw_ids:
            download_ids = _parse_id_list(raw_ids)
            if download_ids is None:
                return jsonify({
                    'success': False,
                    'error': f'download_ids must be an array of up to {MAX_BULK_IDS} integers'
                }), 400
        else:
            download_ids = []
            for status in RETRYABLE_STATUSES:
                download_ids.extend(d['id'] for d in dm_service.queue_manager.get_queue(status_filter=status))
        
        results = {}
        success_count = 0
        
        for download_id in download_ids:
            try:
                download = dm_service.queue_manager.get_download(download_id)
                target_status = _retry_target_status(download) if download else None
                if target_status:
                    _reset_for_retry(dm_service, download, target_status)
                    results[str(download_id)] = {'success': True, 'message': 'Retry initiated'}
                    success_count += 1
                else:
                    results[str(download_id)] = {
                        'success': False,
                        'message': 'Not in a retryable failed state'
                    }
            except Exception:
                logger.exception("Bulk retry failed for download %s", download_id)
                results[str(download_id)] = {
                    'success': False,
                    'error': 'Internal error (details in server log)'
                }
        
        return jsonify({
            'success': True,
            'retried': success_count,
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Error in bulk retry: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


@download_management_bp.route('/queue/clear', methods=['POST'])
def clear_queue():
    """
    Clear download queue entries for recovery.

    Request JSON (optional):
    {
        "include_active": true,        # Include active DOWNLOADING items
        "include_imported": false,     # Also remove imported history entries
        "statuses": ["FAILED", ...]   # Custom statuses to clear
    }
    """
    try:
        data = request.get_json() or {}
        include_active = bool(data.get('include_active', False))
        include_imported = bool(data.get('include_imported', False))
        statuses = data.get('statuses')

        if statuses is not None and not isinstance(statuses, list):
            return jsonify({
                'success': False,
                'error': 'statuses must be an array'
            }), 400

        dm_service = container.get_download_management_service()
        result = dm_service.clear_queue(
            include_active=include_active,
            include_imported=include_imported,
            statuses=statuses
        )

        status_code = 200 if result.get('success', False) else 400
        return jsonify(result), status_code

    except Exception as e:
        logger.error(f"Error clearing download queue: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error (details in server log)'
        }), 500


# --- Cleanup endpoints -------------------------------------------------------

logger.info("Download Management API initialized")
