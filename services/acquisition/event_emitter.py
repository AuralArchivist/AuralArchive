"""
Module Name: event_emitter.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Emits real-time SocketIO events for download lifecycle and status updates,
    coordinating with the status service for long-running event tracking.

Location:
    /services/acquisition/event_emitter.py
"""

from threading import Lock
from typing import Callable, Optional

from core.logging import get_module_logger


class EventEmitter:
    """
    Emits SocketIO events for download events.
    
    Events:
    - download:queued
    - download:started
    - download:progress
    - download:completed
    - download:failed
    - download:cancelled
    - download:paused
    - download:resumed
    - queue:updated
    """
    
    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.DownloadManagement.EventEmitter")
        self._status_service = None
        self._status_lock = Lock()
        self._status_events = {}
        self._download_lookup: Optional[Callable[[int], Optional[dict]]] = None
        self._progress_buckets = {
            'downloading': {},
            'converting': {},
        }
        self._state_order = {
            'queued': 10,
            'searching': 20,
            'found': 30,
            'running': 35,
            'downloading': 40,
            'audible_downloading': 40,
            'paused': 45,
            'complete': 50,
            'converting': 60,
            'converted': 70,
            'processing': 75,
            'importing': 80,
            'imported': 90,
            'seeding': 100,
            'seeding_complete': 110,
            'completed': 120,
            'search_failed': 130,
            'download_failed': 130,
            'conversion_failed': 130,
            'import_failed': 130,
            'failed': 130,
            'cancelled': 130,
        }

    # --- Wiring helpers ---

    def attach_lookup(self, lookup: Callable[[int], Optional[dict]]):
        """Attach a callable that returns download metadata by ID."""
        self._download_lookup = lookup

    def _get_status_service(self):
        if self._status_service is None:
            try:
                from services.container import container

                self._status_service = container.get_status_service()
            except Exception as exc:
                self.logger.debug(f"Status service unavailable: {exc}")
                self._status_service = None
        return self._status_service

    def _get_download_details(self, download_id: int) -> dict:
        if not self._download_lookup:
            return {}
        try:
            return self._download_lookup(download_id) or {}
        except Exception as exc:
            self.logger.debug(f"Download lookup failed: {exc}")
            return {}

    def _title_for_download(self, download_id: int) -> str:
        details = self._get_download_details(download_id)
        return (
            details.get('book_title')
            or details.get('title')
            or details.get('book_asin')
            or f"Download #{download_id}"
        )

    def _resolve_title(self, download_id: int, override: Optional[str] = None) -> str:
        title = (override or '').strip() if isinstance(override, str) else ''
        return title or self._title_for_download(download_id)

    @staticmethod
    def _progress_bucket(progress: float) -> int:
        value = max(0.0, min(100.0, float(progress)))
        if value >= 100.0:
            return 10
        return int(value // 10)

    def _claim_progress_bucket(self, kind: str, download_id: int, progress: float) -> Optional[int]:
        buckets = self._progress_buckets.get(kind)
        if buckets is None:
            return None
        bucket = self._progress_bucket(progress)
        previous = buckets.get(download_id, -1)
        if bucket > previous:
            buckets[download_id] = bucket
            return bucket
        return None

    def _reset_progress_tracking(self, download_id: int):
        self._progress_buckets['downloading'].pop(download_id, None)
        self._progress_buckets['converting'].pop(download_id, None)

    def _ensure_status_event(self, download_id: int, *, state: str, message: str) -> Optional[int]:
        service = self._get_status_service()
        if not service:
            return None

        with self._status_lock:
            existing = self._status_events.get(download_id)
            if existing:
                return existing

        details = self._get_download_details(download_id)
        title = self._title_for_download(download_id)
        metadata = {
            'asin': details.get('book_asin'),
            'download_id': download_id,
            'priority': details.get('priority'),
            'status': details.get('status'),
        }
        event = service.start_event(
            category='download',
            title=title,
            message=message,
            source='Downloads',
            entity_id=download_id,
            state=state,
            progress=0.0,
            metadata=metadata,
        )

        with self._status_lock:
            existing = self._status_events.get(download_id)
            if existing:
                return existing
            self._status_events[download_id] = event['id']
            return event['id']

    def _update_status_event(self, download_id: int, **updates):
        service = self._get_status_service()
        if not service:
            return
        with self._status_lock:
            event_id = self._status_events.get(download_id)
        if not event_id:
            event_id = self._ensure_status_event(download_id, state=updates.get('state', 'queued'), message=updates.get('message', 'Download update'))
            if not event_id:
                return
        metadata = updates.pop('metadata', None)
        payload = {k: v for k, v in updates.items() if v is not None}
        if metadata is not None:
            payload['metadata'] = metadata

        new_state = str(payload.get('state') or '').lower()
        try:
            current = service.get_event(event_id) if event_id else None
        except Exception:
            self.logger.debug("Could not read status event %s", event_id, exc_info=True)
            current = None
        current_state = str((current or {}).get('state') or '').lower()

        if current_state and new_state and current_state != new_state:
            if new_state not in {'failed', 'cancelled'}:
                pause_resume = (
                    (current_state == 'downloading' and new_state == 'paused')
                    or (current_state == 'paused' and new_state == 'downloading')
                )
                if not pause_resume:
                    cur_rank = self._state_order.get(current_state, 0)
                    new_rank = self._state_order.get(new_state, 0)
                    if new_rank and cur_rank and new_rank < cur_rank:
                        self.logger.debug(
                            "Ignoring regressive status update for download %s: %s -> %s",
                            download_id,
                            current_state,
                            new_state,
                        )
                        return

        service.update_event(event_id, **payload)

    def _complete_status_event(self, download_id: int, *, success: bool, message: Optional[str] = None, error: Optional[str] = None):
        service = self._get_status_service()
        if not service:
            return
        with self._status_lock:
            event_id = self._status_events.get(download_id)
        if not event_id:
            return
        if success:
            service.complete_event(event_id, message=message)
        elif error:
            service.fail_event(event_id, message=message, error=error)
        else:
            service.cancel_event(event_id, message=message)
        self._reset_progress_tracking(download_id)
        with self._status_lock:
            self._status_events.pop(download_id, None)
    
    def _emit(self, event: str, data: dict):
        """Emit a SocketIO event from any thread (wraps in app context)."""
        try:
            from app import app, socketio
            with app.app_context():
                socketio.emit(event, data)
            self.logger.debug("Emitted socket event: %s", event)
        except Exception as exc:
            self.logger.error("Error emitting socket event %s: %s", event, exc, exc_info=True)
    
    def emit_queue_added(self, download_id: int, book_asin: str):
        """Emit queue item added event."""
        self._emit('queue:item_added', {
            'download_id': download_id,
            'book_asin': book_asin
        })
        title = self._title_for_download(download_id) or book_asin
        self._ensure_status_event(
            download_id,
            state='queued',
            message=f'Queued download: {title}'
        )
    
    def emit_download_started(self, download_id: int):
        """Emit download started event."""
        self._emit('download:started', {
            'download_id': download_id
        })
        title = self._title_for_download(download_id)
        self._progress_buckets['downloading'][download_id] = -1
        self._update_status_event(
            download_id,
            state='downloading',
            message=f'Downloading {title}',
            progress=0.0,
            metadata={'status': 'DOWNLOADING'}
        )

    def emit_progress(self, download_id: int, progress: float, message: Optional[str] = None):
        """Emit download progress event for UI updates."""
        payload = {
            'download_id': download_id,
            'progress': float(progress)
        }
        if message:
            payload['message'] = message
        self._emit('download:progress', payload)
        title = self._title_for_download(download_id)
        milestone = self._claim_progress_bucket('downloading', download_id, float(progress))
        milestone_message = None
        if milestone is not None:
            milestone_message = f'Downloading {title} - {milestone * 10}%'
        self._update_status_event(
            download_id,
            state='downloading',
            message=milestone_message,
            progress=float(progress),
            metadata={'status': 'DOWNLOADING'}
        )
    
    def update_download_progress(self, download_id: int, progress: float) -> None:
        """Update the sidebar status event progress for a torrent/usenet download.

        Called by download_monitor after progress_tracker.emit_progress() so that
        the sidebar activity panel receives the current percentage without firing a
        second download:progress socket event (that is already handled by
        ProgressTracker).
        """
        self._update_status_event(
            download_id,
            state='downloading',
            progress=float(progress),
            metadata={'status': 'DOWNLOADING'},
        )

    def emit_complete(self, download_id: int, *_args, **_kwargs):
        """File downloaded (COMPLETE state) - pipeline continues to conversion/import.

        Updates the activity panel entry but does NOT close it; subsequent state
        transitions (converting, importing...) will update the same entry.
        """
        self._emit('download:state_changed', {'download_id': download_id, 'new_status': 'COMPLETE'})
        title = self._title_for_download(download_id)
        self._update_status_event(
            download_id,
            state='complete',
            message=f'Download complete for {title}',
            metadata={'status': 'COMPLETE'},
        )

    def emit_download_completed(self, download_id: int):
        """Full lifecycle complete (IMPORTED / end of seeding). Closes the status event."""
        self._emit('download:completed', {'download_id': download_id})
        title = self._title_for_download(download_id)
        self._complete_status_event(download_id, success=True, message=f'Imported: {title}')

    def emit_conversion_progress(self, download_id: int, progress: float, message: Optional[str] = None):
        """Update the main download status event with conversion progress."""
        title = self._title_for_download(download_id)
        milestone = self._claim_progress_bucket('converting', download_id, float(progress))
        milestone_message = None
        if milestone is not None:
            milestone_message = f'Converting {title} - {milestone * 10}%'
        self._update_status_event(
            download_id,
            state='converting',
            message=milestone_message,
            progress=float(progress),
            metadata={'status': 'CONVERTING'},
        )
    
    def emit_download_cancelled(self, download_id: int):
        """Emit download cancelled event."""
        self._emit('download:cancelled', {
            'download_id': download_id
        })
        title = self._title_for_download(download_id)
        self._complete_status_event(download_id, success=False, message=f'Processing cancelled for {title}')
    
    def emit_download_paused(self, download_id: int):
        """Emit download paused event."""
        self._emit('download:paused', {
            'download_id': download_id
        })
        title = self._title_for_download(download_id)
        self._update_status_event(download_id, state='paused', message=f'Download paused for {title}', metadata={'status': 'PAUSED'})
    
    def emit_download_resumed(self, download_id: int):
        """Emit download resumed event."""
        self._emit('download:resumed', {
            'download_id': download_id
        })
        title = self._title_for_download(download_id)
        self._update_status_event(download_id, state='downloading', message=f'Download resumed for {title}', metadata={'status': 'DOWNLOADING'})
    
    def emit_state_changed(self, download_id: int, new_status: str, message: Optional[str] = None):
        """Emit state transition event."""
        self._emit('download:state_changed', {
            'download_id': download_id,
            'new_status': new_status,
        })
        self._update_status_event(
            download_id,
            state=new_status.lower(),
            message=message or f'Status changed to {new_status}',
            metadata={'status': new_status}
        )

    def emit_search_started(self, download_id: int, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        self.emit_state_changed(download_id, 'SEARCHING', f'Searching for {resolved}')

    def emit_search_found(self, download_id: int, source: Optional[str] = None, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        src = (source or '').strip()
        if src:
            self.emit_state_changed(download_id, 'FOUND', f'{resolved} found on {src}')
        else:
            self.emit_state_changed(download_id, 'FOUND', f'{resolved} found')

    def emit_conversion_started(self, download_id: int, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        self._progress_buckets['converting'][download_id] = -1
        self.emit_state_changed(download_id, 'CONVERTING', f'Converting {resolved}')

    def emit_conversion_completed(self, download_id: int, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        self.emit_state_changed(download_id, 'CONVERTED', f'Conversion complete for {resolved}')

    def emit_import_started(self, download_id: int, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        self.emit_state_changed(download_id, 'IMPORTING', f'Importing {resolved} to library')

    def emit_import_completed(self, download_id: int, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        self.emit_state_changed(download_id, 'IMPORTED', f'{resolved} imported to library')

    def emit_seeding_started(self, download_id: int, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        self.emit_state_changed(download_id, 'SEEDING', f'Seeding {resolved}')

    def emit_seeding_completed(self, download_id: int, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        self.emit_state_changed(download_id, 'SEEDING_COMPLETE', f'Seeding complete for {resolved}')

    def emit_search_failed(self, download_id: int, reason: Optional[str] = None, title: Optional[str] = None):
        resolved = self._resolve_title(download_id, title)
        detail = (reason or '').strip()
        if detail:
            message = f'No valid source found for {resolved}: {detail}'
        else:
            message = f'No valid source found for {resolved}'
        self._emit('download:state_changed', {'download_id': download_id, 'new_status': 'SEARCH_FAILED'})
        self._update_status_event(
            download_id,
            state='search_failed',
            message=message,
            metadata={'status': 'SEARCH_FAILED'}
        )

    def emit_stage_failed(self, download_id: int, failure_status: str, error_message: str, retrying: bool = False):
        title = self._title_for_download(download_id)
        failure_key = (failure_status or '').upper()
        mapping = {
            'DOWNLOAD_FAILED': ('download_failed', 'Download failed'),
            'AUDIBLE_DOWNLOAD_FAILED': ('download_failed', 'Download failed'),
            'CONVERSION_FAILED': ('conversion_failed', 'Conversion failed'),
            'IMPORT_FAILED': ('import_failed', 'Import failed'),
        }
        state, prefix = mapping.get(failure_key, ('failed', 'Operation failed'))
        suffix = ' (will retry)' if retrying else ''
        message = f'{prefix} for {title}: {error_message}{suffix}'
        self._emit('download:state_changed', {'download_id': download_id, 'new_status': failure_key or 'FAILED'})
        self._update_status_event(
            download_id,
            state=state,
            message=message,
            metadata={'status': failure_key or 'FAILED'}
        )

    def emit_retry_scheduled(self, download_id: int, retry_state: str, delay_seconds: int):
        title = self._title_for_download(download_id)
        state_name = (retry_state or '').upper()
        if delay_seconds > 0:
            message = f'Retry scheduled for {title} in {delay_seconds}s ({state_name})'
        else:
            message = f'Retrying {title} now ({state_name})'
        self._emit('download:retry_scheduled', {
            'download_id': download_id,
            'retry_state': state_name,
            'delay_seconds': delay_seconds,
        })
        self._update_status_event(
            download_id,
            state='warning',
            message=message,
            metadata={
                'status': 'RETRY_SCHEDULED',
                'retry_state': state_name,
                'retry_delay_seconds': delay_seconds,
            }
        )
    
    def emit_queue_updated(self):
        """Emit queue updated event (triggers frontend refresh)."""
        self._emit('queue:updated', {})
