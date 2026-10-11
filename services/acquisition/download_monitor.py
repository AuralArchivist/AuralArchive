"""
Module Name: download_monitor.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Polls download clients for progress and seeding updates, caching status
    snapshots and triggering state transitions or cleanup when thresholds are
    met. Emits SocketIO events through helper classes.

Location:
    /services/acquisition/download_monitor.py
"""

import os
from typing import Dict, Any, Optional

from core.logging import get_module_logger
from .path_mapping import normalize_path, remap_remote_to_local


class DownloadMonitor:
    """Polls download clients for progress and seeding status updates."""
    
    ACTIVE_SEEDING_STATES = {
        "uploading",
        "stalledup",
        "forcedup",
        "checkingup",
        "queuedup",
        "seeding",
    }

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.DownloadManagement.DownloadMonitor")
        self._queue_manager = None
        self._client_selector = None
        self._progress_tracker = None
        self._state_machine = None
        self._cleanup_manager = None
        self._event_emitter = None
        self._last_known_downloads: Dict[int, Dict[str, Any]] = {}
        self._nzb_missing_counts: Dict[int, int] = {}
        self._seeding_missing_counts: Dict[int, int] = {}
        self._downloading_missing_counts: Dict[int, int] = {}
        self.NZB_MISSING_THRESHOLD = 3
        self.SEEDING_MISSING_WARN_THRESHOLD = 3
        self.DOWNLOADING_MISSING_THRESHOLD = 5
    
    def _get_queue_manager(self):
        """Lazy load QueueManager."""
        if self._queue_manager is None:
            from .queue_manager import QueueManager
            self._queue_manager = QueueManager()
        return self._queue_manager
    
    def _get_client_selector(self):
        """Lazy load ClientSelector."""
        if self._client_selector is None:
            from .client_selector import ClientSelector
            self._client_selector = ClientSelector()
        return self._client_selector

    def set_client_selector(self, client_selector):
        """Attach shared ClientSelector from DownloadManagementService."""
        self._client_selector = client_selector

    def set_queue_manager(self, queue_manager):
        """Attach shared QueueManager from DownloadManagementService."""
        self._queue_manager = queue_manager

    def set_state_machine(self, state_machine):
        """Attach shared StateMachine from DownloadManagementService."""
        self._state_machine = state_machine

    def set_cleanup_manager(self, cleanup_manager):
        """Attach shared CleanupManager from DownloadManagementService."""
        self._cleanup_manager = cleanup_manager
    
    def _get_progress_tracker(self):
        """Lazy load ProgressTracker."""
        if self._progress_tracker is None:
            from .progress_tracker import ProgressTracker
            self._progress_tracker = ProgressTracker()
        return self._progress_tracker
    
    def _get_state_machine(self):
        """Lazy load StateMachine."""
        if self._state_machine is None:
            from .state_machine import StateMachine
            self._state_machine = StateMachine()
        return self._state_machine
    
    def _get_cleanup_manager(self):
        """Lazy load CleanupManager."""
        if self._cleanup_manager is None:
            from .cleanup_manager import CleanupManager
            self._cleanup_manager = CleanupManager()
        return self._cleanup_manager
    
    def _get_event_emitter(self):
        """Lazy load EventEmitter."""
        if self._event_emitter is None:
            from .event_emitter import EventEmitter
            self._event_emitter = EventEmitter()
        return self._event_emitter

    def set_event_emitter(self, event_emitter):
        """Attach the shared EventEmitter from DownloadManagementService."""
        self._event_emitter = event_emitter

    def _cache_download(self, download_id: int, download: Dict[str, Any]):
        """Remember last known download data for seeding monitors."""
        if download:
            self._last_known_downloads[download_id] = dict(download)

    def _clear_seeding_missing_count(self, download_id: int):
        """Clear transient seeding-miss tracking for a download."""
        self._seeding_missing_counts.pop(download_id, None)

    @staticmethod
    def _format_ratio_limit(value: Optional[float]) -> str:
        if isinstance(value, (int, float)):
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return "invalid"
        return "unset"

    @staticmethod
    def _format_time_limit(value: Optional[int]) -> str:
        if isinstance(value, (int, float)):
            try:
                return str(int(value))
            except (TypeError, ValueError):
                return "invalid"
        return "unset"
    
    def update_progress(self, download_id: int):
        """Poll client and update progress for a download."""
        queue_manager = self._get_queue_manager()
        download = queue_manager.get_download(download_id)
        
        if not download:
            self.logger.warning(f"Download {download_id} not found")
            return
        
        if download['status'] != 'DOWNLOADING':
            self.logger.debug(f"Download {download_id} not in DOWNLOADING state")
            return
        
        client_selector = self._get_client_selector()
        client_name = download.get('download_client')
        if not client_name:
            self.logger.debug(f"Download {download_id} has no client assigned yet; skipping poll")
            return
        client = client_selector.get_client(client_name)

        if not client:
            self.logger.debug(f"Client {client_name} not available; skipping progress poll")
            return

        if getattr(client, "client_protocol", None) == "nzb":
            self._update_nzb_progress(download_id, download, client)
            return

        client_id = download.get('download_client_id')
        if not client_id:
            client_id = self._discover_torrent_hash(client, download)
            if client_id:
                queue_manager.update_download(download_id, {
                    'download_client_id': client_id,
                    'last_error': None
                })
                download['download_client_id'] = client_id
                self.logger.debug(f"Resolved torrent hash for download {download_id}: {client_id}")
            else:
                self.logger.debug(f"Skipping progress update for download {download_id} (torrent hash unavailable)")
                return
        
        try:
            status = client.get_status(client_id)

            if status and status.get('hash') and status.get('hash') != client_id:
                resolved_hash = status.get('hash')
                previous_hash = client_id
                queue_manager.update_download(download_id, {
                    'download_client_id': resolved_hash,
                    'last_error': None
                })
                download['download_client_id'] = resolved_hash
                client_id = resolved_hash
                self.logger.debug(
                    "Updated download %s client hash from %s to %s",
                    download_id,
                    previous_hash,
                    resolved_hash,
                )
            
            if not status:
                self.logger.warning(f"No status for download {download_id}")
                return

            self._downloading_missing_counts.pop(download_id, None)

            progress = status.get('progress', 0.0)
            download_speed = status.get('download_speed', 0)
            eta_seconds = status.get('eta', -1)

            updates = {
                'download_progress': progress,
                'eta_seconds': eta_seconds
            }
            queue_manager.update_download(download_id, updates)

            progress_tracker = self._get_progress_tracker()
            progress_tracker.emit_progress(download_id, progress, download_speed, eta_seconds)
            self._get_event_emitter().update_download_progress(download_id, progress)

            if progress >= 100.0:
                self.logger.debug(f"Download {download_id} complete")
                state_machine = self._get_state_machine()
                if state_machine.transition(download_id, 'COMPLETE'):
                    self._get_event_emitter().emit_complete(download_id)

        except Exception as e:
            error_text = str(e)
            if 'not found' in error_text.lower():
                replacement_hash = self._discover_torrent_hash(client, download)
                if replacement_hash and replacement_hash != client_id:
                    try:
                        queue_manager.update_download(download_id, {
                            'download_client_id': replacement_hash,
                            'last_error': None
                        })
                        download['download_client_id'] = replacement_hash

                        status = client.get_status(replacement_hash)
                        if status:
                            self._downloading_missing_counts.pop(download_id, None)

                            progress = status.get('progress', 0.0)
                            download_speed = status.get('download_speed', 0)
                            eta_seconds = status.get('eta', -1)

                            queue_manager.update_download(download_id, {
                                'download_progress': progress,
                                'eta_seconds': eta_seconds,
                            })

                            progress_tracker = self._get_progress_tracker()
                            progress_tracker.emit_progress(download_id, progress, download_speed, eta_seconds)
                            self._get_event_emitter().update_download_progress(download_id, progress)

                            if progress >= 100.0:
                                self.logger.debug(f"Download {download_id} complete")
                                state_machine = self._get_state_machine()
                                if state_machine.transition(download_id, 'COMPLETE'):
                                    self._get_event_emitter().emit_complete(download_id)

                            self.logger.info(
                                "Recovered torrent hash for download %s: %s -> %s",
                                download_id,
                                client_id,
                                replacement_hash,
                            )
                            return
                    except Exception as recovery_error:
                        self.logger.debug(
                            "Recovery attempt failed for download %s: %s",
                            download_id,
                            recovery_error,
                        )

                miss_count = self._downloading_missing_counts.get(download_id, 0) + 1
                self._downloading_missing_counts[download_id] = miss_count

                if miss_count >= self.DOWNLOADING_MISSING_THRESHOLD:
                    self._downloading_missing_counts.pop(download_id, None)
                    error_msg = (
                        f"Torrent {client_id} not found in download client after "
                        f"{miss_count} consecutive polls; re-queuing for a new search"
                    )
                    queue_manager.update_download(download_id, {'last_error': error_msg})
                    self._get_state_machine().transition(download_id, 'DOWNLOAD_FAILED')
                    self._get_event_emitter().emit_stage_failed(
                        download_id, 'DOWNLOAD_FAILED', error_msg, retrying=True
                    )
                    self.logger.error(
                        "Download %s transitioned to DOWNLOAD_FAILED: %s",
                        download_id,
                        error_msg, exc_info=True,
                    )
                    return

                self.logger.warning(
                    "Torrent %s not found for download %s (miss %d/%d); waiting",
                    client_id,
                    download_id,
                    miss_count,
                    self.DOWNLOADING_MISSING_THRESHOLD,
                )
                return

            self.logger.error(f"Error updating progress for download {download_id}: {e}", exc_info=True)

    def _update_nzb_progress(self, download_id: int, download: Dict[str, Any], client) -> None:
        """
        Poll an NZB client for progress and handle terminal states.

        Called from update_progress when the assigned download client is an
        NZB client (SABnzbd or NZBGet).  NZB downloads use state as the
        authoritative completion signal rather than progress >= 100.
        """
        from integrations.download_clients.base_nzb_client import NzbState

        client_id = download.get("download_client_id")
        if not client_id:
            self.logger.warning(
                f"NZB download {download_id} has no client ID; cannot poll status"
            )
            return

        try:
            status = client.get_status(client_id)
        except ValueError:
            miss_count = self._nzb_missing_counts.get(download_id, 0) + 1
            self._nzb_missing_counts[download_id] = miss_count
            self.logger.warning(
                "NZB ID %s not found in client for download %s (miss %d/%d)",
                client_id, download_id, miss_count, self.NZB_MISSING_THRESHOLD,
            )
            if miss_count >= self.NZB_MISSING_THRESHOLD:
                self._nzb_missing_counts.pop(download_id, None)
                error_msg = (
                    f"NZB job {client_id} was not found in the download client after "
                    f"{self.NZB_MISSING_THRESHOLD} consecutive polls; "
                    "re-queuing for a new search"
                )
                self.logger.error(
                    "NZB download %s aborted: %s", download_id, error_msg
                )
                queue_manager = self._get_queue_manager()
                queue_manager.update_download(download_id, {"last_error": error_msg})
                state_machine = self._get_state_machine()
                state_machine.transition(download_id, "DOWNLOAD_FAILED")
                self._get_event_emitter().emit_stage_failed(
                    download_id, "DOWNLOAD_FAILED", error_msg, retrying=True
                )
            return
        except Exception as exc:
            self.logger.error(
                f"Error polling NZB status for download {download_id}: {exc}", exc_info=True
            )
            return

        self._nzb_missing_counts.pop(download_id, None)

        if not status:
            return

        state: NzbState         = status.get("state", NzbState.UNKNOWN)
        progress: float         = float(status.get("progress", 0.0))
        download_speed: int     = max(int(status.get("download_speed", 0) or 0), 0)
        eta: int                = int(status.get("eta", -1) or -1)

        queue_manager = self._get_queue_manager()
        queue_manager.update_download(download_id, {
            "download_progress": progress,
            "eta_seconds":       eta,
        })

        progress_tracker = self._get_progress_tracker()
        progress_tracker.emit_progress(download_id, progress, download_speed, eta)
        self._get_event_emitter().update_download_progress(download_id, progress)

        if state == NzbState.COMPLETE:
            self.logger.info(f"NZB download {download_id} complete")
            storage_path = status.get("storage_path")
            if storage_path:
                local_path = self._remap_sabnzbd_path(storage_path)
                if os.path.isfile(local_path):
                    local_path = os.path.dirname(local_path)
                    self.logger.debug(
                        "NZB download %s: storage was a file path; using parent directory: %s",
                        download_id, local_path,
                    )
                queue_manager.update_download(download_id, {"temp_file_path": local_path})
                self.logger.info(
                    f"NZB download {download_id} stored at: {local_path}"
                    + (f" (remapped from {storage_path})" if local_path != storage_path else "")
                )
            else:
                self.logger.warning(
                    f"NZB download {download_id} completed but SABnzbd returned no storage path"
                )
            state_machine = self._get_state_machine()
            if state_machine.transition(download_id, "COMPLETE"):
                self._get_event_emitter().emit_complete(download_id)

        elif state == NzbState.FAILED:
            error_msg = status.get("error") or "NZB download failed"
            self.logger.error(
                f"NZB download {download_id} failed: {error_msg}"
            )
            queue_manager.update_download(download_id, {"last_error": error_msg})
            state_machine = self._get_state_machine()
            state_machine.transition(download_id, "FAILED")
            self._get_event_emitter().emit_stage_failed(download_id, 'DOWNLOAD_FAILED', error_msg, retrying=False)

    def _remap_sabnzbd_path(self, path: str) -> str:
        """Translate a SABnzbd-reported path to local filesystem path."""
        if not path:
            return path

        try:
            client_selector = self._get_client_selector()
            sab_config = client_selector.get_client_config('sabnzbd', refresh=True) or {}
            remote_base = sab_config.get('remote_path') or '/downloads'
            local_base = sab_config.get('local_path') or ''
            remapped = remap_remote_to_local(path, remote_base, local_base)
            return remapped or path
        except Exception as exc:
            self.logger.debug("SABnzbd path remap skipped", extra={"path": path, "error": str(exc)})

        return path

    def _discover_torrent_hash(self, client, download: Dict[str, Any]) -> Optional[str]:
        """Attempt to resolve torrent hash for downloads missing client ID."""
        try:
            torrents = client.get_all_torrents()
            if not torrents:
                return None

            temp_path = download.get('temp_file_path')
            normalized_temp = normalize_path(os.path.abspath(temp_path.rstrip('/\\'))) if temp_path else None
            temp_basename = os.path.basename(normalized_temp) if normalized_temp else None
            book_title = (download.get('book_title') or download.get('title') or '').lower()

            for torrent in torrents:
                candidate_hash = torrent.get('hash')
                if not candidate_hash:
                    continue

                save_path = normalize_path(torrent.get('save_path') or '')
                if normalized_temp and save_path and save_path == normalized_temp:
                    return candidate_hash

            if temp_basename:
                suffix = f"/{temp_basename}"
                basename_matches = []
                for torrent in torrents:
                    candidate_hash = torrent.get('hash')
                    if not candidate_hash:
                        continue

                    save_path = normalize_path(torrent.get('save_path') or '')
                    if save_path and save_path.endswith(suffix):
                        basename_matches.append(candidate_hash)

                if len(basename_matches) == 1:
                    return basename_matches[0]

            title_matches = []
            for torrent in torrents:
                candidate_hash = torrent.get('hash')
                if not candidate_hash:
                    continue

                name = (torrent.get('name') or '').lower()
                if book_title and name and book_title in name:
                    title_matches.append(candidate_hash)

            if len(title_matches) == 1:
                return title_matches[0]

            return None

        except Exception as exc:
            self.logger.debug(f"Unable to discover torrent hash: {exc}")
            return None

    def stop_monitoring(self, download_id: int):
        """Hook called when a download is cancelled or removed.

        Intentionally a no-op: there are no per-download monitoring threads.
        The single monitor loop picks rows by status each tick, so once the
        row is CANCELLED/removed it is no longer polled.
        """
        self.logger.debug(f"Stopped monitoring download {download_id}")
    
    def monitor_seeding(self, download_id: int):
        """Monitor a seeding torrent and detect when seeding completes.

        This method is called for downloads in SEEDING state to check if the
        download client has marked the torrent as complete based on its seeding
        goals (ratio reached, time elapsed, etc.).

        When seeding completes, triggers cleanup based on delete_source setting.
        """
        queue_manager = self._get_queue_manager()
        download = queue_manager.get_download(download_id)
        
        if not download:
            download = getattr(self, '_last_known_downloads', {}).get(download_id)
            if not download:
                self.logger.warning(f"Download {download_id} not found for seeding monitor")
                return
        else:
            self._cache_download(download_id, download)
        
        if download.get('status') != 'SEEDING':
            self.logger.debug(f"Download {download_id} not in SEEDING state, skipping seeding monitor")
            return

        client_selector = self._get_client_selector()
        client_name = download.get('download_client')
        if client_name in ("sabnzbd", "nzbget"):
            self.logger.debug(f"Skipping seeding monitor for NZB download {download_id}")
            return

        client_id = download.get('download_client_id')
        
        if not client_name or not client_id:
            self.logger.error(f"Download {download_id} missing client info for seeding monitor")
            return
        
        client = client_selector.get_client(client_name)
        if not client:
            self.logger.debug(f"Client {client_name} not available; skipping seeding poll")
            return
        
        try:
            try:
                status = client.get_status(client_id)
            except ValueError:
                replacement_hash = self._discover_torrent_hash(client, download)
                if replacement_hash and replacement_hash != client_id:
                    previous_hash = client_id
                    queue_manager.update_download(download_id, {
                        'download_client_id': replacement_hash,
                        'last_error': None,
                    })
                    download['download_client_id'] = replacement_hash
                    client_id = replacement_hash
                    self.logger.info(
                        "Recovered seeding hash for download %s: %s -> %s",
                        download_id,
                        previous_hash,
                        replacement_hash,
                    )
                    status = client.get_status(client_id)
                else:
                    misses = self._seeding_missing_counts.get(download_id, 0) + 1
                    self._seeding_missing_counts[download_id] = misses

                    if misses >= self.SEEDING_MISSING_WARN_THRESHOLD:
                        warning_message = (
                            f"Unable to locate seeding torrent in client after {misses} checks; "
                            "retaining SEEDING state until client confirms completion"
                        )
                        queue_manager.update_download(download_id, {'last_error': warning_message})
                        self.logger.warning(
                            "Torrent %s still missing for seeding download %s (%d consecutive misses)",
                            client_id,
                            download_id,
                            misses,
                        )
                    else:
                        self.logger.info(
                            "Torrent %s not found while monitoring seeding download %s (miss %d); waiting",
                            client_id,
                            download_id,
                            misses,
                        )
                    return

            self._clear_seeding_missing_count(download_id)
            
            if not status:
                self.logger.warning(f"No status from client for download {download_id}")
                return
            
            state_value = str(status.get('state') or '').lower()
            is_seeding = state_value in self.ACTIVE_SEEDING_STATES
            is_complete = (status.get('progress', 0) or 0) >= 100.0
            
            ratio = status.get('ratio', 0.0)
            seeding_time = status.get('seeding_time', 0)
            
            self.logger.debug(
                f"Download {download_id} seeding: ratio={ratio:.2f}, "
                f"time={seeding_time}s, state={status.get('state')}"
            )
            
            try:
                queue_manager.update_download(download_id, {
                    'seeding_ratio': ratio,
                    'seeding_time_seconds': seeding_time
                })
            except Exception as update_error:
                self.logger.debug(
                    "Skipping seeding metric update for download %s: %s",
                    download_id,
                    update_error
                )
            self._cache_download(download_id, download)
            
            client_marked_complete = False
            completion_reason = None
            if hasattr(client, 'is_seeding_complete'):
                client_marked_complete = client.is_seeding_complete(status)
                if client_marked_complete:
                    completion_reason = 'client'
            else:
                if is_complete and not is_seeding:
                    client_marked_complete = True
                    completion_reason = 'fallback_state'

            if client_marked_complete:
                ratio_limit = status.get('seed_ratio_limit')
                time_limit = status.get('seed_time_limit_seconds')
                self.logger.info(
                    "Download %s seeding complete via %s (state=%s, ratio=%.2f/%s, time=%ss/%s, progress=%.2f)",
                    download_id,
                    completion_reason or 'unknown',
                    status.get('state'),
                    ratio,
                    self._format_ratio_limit(ratio_limit),
                    seeding_time,
                    self._format_time_limit(time_limit),
                    status.get('progress', 0.0)
                )
                self._complete_seeding_transition(
                    download_id,
                    download,
                    ratio=ratio,
                    seeding_time=seeding_time
                )
            
        except Exception as e:
            self.logger.error(f"Error monitoring seeding for download {download_id}: {e}", exc_info=True)
            import traceback
            self.logger.error(traceback.format_exc(), exc_info=True)

    def _complete_seeding_transition(self, download_id: int, download: Dict[str, Any],
                                     ratio: float = 0.0, seeding_time: int = 0):
        """Shared helper to finish seeding workflow and cleanup."""
        state_machine = self._get_state_machine()
        if state_machine.transition(download_id, 'SEEDING_COMPLETE'):
            event_emitter = self._get_event_emitter()
            event_emitter.emit_seeding_completed(download_id)
            event_emitter.emit_download_completed(download_id)
            self._handle_seeding_complete(download_id, download)

            queue_manager = self._get_queue_manager()
            try:
                queue_manager.delete_download(download_id)
                event_emitter.emit_queue_updated()
                self.logger.info(
                    f"Download {download_id} removed from queue after seeding:"
                    f" '{download.get('book_title', 'Unknown')}' (ASIN: {download.get('book_asin')})"
                )
            except Exception as delete_error:
                self.logger.error(
                    f"Failed to delete download {download_id} after seeding complete: {delete_error}", exc_info=True
                )
            finally:
                self._last_known_downloads.pop(download_id, None)
                self._clear_seeding_missing_count(download_id)
    
    def _handle_seeding_complete(self, download_id: int, download: Dict[str, Any]):
        """Handle cleanup after seeding completes."""
        try:
            cleanup_manager = self._get_cleanup_manager()
            
            delete_source = download.get('delete_source', False)
            
            cleanup_manager.finalize_seeding(
                download_id,
                download,
                delete_files=delete_source
            )

            if delete_source:
                self.logger.debug(
                    f"Download {download_id} - deleted torrent data after seeding"
                )
            else:
                self.logger.debug(
                    f"Download {download_id} - kept source files, removed torrent reference"
                )

            self.logger.debug(f"Seeding cleanup complete for download {download_id}")
            
        except Exception as e:
            self.logger.error(f"Error handling seeding complete for download {download_id}: {e}", exc_info=True)
            import traceback
            self.logger.error(traceback.format_exc(), exc_info=True)
