"""
Module Name: retry_handler.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Manages retry logic and limits for download pipeline failures across search,
    download, conversion, and import stages.

Location:
    /services/acquisition/retry_handler.py
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from core.logging import get_module_logger


class RetryHandler:
    """
    Handles retry logic for failed operations.
    
    Retry limits by stage:
    - SEARCH_FAILED: 3 attempts
    - DOWNLOAD_FAILED: 2 attempts
    - CONVERSION_FAILED: 1 attempt
    - IMPORT_FAILED: 2 attempts
    """
    
    MAX_RETRIES = {
        'SEARCH_FAILED': 3,
        'DOWNLOAD_FAILED': 2,
        'AUDIBLE_DOWNLOAD_FAILED': 2,
        'CONVERSION_FAILED': 1,
        'IMPORT_FAILED': 2
    }
    
    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.DownloadManagement.RetryHandler")
        self._queue_manager = None
        self._state_machine = None
        self._event_emitter = None
        self.retry_backoff_seconds = 10
        self.retry_limits: Dict[str, int] = dict(self.MAX_RETRIES)

    def attach_event_emitter(self, event_emitter: Optional[Any]):
        """Attach shared EventEmitter for failure/retry status updates."""
        self._event_emitter = event_emitter

    def set_queue_manager(self, queue_manager):
        """Attach shared QueueManager from DownloadManagementService."""
        self._queue_manager = queue_manager

    def set_state_machine(self, state_machine):
        """Attach shared StateMachine from DownloadManagementService."""
        self._state_machine = state_machine
    
    def _get_queue_manager(self):
        """Lazy load QueueManager."""
        if self._queue_manager is None:
            from .queue_manager import QueueManager
            self._queue_manager = QueueManager()
        return self._queue_manager
    
    def _get_state_machine(self):
        """Lazy load StateMachine."""
        if self._state_machine is None:
            from .state_machine import StateMachine
            self._state_machine = StateMachine()
        return self._state_machine

    def set_retry_backoff(self, seconds: int):
        """Configure retry backoff delay in seconds."""
        minimum = max(10, int(seconds) if seconds else 10)
        self.retry_backoff_seconds = minimum
        self.logger.debug(
            "Retry backoff configured to %s seconds",
            self.retry_backoff_seconds
        )

    def set_retry_limits(self, limits: Optional[Dict[str, int]]):
        """Override per-stage max-retry counts from config.

        Merges onto the existing MAX_RETRIES-derived defaults rather than
        replacing the dict outright, so a stage the config doesn't mention
        (e.g. AUDIBLE_DOWNLOAD_FAILED, which has no settings-UI field) keeps
        its hardcoded default instead of silently dropping to 0.
        """
        if not limits:
            return
        for stage, max_retries in limits.items():
            try:
                self.retry_limits[stage] = max(0, int(max_retries))
            except (TypeError, ValueError):
                self.logger.warning("Invalid retry limit for %s: %s", stage, max_retries)
        self.logger.debug("Retry limits configured: %s", self.retry_limits)
    
    def should_retry(self, download_id: int, failure_status: str) -> bool:
        """Check if download should be retried."""
        queue_manager = self._get_queue_manager()
        download = queue_manager.get_download(download_id)
        
        if not download:
            self.logger.warning(
                "Retry evaluation aborted - download %s not found in queue",
                download_id
            )
            return False
        
        retry_count = download.get('retry_count', 0)
        max_retries = self.retry_limits.get(failure_status, 0)
        
        can_retry = retry_count < max_retries
        
        if can_retry:
            self.logger.debug(
                "Download %s can retry (%s/%s)",
                download_id,
                retry_count + 1,
                max_retries
            )
        else:
            self.logger.warning(
                "Download %s exceeded max retries (%s/%s)",
                download_id,
                retry_count,
                max_retries
            )
        
        return can_retry
    
    def handle_failure(self, download_id: int, failure_status: str, 
                       error_message: str) -> bool:
        """Handle download failure - retry or mark as failed."""
        queue_manager = self._get_queue_manager()
        download = queue_manager.get_download(download_id)
        
        if not download:
            self.logger.error(
                "Retry handling aborted - download %s not found in queue",
                download_id
            )
            return False

        title_hint = download.get('book_title') or download.get('title') or download.get('book_asin')
        
        if self.should_retry(download_id, failure_status):
            retry_count = download.get('retry_count', 0) + 1
            
            retry_state = self._get_retry_state(failure_status)
            
            updates: Dict[str, Any] = {
                'status': retry_state,
                'retry_count': retry_count,
                'error_message': None,
                'last_error': error_message
            }

            if failure_status in ('DOWNLOAD_FAILED', 'SEARCH_FAILED'):
                retry_at = datetime.utcnow() + timedelta(seconds=self.retry_backoff_seconds)
                updates['next_retry_at'] = retry_at.isoformat()
            else:
                updates['next_retry_at'] = None

            queue_manager.update_download(download_id, updates)

            if self._event_emitter:
                if failure_status == 'SEARCH_FAILED':
                    self._event_emitter.emit_search_failed(download_id, title=title_hint)
                else:
                    self._event_emitter.emit_stage_failed(download_id, failure_status, error_message, retrying=True)
                delay_seconds = self.retry_backoff_seconds if failure_status == 'DOWNLOAD_FAILED' else 0
                self._event_emitter.emit_retry_scheduled(download_id, retry_state, delay_seconds)
            
            self.logger.debug(
                "Retrying download %s: %s -> %s",
                download_id,
                failure_status,
                retry_state
            )
            return True
        else:
            queue_manager.update_download(download_id, {
                'status': failure_status,
                'error_message': error_message,
                'next_retry_at': None,
                'last_error': error_message
            })

            if self._event_emitter:
                if failure_status == 'SEARCH_FAILED':
                    self._event_emitter.emit_search_failed(download_id, title=title_hint)
                else:
                    self._event_emitter.emit_stage_failed(download_id, failure_status, error_message, retrying=False)
            
            self.logger.error(
                "Download %s permanently failed: %s",
                download_id,
                error_message
            )
            from services.notifications.events import download_failed
            download_failed(download, failure_status, error_message)
            return False
    
    def _get_retry_state(self, failure_status: str) -> str:
        """Get target state for retry based on failure type."""
        retry_map = {
            'SEARCH_FAILED': 'SEARCHING',
            'DOWNLOAD_FAILED': 'FOUND',
            'AUDIBLE_DOWNLOAD_FAILED': 'QUEUED',
            'CONVERSION_FAILED': 'CONVERTING',
            'IMPORT_FAILED': 'IMPORTING'
        }
        
        return retry_map.get(failure_status, 'QUEUED')
