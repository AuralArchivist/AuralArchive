"""
Module Name: audible_download.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Audible downloads: the worker pool, cancellation, and the download of
    an owned book from Audible.

Location:
    /services/acquisition/audible_download.py
"""

import asyncio
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from services.container import container


class AudibleDownloadMixin:
    """Audible download workers. Mixed into DownloadManagementService."""

    def _configure_audible_concurrency(self):
        """Configure Audible download concurrency based on [audible] config."""
        max_workers = 1
        try:
            config_service = container.get_config_service()
            audible_config = config_service.get_section('audible') or {}
            raw_value = (
                audible_config.get('concurrent_downloads')
                or audible_config.get('max_concurrent_downloads')
                or audible_config.get('download_concurrency')
            )
            if raw_value is None:
                raw_value = 1
            max_workers = int(raw_value)
        except Exception as exc:
            self.logger.debug(f"Falling back to single Audible worker: {exc}")
            max_workers = 1

        if max_workers < 1:
            max_workers = 1
        elif max_workers > 8:
            max_workers = 8

        if max_workers != self._audible_max_workers or self._audible_executor is None:
            self.logger.debug(
                "Configuring Audible concurrency: previous=%s, new=%s",
                self._audible_max_workers,
                max_workers,
            )
            self._reset_audible_executor(max_workers)

    def _reset_audible_executor(self, max_workers: Optional[int] = None):
        """Recreate the Audible download executor with a new worker count."""
        if max_workers is None:
            max_workers = self._audible_max_workers or 1

        with self._audible_executor_lock:
            if self._audible_executor:
                try:
                    self._audible_executor.shutdown(wait=False)
                except Exception as exc:
                    self.logger.debug(f"Error shutting down Audible executor: {exc}")
                finally:
                    self._audible_executor = None

            self._audible_max_workers = max_workers
            self._audible_executor = ThreadPoolExecutor(
                max_workers=self._audible_max_workers,
                thread_name_prefix="AudibleDownload"
            )

    def set_audible_concurrency(self, max_workers: int) -> int:
        """Public helper to adjust Audible download worker count."""
        try:
            requested = int(max_workers)
        except (TypeError, ValueError):
            requested = 1

        if requested < 1:
            requested = 1
        elif requested > 8:
            requested = 8

        self._reset_audible_executor(requested)
        self.logger.debug("Audible concurrency updated to %s worker(s)", requested)
        return requested

    def _ensure_audible_executor(self) -> ThreadPoolExecutor:
        """Return an active executor for Audible downloads."""
        with self._audible_executor_lock:
            if self._audible_executor is None:
                self._audible_executor = ThreadPoolExecutor(
                    max_workers=self._audible_max_workers or 1,
                    thread_name_prefix="AudibleDownload"
                )
            return self._audible_executor

    def _schedule_audible_download(self, download_id: int, download: Dict[str, Any]):
        """Submit an Audible download task to the worker pool."""
        cancel_event = self._register_audible_download(download_id)
        executor = self._ensure_audible_executor()

        future = executor.submit(
            self._run_audible_download,
            download_id,
            download,
            cancel_event,
        )

        def _log_future_done(fut):
            try:
                fut.result()
            except Exception as exc:
                self.logger.error(f"Audible download task for {download_id} raised: {exc}", exc_info=True)

        future.add_done_callback(_log_future_done)

        with self._audible_context_lock:
            context = self._audible_download_context.get(download_id, {})
            context['future'] = future
            self._audible_download_context[download_id] = context

    def _register_audible_download(self, download_id: int) -> threading.Event:
        """Create and track a cancellation token for an Audible download."""
        cancel_event = threading.Event()
        with self._audible_context_lock:
            self._audible_download_context[download_id] = {
                'cancel_event': cancel_event,
                'registered_at': datetime.utcnow().isoformat()
            }
        return cancel_event

    def _request_audible_cancel(self, download_id: int):
        """Signal any in-flight Audible download to cancel cooperatively."""
        with self._audible_context_lock:
            context = self._audible_download_context.get(download_id)

        if not context:
            return

        cancel_event = context.get('cancel_event')
        if cancel_event and not cancel_event.is_set():
            self.logger.info("Cancellation requested for Audible download %s", download_id)
            cancel_event.set()

    def _clear_audible_context(self, download_id: int):
        """Remove Audible download tracking metadata once finished."""
        with self._audible_context_lock:
            removed = self._audible_download_context.pop(download_id, None)

        if removed:
            self.logger.debug("Audible download %s context cleared", download_id)

    def _run_audible_download(self, download_id: int, download: Dict[str, Any], cancel_event: Optional[threading.Event] = None):
        """Worker function to download an Audible audiobook."""
        temp_path: Optional[str] = None

        try:
            current_status = (download.get('status') or '').upper()
            if current_status == 'CANCELLED':
                self.logger.info(
                    "Audible download %s cancellation detected before start; skipping",
                    download_id
                )
                return

            if current_status != 'AUDIBLE_DOWNLOADING':
                self.logger.debug(
                    "Audible download %s invoked while status=%s; ignoring",
                    download_id,
                    current_status or 'UNKNOWN'
                )
                return

            book_asin = download.get('book_asin')
            if not book_asin:
                raise ValueError("Missing book_asin for Audible download")
            
            self.logger.info(f"Starting Audible download for ASIN: {book_asin}")
            
            temp_path = os.path.join(self.audible_dl_path, str(download_id))
            os.makedirs(temp_path, exist_ok=True)

            file_format_field = (download.get('file_format') or '').strip()
            config_defaults = {}
            try:
                config_service = container.get_config_service()
                config_defaults = config_service.get_section('audible') or {}
            except Exception as cfg_error:
                self.logger.debug(f"Unable to load Audible config defaults: {cfg_error}")

            config_format = str(config_defaults.get('download_format', 'aaxc')).lower()
            config_quality = str(config_defaults.get('download_quality', 'best')).lower()
            fallback_flag_raw = str(config_defaults.get('aax_fallback_enabled', 'true')).strip().lower()
            fallback_enabled_default = fallback_flag_raw in {'true', '1', 'yes', 'on'}

            valid_formats = {'aax', 'aaxc', 'aax-fallback'}
            valid_qualities = {'best', 'high', 'normal'}

            preferred_format = config_format if config_format in valid_formats else 'aaxc'
            preferred_quality = config_quality if config_quality in valid_qualities else 'best'

            if file_format_field:
                parts = [segment.strip().lower() for segment in file_format_field.split(':') if segment.strip()]
                if parts:
                    if parts[0] in valid_formats:
                        preferred_format = parts[0]
                    if len(parts) > 1 and parts[1] in valid_qualities:
                        preferred_quality = parts[1]

            download_format = preferred_format
            aax_fallback_enabled = fallback_enabled_default

            if download_format == 'aax-fallback':
                download_format = 'aax'
                aax_fallback_enabled = True
            elif download_format == 'aax':
                aax_fallback_enabled = fallback_enabled_default
            else:
                aax_fallback_enabled = True

            self.logger.info(
                "Audible download %s preferences → format=%s, quality=%s, fallback=%s",
                download_id,
                download_format,
                preferred_quality,
                aax_fallback_enabled
            )
            
            from integrations.audible.download.audible_download_helper import AudibleDownloadHelper
            
            start_time = datetime.now()
            last_logged_pct = {'value': -10.0}

            def on_progress(downloaded_bytes: int, total_bytes: int, message: str):
                if total_bytes <= 0 or (cancel_event and cancel_event.is_set()):
                    return

                progress_pct = (downloaded_bytes / total_bytes) * 100
                self.queue_manager.update_download(download_id, {
                    'download_progress': progress_pct,
                    'updated_at': datetime.now().isoformat()
                })
                self.event_emitter.emit_progress(download_id, progress_pct, message)

                if progress_pct >= last_logged_pct['value'] + 10.0 or downloaded_bytes >= total_bytes:
                    elapsed = (datetime.now() - start_time).total_seconds() or 1.0
                    downloaded_mb = downloaded_bytes / (1024 * 1024)
                    total_mb = total_bytes / (1024 * 1024)
                    speed_mb_s = downloaded_mb / elapsed
                    self.logger.debug(
                        "Audible download %s progress %.1f%% (%.2f / %.2f MB, %.2f MB/s)",
                        download_id,
                        progress_pct,
                        downloaded_mb,
                        total_mb,
                        speed_mb_s
                    )
                    last_logged_pct['value'] = progress_pct
            
            download_helper = AudibleDownloadHelper(progress_callback=on_progress, cancel_event=cancel_event)
            
            db_service = container.get_database_service()
            book_data = db_service.books.get_book_by_asin(book_asin)
            title = book_data.get('Title', book_asin) if book_data else book_asin
            
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()

            result = download_helper.download_book(
                asin=book_asin,
                output_dir=Path(temp_path),
                filename=safe_title,
                format_preference=download_format,
                quality=preferred_quality,
                aax_fallback=aax_fallback_enabled
            )
            
            if not result.get('success'):
                raise Exception(result.get('error', 'Audible download failed'))
            
            audio_file = result.get('audio_file')
            voucher_file = result.get('voucher_file')
            file_format = result.get('format', 'aaxc').upper()
            
            total_elapsed = (datetime.now() - start_time).total_seconds() or 1.0
            total_size_mb = os.path.getsize(audio_file) / (1024 * 1024) if audio_file and os.path.exists(audio_file) else 0.0
            avg_speed_mb_s = total_size_mb / total_elapsed

            self.logger.success(
                "Audible download completed successfully",
                extra={
                    "audio_file": audio_file,
                    "elapsed_seconds": round(total_elapsed, 1),
                    "size_mb": round(total_size_mb, 2),
                    "avg_mb_s": round(avg_speed_mb_s, 2),
                },
            )
            
            completion_updates = {
                'temp_file_path': audio_file,
                'voucher_file_path': voucher_file,
                'file_format': file_format.lower(),
                'download_progress': 100.0,
                'completed_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat(),
            }
            self.queue_manager.update_download(download_id, completion_updates)

            if self.state_machine.transition(download_id, 'COMPLETE'):
                self.event_emitter.emit_complete(download_id)
            else:
                current_record = self.queue_manager.get_download(download_id) or {}
                current_status = (current_record.get('status') or '').upper()

                if current_status in {'FOUND', 'AUDIBLE_DOWNLOAD_FAILED'}:
                    self.logger.warning(
                        "Audible download %s finished while queue status was %s; coercing lifecycle",
                        download_id,
                        current_status
                    )

                    if self.state_machine.transition(download_id, 'AUDIBLE_DOWNLOADING'):
                        if self.state_machine.transition(download_id, 'COMPLETE'):
                            self.event_emitter.emit_complete(download_id)
                            self.logger.info(
                                "Audible download %s lifecycle coerced to COMPLETE from FOUND",
                                download_id
                            )
                            return

                elif current_status == 'CANCELLED':
                    self.logger.info(
                        "Audible download %s completed after cancellation request; leaving status CANCELLED",
                        download_id
                    )
                    return

                self.logger.warning(
                    "Audible download %s completed but state transition to COMPLETE was rejected (status=%s)",
                    download_id,
                    current_status or 'UNKNOWN'
                )
                self.queue_manager.update_download(download_id, {'status': 'COMPLETE'})
                self.event_emitter.emit_complete(download_id)
            
            self.logger.info(f"Audible download {download_id} completed successfully")
            
        except asyncio.CancelledError:
            self.logger.info("Audible download %s cancelled before completion", download_id)

            if temp_path and os.path.isdir(temp_path):
                shutil.rmtree(temp_path, ignore_errors=True)

            existing_record = self.queue_manager.get_download(download_id)
            if existing_record:
                self.queue_manager.update_download(download_id, {
                    'download_progress': None,
                    'temp_file_path': None,
                    'voucher_file_path': None,
                    'updated_at': datetime.now().isoformat()
                })
                self.state_machine.transition(download_id, 'CANCELLED')
                self.event_emitter.emit_download_cancelled(download_id)

        except Exception as e:
            self.logger.error(f"Audible download failed for {download_id}: {e}", exc_info=True)
            self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='AUDIBLE_DOWNLOAD_FAILED',
                    error_message=str(e)
                )
        finally:
            self._clear_audible_context(download_id)
