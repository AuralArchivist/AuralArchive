"""
Module Name: state_machine.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Manages download state transitions and validation for the pipeline. Enforces
    valid transitions across torrent and Audible branches while stamping key
    timestamps during lifecycle milestones.

Location:
    /services/acquisition/state_machine.py

Valid state flow:
    QUEUED → SEARCHING → FOUND → DOWNLOADING → PAUSED → DOWNLOADING
                                            ↓
                                        COMPLETE → CONVERTING → CONVERTED → IMPORTING → IMPORTED
                                            ↓ (torrents/NZBs skip conversion)
                                        COMPLETE → IMPORTING → IMPORTED
                                                                  ↓
                                                      (if seeding enabled) SEEDING → SEEDING_COMPLETE
                                            ↓
                                        (error states: SEARCH_FAILED, DOWNLOAD_FAILED, etc.)

Audible-specific branch:
    QUEUED → AUDIBLE_DOWNLOADING → COMPLETE → CONVERTING → …
                          ↓
                   AUDIBLE_DOWNLOAD_FAILED (retryable)
"""

from typing import Dict, Set
from datetime import datetime

from core.logging import get_module_logger


class StateMachine:
    """
    Enforces valid state transitions for download lifecycle.
    
    Prevents invalid state changes and maintains data consistency.
    """
    
    ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    'QUEUED': {'SEARCHING', 'FOUND', 'CANCELLED', 'AUDIBLE_DOWNLOADING'},
    'SEARCHING': {'FOUND', 'SEARCH_FAILED', 'CANCELLED'},
    'FOUND': {'DOWNLOADING', 'CANCELLED'},
    'DOWNLOADING': {'COMPLETE', 'DOWNLOAD_COMPLETE', 'DOWNLOAD_FAILED', 'PAUSED', 'CANCELLED'},
    'AUDIBLE_DOWNLOADING': {'COMPLETE', 'DOWNLOAD_COMPLETE', 'AUDIBLE_DOWNLOAD_FAILED', 'CANCELLED'},
    'PAUSED': {'DOWNLOADING', 'CANCELLED'},
    'DOWNLOAD_COMPLETE': {'CONVERTING', 'IMPORTING', 'CANCELLED'},
    'COMPLETE': {'CONVERTING', 'IMPORTING', 'CANCELLED'},
    'CONVERTING': {'CONVERTED', 'CONVERSION_FAILED', 'CANCELLED'},
    'CONVERTED': {'PROCESSING', 'IMPORTING', 'CANCELLED'},
    'PROCESSING': {'PROCESSED', 'IMPORTING', 'CANCELLED'},
    'PROCESSED': {'IMPORTING', 'CANCELLED'},
    'IMPORTING': {'IMPORTED', 'IMPORT_FAILED', 'CANCELLED'},
    'IMPORTED': {'SEEDING'},
    'SEEDING': {'SEEDING_COMPLETE', 'CANCELLED'},
    'SEEDING_COMPLETE': set(),
    'SEARCH_FAILED': {'SEARCHING', 'CANCELLED'},
    'DOWNLOAD_FAILED': {'FOUND', 'CANCELLED'},
    'AUDIBLE_DOWNLOAD_FAILED': {'QUEUED', 'AUDIBLE_DOWNLOADING', 'CANCELLED'},
    'CONVERSION_FAILED': {'CONVERTING', 'CANCELLED'},
    'IMPORT_FAILED': {'IMPORTING', 'CANCELLED'},
    'CANCELLED': set()
    }
    
    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.DownloadManagement.StateMachine")
        self._queue_manager = None
    
    def _get_queue_manager(self):
        """Lazy load QueueManager."""
        if self._queue_manager is None:
            from .queue_manager import QueueManager
            self._queue_manager = QueueManager()
        return self._queue_manager

    def set_queue_manager(self, queue_manager):
        """Attach shared QueueManager from DownloadManagementService."""
        self._queue_manager = queue_manager

    def transition(self, download_id: int, new_status: str) -> bool:
        """Transition download to new status if valid."""
        queue_manager = self._get_queue_manager()
        download = queue_manager.get_download(download_id)
        
        if not download:
            self.logger.error("Download %s not found", download_id)
            return False
        
        current_status = download['status']
        
        if not self.is_valid_transition(current_status, new_status):
            self.logger.warning("Invalid transition download_id=%s %s→%s", download_id, current_status, new_status)
            return False
        
        updates = {'status': new_status}
        
        if new_status in {'DOWNLOADING', 'AUDIBLE_DOWNLOADING'} and current_status in ('QUEUED', 'FOUND', 'PAUSED', 'AUDIBLE_DOWNLOAD_FAILED'):
            updates['started_at'] = datetime.now().isoformat()
        elif new_status == 'COMPLETE':
            updates['completed_at'] = datetime.now().isoformat()
        elif new_status == 'IMPORTED':
            updates['completed_at'] = datetime.now().isoformat()
        
        queue_manager.update_download(download_id, updates)
        
        self.logger.info("Download %s transition %s→%s", download_id, current_status, new_status)
        return True
    
    def is_valid_transition(self, current_status: str, new_status: str) -> bool:
        """Check if state transition is valid."""
        if current_status not in self.ALLOWED_TRANSITIONS:
            self.logger.warning("Unknown current status '%s' for transition to '%s'", current_status, new_status)
            return False
        
        allowed = self.ALLOWED_TRANSITIONS[current_status]
        return new_status in allowed
    
    def can_retry(self, current_status: str) -> bool:
        """Check if download can be retried in current state."""
        return current_status in (
            'SEARCH_FAILED',
            'DOWNLOAD_FAILED',
            'AUDIBLE_DOWNLOAD_FAILED',
            'CONVERSION_FAILED',
            'IMPORT_FAILED'
        )
    