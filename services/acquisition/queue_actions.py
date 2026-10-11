"""
Module Name: queue_actions.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Download queue actions on the download service: add, list, cancel,
    remove, pause, resume and clear.

Location:
    /services/acquisition/queue_actions.py
"""

from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from integrations.audible.ownership_validator import (
    assess_audible_ownership,
    fetch_audible_library_entry,
    merge_audible_records,
)
from services.container import container

_LOGGER = get_module_logger("Service.DownloadManagement.Service")


class QueueActionsMixin:
    """Queue actions used by the API and other services. Mixed into DownloadManagementService."""

    CLEARABLE_STATUSES: List[str] = [
        'QUEUED', 'SEARCHING', 'FOUND', 'DOWNLOADING', 'AUDIBLE_DOWNLOADING',
        'AUDIBLE_DOWNLOAD_FAILED', 'DOWNLOAD_COMPLETE', 'COMPLETE', 'CONVERTING',
        'CONVERTED', 'PROCESSING', 'PROCESSED', 'IMPORTING', 'IMPORTED',
        'SEARCH_FAILED', 'DOWNLOAD_FAILED',
        'CONVERSION_FAILED', 'IMPORT_FAILED', 'FAILED', 'PAUSED', 'SEEDING',
        'SEEDING_COMPLETE', 'CANCELLED'
    ]

    PENDING_STATUSES = {'QUEUED', 'SEARCHING', 'FOUND', 'SEARCH_FAILED', 'DOWNLOAD_FAILED',
                        'AUDIBLE_DOWNLOAD_FAILED', 'FAILED'}

    def add_to_queue(self, book_asin: str, search_result_id: Optional[int] = None,
                     priority: int = 5, **kwargs) -> Dict[str, Any]:
        """Add a book to the download queue.

        Returns:
            {
                'success': bool,
                'download_id': int,
                'message': str
            }
        """
        try:
            self.logger.info(f"Adding book {book_asin} to download queue (priority={priority})")

            requested_type = (kwargs.get('download_type') or 'torrent').lower()
            ownership_details: Dict[str, Any] = kwargs.get('ownership_details') or {}
            audible_entry_override: Optional[Dict[str, Any]] = kwargs.pop('audible_entry_override', None)

            if requested_type == 'audible':
                db_service = container.get_database_service()
                audible_entry = fetch_audible_library_entry(db_service, book_asin)
                if audible_entry_override:
                    audible_entry = merge_audible_records(audible_entry, audible_entry_override)
                owned_via_audible, assessed_details = assess_audible_ownership(audible_entry)

                if not owned_via_audible:
                    reason = assessed_details.get('reason') if assessed_details else 'Ownership verification failed.'
                    self.logger.warning(
                        "Blocked Audible queue request for ASIN %s: %s",
                        book_asin,
                        reason
                    )
                    return {
                        'success': False,
                        'message': f'Audible ownership verification failed: {reason}'
                    }

                if not ownership_details:
                    ownership_details = assessed_details
                else:
                    ownership_details.setdefault('reason', assessed_details.get('reason'))
                kwargs['ownership_details'] = ownership_details
                kwargs['download_type'] = 'audible'
            
            existing = self.queue_manager.get_active_download_by_asin(book_asin)
            if existing:
                return {
                    'success': False,
                    'message': f"Book already in queue with status: {existing['status']}"
                }

            kwargs['target_library_id'] = self._route_library(book_asin, kwargs.pop('library_id', None))

            download_id = self.queue_manager.add_to_queue(
                book_asin=book_asin,
                search_result_id=search_result_id,
                priority=priority,
                **kwargs
            )
            
            self.event_emitter.emit_queue_added(download_id, book_asin)
            
            self._ensure_monitor_running()

            self.logger.info(f"Book {book_asin} added to queue with ID {download_id}")
            return {
                'success': True,
                'download_id': download_id,
                'message': 'Added to download queue'
            }
            
        except Exception as e:
            self.logger.error(f"Error adding to queue: {e}", exc_info=True)
            return {
                'success': False,
                'message': f'Failed to add to queue: {str(e)}'
            }

    def get_queue(self, status_filter: Optional[str] = None, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get all items in the download queue."""
        all_items = self.queue_manager.get_queue(status_filter)

        try:
            self._overlay_seeding_metrics(all_items, self.client_selector.get_cached_client)
        except Exception as overlay_error:
            self.logger.debug("Unable to overlay seeding metrics: %s", overlay_error)

        try:
            from services.metadata.covers.helpers import cache_image
            for item in all_items:
                raw_url = item.get('cover_image')
                item['cover_url'] = cache_image(raw_url) if raw_url else None
        except Exception as cover_error:
            self.logger.debug("Unable to resolve cover URLs: %s", cover_error)

        if limit is not None:
            return all_items[offset:offset + limit]
        elif offset > 0:
            return all_items[offset:]
        
        return all_items

    @staticmethod
    def _overlay_seeding_metrics(items: List[Dict[str, Any]], resolver) -> List[Dict[str, Any]]:
        """Overlay live seeding metrics from the download client onto queue items."""
        if not items or not resolver:
            return items

        for item in items:
            status = str(item.get('status') or '').upper()
            if status not in {'SEEDING', 'SEEDING_COMPLETE'}:
                continue

            client_name = item.get('download_client')
            client_id = item.get('download_client_id')
            if not client_name or not client_id:
                continue

            client = resolver(client_name)
            if not client:
                continue

            try:
                status_data = client.get_status(client_id)
            except Exception as exc:
                _LOGGER.debug("Skipping seeding overlay for %s: %s", item.get('id'), exc)
                continue

            if status_data is None:
                continue

            if 'seeding_time' in status_data:
                try:
                    item['seeding_time_seconds'] = int(status_data.get('seeding_time') or 0)
                except (TypeError, ValueError):
                    pass

            if 'ratio' in status_data:
                try:
                    item['seeding_ratio'] = float(status_data.get('ratio') or 0.0)
                except (TypeError, ValueError):
                    pass

            if 'seed_time_limit_seconds' in status_data:
                item['seed_time_limit_seconds'] = status_data.get('seed_time_limit_seconds')

            if 'seed_ratio_limit' in status_data:
                item['seed_ratio_limit'] = status_data.get('seed_ratio_limit')

        return items

    def cancel_download(self, download_id: int) -> Dict[str, Any]:
        """Cancel a download and clean up files.

        Returns:
            {'success': bool, 'message': str}
        """
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                return {'success': False, 'message': 'Download not found'}
            
            self.logger.info(f"Cancelling download {download_id} (ASIN: {download['book_asin']})")
            
            status = (download.get('status') or '').upper()
            if status == 'AUDIBLE_DOWNLOADING':
                self._request_audible_cancel(download_id)
                self.download_monitor.stop_monitoring(download_id)
            elif status == 'DOWNLOADING':
                self.download_monitor.stop_monitoring(download_id)

            self.cleanup_manager.cleanup_download_files(download_id)
            
            self.state_machine.transition(download_id, 'CANCELLED')
            
            self.event_emitter.emit_download_cancelled(download_id)

            self.logger.info(
                f"Download {download_id} cancelled: '{download.get('book_title', 'Unknown')}'"
                f" (ASIN: {download.get('book_asin')}, was: {status})"
            )
            return {'success': True, 'message': 'Download cancelled'}

        except Exception as e:
            self.logger.error(f"Error cancelling download: {e}", exc_info=True)
            return {'success': False, 'message': str(e)}

    def cancel_pending_for_asin(self, asin: str) -> int:
        """Cancel pending queue entries for a book that is now in the library."""
        if not asin:
            return 0
        cancelled = 0
        for item in self.queue_manager.get_queue():
            if (item.get('book_asin') or '').upper() != asin.upper():
                continue
            status = (item.get('status') or '').upper()
            if status not in self.PENDING_STATUSES:
                continue
            if status == 'FAILED':
                self.queue_manager.update_download(item['id'], {'status': 'CANCELLED'})
            else:
                self.state_machine.transition(item['id'], 'CANCELLED')
            cancelled += 1
            self.logger.info(f"Cancelled queued download {item['id']} (ASIN: {asin}): book was imported")
        if cancelled:
            self.event_emitter.emit_queue_updated()
        return cancelled

    def remove_download(self, download_id: int) -> Dict[str, Any]:
        """Permanently remove a single download from the queue.

        Unlike cancel_download (which soft-cancels an active download and
        leaves its row behind as CANCELLED), this deletes the row entirely.
        Intended for failed/cancelled/terminal entries the user wants off
        the queue without clearing every other item.

        Returns:
            {'success': bool, 'message': str}
        """
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                return {'success': False, 'message': 'Download not found'}

            status = (download.get('status') or '').upper()
            self.logger.info(
                f"Removing download {download_id} from queue: "
                f"'{download.get('book_title', 'Unknown')}' (ASIN: {download.get('book_asin')}, status: {status})"
            )

            try:
                if status == 'AUDIBLE_DOWNLOADING':
                    self._request_audible_cancel(download_id)
                if status in {'DOWNLOADING', 'AUDIBLE_DOWNLOADING'}:
                    self.download_monitor.stop_monitoring(download_id)
            except Exception as monitor_error:
                self.logger.warning(f"Failed to stop monitoring download {download_id}: {monitor_error}")

            try:
                self.cleanup_manager.cleanup_download_files(download_id)
            except Exception as cleanup_error:
                self.logger.warning(f"Cleanup failed for download {download_id}: {cleanup_error}")

            self.queue_manager.delete_download(download_id)
            self.event_emitter.emit_queue_updated()

            return {'success': True, 'message': 'Download removed from queue'}

        except Exception as e:
            self.logger.error(f"Error removing download {download_id}: {e}", exc_info=True)
            return {'success': False, 'message': str(e)}

    def pause_download(self, download_id: int) -> Dict[str, Any]:
        """Pause an active download (only works during DOWNLOADING state).

        Returns:
            {'success': bool, 'message': str}
        """
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                return {'success': False, 'message': 'Download not found'}
            
            if download['status'] != 'DOWNLOADING':
                return {
                    'success': False,
                    'message': f"Cannot pause download in {download['status']} state"
                }
            
            self.logger.info(f"Pausing download {download_id}")
            
            client = self.client_selector.get_client(download['download_client'])
            if not client:
                return {'success': False, 'message': f"Download client {download.get('download_client')} not available"}

            client_id = download.get('download_client_id')
            if not client_id:
                return {'success': False, 'message': 'Download has no client ID yet; try again shortly'}

            if not client.pause(client_id):
                client_error = getattr(client, 'get_last_error', lambda: None)() or 'Client rejected pause request'
                self.logger.warning(
                    "Pause request failed for download %s (client_id=%s): %s",
                    download_id,
                    client_id,
                    client_error,
                )
                return {'success': False, 'message': f'Failed to pause download: {client_error}'}
            
            if not self.state_machine.transition(download_id, 'PAUSED'):
                return {'success': False, 'message': 'Download paused in client but state transition failed'}
            
            self.event_emitter.emit_download_paused(download_id)
            
            return {'success': True, 'message': 'Download paused'}
            
        except Exception as e:
            self.logger.error(f"Error pausing download: {e}", exc_info=True)
            return {'success': False, 'message': str(e)}

    def resume_download(self, download_id: int) -> Dict[str, Any]:
        """Resume a paused download.

        Returns:
            {'success': bool, 'message': str}
        """
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                return {'success': False, 'message': 'Download not found'}
            
            if download['status'] != 'PAUSED':
                return {
                    'success': False,
                    'message': f"Cannot resume download in {download['status']} state"
                }
            
            self.logger.info(f"Resuming download {download_id}")
            
            client = self.client_selector.get_client(download['download_client'])
            if not client:
                return {'success': False, 'message': f"Download client {download.get('download_client')} not available"}

            client_id = download.get('download_client_id')
            if not client_id:
                return {'success': False, 'message': 'Download has no client ID yet; try again shortly'}

            if not client.resume(client_id):
                client_error = getattr(client, 'get_last_error', lambda: None)() or 'Client rejected resume request'
                self.logger.warning(
                    "Resume request failed for download %s (client_id=%s): %s",
                    download_id,
                    client_id,
                    client_error,
                )
                return {'success': False, 'message': f'Failed to resume download: {client_error}'}
            
            if not self.state_machine.transition(download_id, 'DOWNLOADING'):
                return {'success': False, 'message': 'Download resumed in client but state transition failed'}
            
            self.event_emitter.emit_download_resumed(download_id)
            
            return {'success': True, 'message': 'Download resumed'}
            
        except Exception as e:
            self.logger.error(f"Error resuming download: {e}", exc_info=True)
            return {'success': False, 'message': str(e)}

    def clear_queue(self, include_active: bool = False,
                    include_imported: bool = False,
                    statuses: Optional[List[str]] = None) -> Dict[str, Any]:
        """Remove downloads from the queue for recovery scenarios.

        Returns:
            {'success': bool, 'removed': int, 'cleared_ids': List[int], 'message': str}
        """
        try:
            base_statuses = statuses if statuses else list(self.CLEARABLE_STATUSES)

            processed_ids = set()
            cleared_ids: List[int] = []
            errors: List[Dict[str, Any]] = []

            normalized_statuses: List[str] = []
            for status in base_statuses:
                if not isinstance(status, str):
                    continue
                normalized = status.upper()
                if normalized in {'DOWNLOADING', 'AUDIBLE_DOWNLOADING'} and not include_active:
                    continue
                if normalized == 'IMPORTED' and not include_imported:
                    continue
                normalized_statuses.append(normalized)

            seen_statuses = set()
            ordered_statuses: List[str] = []
            for status in normalized_statuses:
                if status not in seen_statuses:
                    seen_statuses.add(status)
                    ordered_statuses.append(status)

            for status in ordered_statuses:
                try:
                    queue_items = self.queue_manager.get_queue(status_filter=status)
                except Exception as fetch_error:
                    self.logger.error(f"Error fetching downloads for status {status}: {fetch_error}", exc_info=True)
                    errors.append({'status': status, 'error': str(fetch_error)})
                    continue

                for item in queue_items:
                    download_id = item.get('id') or item.get('ID')
                    if download_id is None or download_id in processed_ids:
                        continue

                    processed_ids.add(download_id)

                    try:
                        if status == 'AUDIBLE_DOWNLOADING':
                            self._request_audible_cancel(download_id)

                        if status in {'DOWNLOADING', 'AUDIBLE_DOWNLOADING'}:
                            self.download_monitor.stop_monitoring(download_id)
                    except Exception as monitor_error:
                        self.logger.warning(f"Failed to stop monitoring download {download_id}: {monitor_error}")

                    try:
                        self.cleanup_manager.cleanup_download_files(download_id)
                    except Exception as cleanup_error:
                        self.logger.warning(f"Cleanup failed for download {download_id}: {cleanup_error}")

                    try:
                        self.logger.info(
                            f"Clearing download {download_id}: '{item.get('book_title', 'Unknown')}'"
                            f" (ASIN: {item.get('book_asin')}, status: {status})"
                        )
                        self.queue_manager.delete_download(download_id)
                        cleared_ids.append(download_id)
                    except Exception as delete_error:
                        self.logger.error(f"Failed to delete download {download_id}: {delete_error}", exc_info=True)
                        errors.append({'id': download_id, 'error': str(delete_error)})

            if cleared_ids:
                self.event_emitter.emit_queue_updated()
                self.logger.info(f"Queue clear complete: removed {len(cleared_ids)} item(s) [ids: {cleared_ids}]")

            message = f"Removed {len(cleared_ids)} download(s) from queue"
            success_flag = True
            if errors and not cleared_ids:
                message = 'No downloads were removed from the queue'
                success_flag = False
            elif errors:
                message = f"{message} with some errors"

            result: Dict[str, Any] = {
                'success': success_flag,
                'removed': len(cleared_ids),
                'cleared_ids': cleared_ids,
                'message': message
            }
            if errors:
                result['errors'] = errors

            return result

        except Exception as e:
            self.logger.error(f"Error clearing download queue: {e}", exc_info=True)
            return {
                'success': False,
                'message': f'Failed to clear queue: {str(e)}'
            }

    def _route_library(self, book_asin: str, explicit_id: Optional[int] = None) -> Optional[int]:
        """Library a queued book will be imported into, or None with a single library."""
        try:
            if explicit_id in (None, '') and container.get_library_repository().count() <= 1:
                return None
            explicit = int(explicit_id) if explicit_id not in (None, '') else None
            return container.get_library_router().resolve_for_asin(book_asin, explicit)['library']['id']
        except Exception as exc:
            self.logger.warning(f"Could not choose a library for {book_asin}: {exc}")
            return None
