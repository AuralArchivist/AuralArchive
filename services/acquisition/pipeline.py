"""
Module Name: pipeline.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    The download monitor pass: moves each queued download to its next stage
    and polls active downloads. Runs as the download monitor background task.

Location:
    /services/acquisition/pipeline.py
"""

import shutil
import time
from datetime import datetime
from typing import Any, Dict

from core.scheduler import get_scheduler

MONITOR_TASK = 'download_monitor'
MONITOR_ERROR_DELAY_SECONDS = 5


class PipelineMixin:
    """Monitor pass and the start/stop controls for its task. Mixed into DownloadManagementService."""

    @property
    def monitor_running(self) -> bool:
        """Whether the download monitor task is active."""
        return get_scheduler().is_active(MONITOR_TASK)

    def start_monitoring(self):
        """Resume the download monitor task."""
        if get_scheduler().resume(MONITOR_TASK):
            self.logger.debug("Download monitor started")

    def stop_monitoring(self):
        """Pause the download monitor task."""
        if get_scheduler().pause(MONITOR_TASK):
            self.logger.debug("Download monitor stopped")

    @property
    def monitoring_active(self) -> bool:
        """Expose monitor thread state for status reporting endpoints."""
        return self.monitor_running

    def monitor_tick(self):
        """One monitor pass: queue, active downloads, pipeline, and an hourly cleanup sweep."""
        try:
            self._process_queue()
            self._monitor_downloads()
            self._process_pipeline()

            if time.monotonic() >= self._next_work_sweep:
                self._next_work_sweep = time.monotonic() + 3600
                try:
                    removed = self.cleanup_manager.cleanup_old_failed_downloads(days=self.retention_days)
                    self.cleanup_manager.sweep_work_dirs(retention_days=self.retention_days)
                    if removed:
                        self.event_emitter.emit_queue_updated()
                except Exception:
                    self.logger.exception("Work folder sweep failed")
            self._monitor_failed = False
        except Exception:
            self._monitor_failed = True
            self.logger.exception("Error in download monitor loop")

    def monitor_interval(self) -> float:
        """Seconds until the next monitor pass (longer after a failed pass)."""
        return MONITOR_ERROR_DELAY_SECONDS if self._monitor_failed else self.polling_interval

    def _ensure_monitor_running(self):
        """Start monitoring thread if not already active."""
        if not self.monitor_running:
            self.start_monitoring()

    def _is_retry_ready(self, download: Dict[str, Any]) -> bool:
        """Return True when the download is eligible for another retry attempt."""
        next_retry_at = download.get('next_retry_at')
        if not next_retry_at:
            return True

        try:
            retry_time = datetime.fromisoformat(next_retry_at)
        except ValueError:
            return True

        return retry_time <= datetime.utcnow()

    def _process_queue(self):
        """Process queued items - transition QUEUED → SEARCHING/FOUND → DOWNLOADING."""
        queued_items = self.queue_manager.get_queue(status_filter='QUEUED')
        active_searches = self._count_active_searches()

        for item in queued_items:
            try:
                if not self._is_retry_ready(item):
                    continue

                download_type = (item.get('download_type') or '').strip().lower()

                if download_type == 'audible':
                    self.logger.info(
                        "Download %s is Audible content; launching dedicated Audible pipeline",
                        item['id']
                    )

                    updates: Dict[str, Any] = {}
                    if not item.get('indexer'):
                        updates['indexer'] = 'Audible'
                    if item.get('last_error'):
                        updates['last_error'] = None
                    if item.get('next_retry_at'):
                        updates['next_retry_at'] = None

                    if updates:
                        self.queue_manager.update_download(item['id'], updates)

                    if self.state_machine.transition(item['id'], 'AUDIBLE_DOWNLOADING'):
                        self.event_emitter.emit_download_started(item['id'])
                        refreshed = self.queue_manager.get_download(item['id']) or item
                        self._schedule_audible_download(item['id'], refreshed)
                    else:
                        self.logger.debug(
                            "Audible download %s already in progress; skipping start",
                            item['id']
                        )
                    continue
                if item.get('search_result_id'):
                    self.logger.info("Download %s using pre-selected search result; scheduling download", item['id'])
                    if self.state_machine.transition(item['id'], 'FOUND'):
                        self.event_emitter.emit_search_found(item['id'], source=item.get('indexer'))
                else:
                    if active_searches >= self.max_active_searches:
                        self.logger.debug(
                            "Reached search concurrency limit (%s); deferring remaining queued items",
                            self.max_active_searches
                        )
                        break
                    self.logger.info("Download %s entering SEARCHING stage", item['id'])
                    self.state_machine.transition(item['id'], 'SEARCHING')
                    self._submit_search(item['id'])
                    active_searches += 1
                    
            except Exception as e:
                self.logger.error(f"Error processing queued item {item['id']}: {e}", exc_info=True)

        searching_items = self.queue_manager.get_queue(status_filter='SEARCHING')
        for item in searching_items:
            try:
                if not self._is_retry_ready(item):
                    continue
                with self._searches_in_flight_lock:
                    already_in_flight = item['id'] in self._searches_in_flight
                if already_in_flight:
                    continue
                self.logger.info(
                    "Download %s re-submitting stalled search (retry %s)",
                    item['id'], item.get('retry_count')
                )
                self._submit_search(item['id'])
            except Exception as e:
                self.logger.error(f"Error re-submitting search for {item['id']}: {e}", exc_info=True)

        found_items = self.queue_manager.get_queue(status_filter='FOUND')
        active_downloads = self._count_active_downloads()

        if found_items and not self._has_sufficient_disk_space():
            found_items = []

        for item in found_items:
            try:
                if not self._is_retry_ready(item):
                    continue

                download_type = (item.get('download_type') or '').strip().lower()
                if download_type == 'audible':
                    self.logger.debug(
                        "Found-stage handler skipping Audible download %s; handled via dedicated pipeline",
                        item['id']
                    )
                    continue

                if active_downloads >= self.max_concurrent_downloads:
                    self.logger.debug(
                        "Reached download concurrency limit (%s); deferring remaining FOUND items",
                        self.max_concurrent_downloads
                    )
                    break

                self.logger.info("Download %s entering DOWNLOADING stage (%s)", item['id'], item.get('book_title', 'Unknown'))
                
                source_info = {
                    'download_url': item.get('download_url'),
                    'download_type': item.get('download_type', 'torrent'),
                    'indexer': item.get('indexer'),
                    'info_hash': item.get('info_hash')
                }

                if item.get('next_retry_at'):
                    self.queue_manager.update_download(item['id'], {'next_retry_at': None})

                if not self.state_machine.transition(item['id'], 'DOWNLOADING'):
                    self.logger.debug(
                        "Download %s could not transition FOUND→DOWNLOADING (already moved); skipping",
                        item['id']
                    )
                    continue

                self.event_emitter.emit_download_started(item['id'])

                self._start_download(item['id'], source_info)
                active_downloads += 1

            except Exception:
                self.logger.exception("Error starting download for FOUND item %s", item['id'])

    def _count_active_downloads(self) -> int:
        try:
            downloading_items = self.queue_manager.get_queue(status_filter='DOWNLOADING')
            return len(downloading_items)
        except Exception as exc:
            self.logger.debug("Unable to count active downloads: %s", exc)
            return 0

    def _has_sufficient_disk_space(self) -> bool:
        """Check free space at the downloads staging path against the
        configured floor. Only gates whether NEW downloads may start - never
        triggers cleanup/eviction of anything already in progress."""
        if self.skip_free_space_check:
            return True

        try:
            unit_multipliers = {'MB': 1024 ** 2, 'GB': 1024 ** 3, 'TB': 1024 ** 4}
            required_bytes = self.minimum_free_space * unit_multipliers.get(self.free_space_unit, 1024 ** 2)
            free_bytes = shutil.disk_usage(self.downloads_path).free

            if free_bytes < required_bytes:
                self.logger.warning(
                    "Disk space below configured floor; refusing new downloads this tick",
                    extra={
                        "downloads_path": self.downloads_path,
                        "free_bytes": free_bytes,
                        "required_bytes": required_bytes,
                        "minimum_free_space": self.minimum_free_space,
                        "free_space_unit": self.free_space_unit,
                    },
                )
                return False
            return True

        except Exception as exc:
            self.logger.warning("Unable to check disk space; allowing downloads to proceed: %s", exc, exc_info=True)
            return True

    def _count_active_searches(self) -> int:
        try:
            searching_items = self.queue_manager.get_queue(status_filter='SEARCHING')
            return len(searching_items)
        except Exception as exc:
            self.logger.debug("Unable to count active searches: %s", exc)
            return 0

    def _monitor_downloads(self):
        """Monitor active downloads - poll clients for progress."""
        active_downloads = self.queue_manager.get_queue(status_filter='DOWNLOADING')
        
        for download in active_downloads:
            try:
                if (download.get('download_type') or '').strip().lower() == 'audible':
                    self.logger.debug(
                        "Skipping monitor poll for Audible download %s; progress handled via API callbacks",
                        download['id']
                    )
                    continue
                self.download_monitor.update_progress(download['id'])
            except Exception:
                self.logger.exception("Error monitoring download %s", download['id'])

    def _process_pipeline(self):
        """
        Process completed stages - trigger next pipeline stage.
        
        Critical: Conversion is ONLY needed for Audible downloads (AAX/AAXC format).
        Torrent/NZB downloads are already in M4B/MP3 format and skip directly to import.
        """
        complete_items = self.queue_manager.get_queue(status_filter='COMPLETE')
        for item in complete_items:
            try:
                raw_download_url = item.get('download_url')
                download_url = (raw_download_url or '').strip()
                raw_file_format = item.get('file_format')
                file_format = (raw_file_format or '').strip().lower()
                temp_file_path = (item.get('temp_file_path') or '').strip()
                
                needs_conversion = (
                    'audible.com' in download_url.lower() or
                    file_format in ('aax', 'aaxc') or
                    download_url.endswith('.aax') or
                    download_url.endswith('.aaxc') or
                    (temp_file_path and (temp_file_path.endswith('.aax') or temp_file_path.endswith('.aaxc')))
                )
                
                if needs_conversion:
                    self.logger.info(f"Download {item['id']} is Audible format (AAX/AAXC) - starting FFmpeg conversion to M4B")
                    self._submit_conversion(item['id'], is_mp3=False)
                elif self.convert_mp3_to_m4b and self._download_has_mp3(item):
                    self.logger.info(f"Download {item['id']} contains MP3 files - merging into a single M4B before import")
                    self._submit_conversion(item['id'], is_mp3=True)
                else:
                    self.logger.info(f"Download {item['id']} is torrent/NZB - skipping conversion, proceeding to import")
                    self._submit_import(item['id'])

            except Exception as e:
                self.logger.error(f"Error processing completed item {item['id']}: {e}", exc_info=True)

        converted_items = self.queue_manager.get_queue(status_filter='CONVERTED')
        for item in converted_items:
            try:
                self._submit_import(item['id'])
            except Exception as e:
                self.logger.error(f"Error starting import for {item['id']}: {e}", exc_info=True)
        
        seeding_items = self.queue_manager.get_queue(status_filter='SEEDING')

        if self.monitor_seeding_enabled:
            for item in seeding_items:
                try:
                    self.download_monitor.monitor_seeding(item['id'])
                except Exception as e:
                    self.logger.error(f"Error monitoring seeding for {item['id']}: {e}", exc_info=True)
        else:
            for item in seeding_items:
                try:
                    if self.cleanup_manager.check_seeding_complete(item['id'], item):
                        self.logger.info(f"Seeding complete for download {item['id']}, finalizing...")

                        self.cleanup_manager.finalize_seeding(
                            download_id=item['id'],
                            download_data=item,
                            delete_files=self.delete_source_after_import
                        )

                        if self.state_machine.transition(item['id'], 'SEEDING_COMPLETE'):
                            self.event_emitter.emit_seeding_completed(item['id'])
                            self.event_emitter.emit_download_completed(item['id'])
                            self.logger.info(f"Download {item['id']} marked as SEEDING_COMPLETE")
                            try:
                                self.queue_manager.delete_download(item['id'])
                                self.event_emitter.emit_queue_updated()
                                self.logger.info(
                                    f"Download {item['id']} removed from queue after seeding:"
                                    f" '{item.get('book_title', 'Unknown')}' (ASIN: {item.get('book_asin')})"
                                )
                            except Exception as delete_error:
                                self.logger.error(
                                    "Failed to remove download %s from queue after seeding: %s",
                                    item['id'],
                                    delete_error, exc_info=True
                                )

                except Exception as e:
                    self.logger.error(f"Error processing seeding item {item['id']}: {e}", exc_info=True)
