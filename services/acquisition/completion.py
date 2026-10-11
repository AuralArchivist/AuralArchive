"""
Module Name: completion.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Finished downloads: converts Audible and MP3 downloads to M4B, then
    imports the book into its library.

Location:
    /services/acquisition/completion.py
"""

import glob
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from loguru import logger as loguru_logger
from services.container import container


def _find_audio_files(base_path: Optional[str], extensions) -> List[str]:
    """Return all audio files under base_path matching the extensions."""
    if not base_path:
        return []
    exts = tuple(e.lower() for e in extensions)
    if os.path.isfile(base_path):
        return [base_path] if base_path.lower().endswith(exts) else []
    if not os.path.isdir(base_path):
        return []

    matches: List[str] = []
    for root, _dirs, files in os.walk(base_path):
        for name in files:
            if name.lower().endswith(exts):
                matches.append(os.path.join(root, name))
    return sorted(matches)


def _find_cover_image(base_path: Optional[str]) -> Optional[str]:
    """Find a likely cover image (cover/folder art) in the download dir."""
    if not base_path or not os.path.isdir(base_path):
        return None
    preferred = ('cover.jpg', 'cover.jpeg', 'cover.png', 'folder.jpg', 'folder.png')
    fallback: Optional[str] = None
    for root, _dirs, files in os.walk(base_path):
        lowered = {name.lower(): name for name in files}
        for pref in preferred:
            if pref in lowered:
                return os.path.join(root, lowered[pref])
        if fallback is None:
            for name in files:
                if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    fallback = os.path.join(root, name)
                    break
    return fallback


def _find_downloaded_file(base_path: Optional[str]) -> Optional[str]:
    """Find the actual audiobook file in the download directory."""
    if not base_path:
        return None

    if os.path.isfile(base_path):
        return base_path
    if not os.path.isdir(base_path):
        return None

    extensions = ['*.m4b', '*.m4a', '*.mp3', '*.aax', '*.aaxc', '*.flac', '*.ogg', '*.wav']

    for ext in extensions:
        files = glob.glob(os.path.join(base_path, '**', ext), recursive=True)
        if files:
            return max(files, key=os.path.getsize)

    return None


class CompletionMixin:
    """Conversion and import stages of the download pipeline. Mixed into DownloadManagementService."""

    def _get_conversion_executor(self) -> ThreadPoolExecutor:
        """Lazy-create the conversion ThreadPoolExecutor (max 2 workers)."""
        if self._conversion_executor is None:
            with self._conversion_executor_lock:
                if self._conversion_executor is None:
                    self._conversion_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="convert")
        return self._conversion_executor

    def _submit_conversion(self, download_id: int, *, is_mp3: bool):
        """Run a conversion off the monitor loop so it doesn't block download polling.

        Guarded by an in-flight set because _process_pipeline runs every poll and
        the item stays in COMPLETE until the worker transitions it to CONVERTING.
        """
        with self._conversions_in_flight_lock:
            if download_id in self._conversions_in_flight:
                return
            self._conversions_in_flight.add(download_id)

        def _run():
            try:
                if is_mp3:
                    self._start_mp3_conversion(download_id)
                else:
                    self._start_conversion(download_id)
            finally:
                with self._conversions_in_flight_lock:
                    self._conversions_in_flight.discard(download_id)

        self._get_conversion_executor().submit(_run)

    def _get_import_executor(self) -> ThreadPoolExecutor:
        """Lazy-create the import ThreadPoolExecutor (max 2 workers), same pattern
        as the conversion executor. Bounds how many imports (file move/copy +
        library metadata write) run at once so a batch of downloads completing
        together can't pile up unbounded disk I/O/CPU."""
        if self._import_executor is None:
            with self._import_executor_lock:
                if self._import_executor is None:
                    self._import_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="import")
        return self._import_executor

    def _submit_import(self, download_id: int):
        """Run an import off the monitor loop so it doesn't block search submission
        or download-progress polling for everything else.

        Guarded by an in-flight set because _process_pipeline runs every poll and
        the item stays in COMPLETE/CONVERTED until the worker transitions it to
        IMPORTING.
        """
        with self._imports_in_flight_lock:
            if download_id in self._imports_in_flight:
                return
            self._imports_in_flight.add(download_id)

        def _run():
            try:
                self._start_import(download_id)
            finally:
                with self._imports_in_flight_lock:
                    self._imports_in_flight.discard(download_id)

        self._get_import_executor().submit(_run)

    def _conversion_progress_callback(self, download_id: int):
        """Callback that reports conversion progress to the UI and the queue."""
        def progress_callback(message: str, progress: int):
            self.progress_tracker.emit_progress(
                download_id=download_id,
                progress_percentage=progress,
                message=message
            )
            self.event_emitter.emit_conversion_progress(download_id, float(progress), message)
            try:
                self.queue_manager.update_download(download_id, {'download_progress': float(progress)})
            except Exception:
                self.logger.debug("Could not save progress for download %s", download_id, exc_info=True)
        return progress_callback

    def _start_conversion(self, download_id: int):
        """Start FFmpeg conversion for completed download."""
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                self.logger.error(f"Download {download_id} not found")
                return
            
            book_asin = download.get('book_asin')
            temp_file_path = download.get('temp_file_path')
            voucher_file_path = download.get('voucher_file_path')
            
            self.logger.info(f"Starting conversion for download {download_id} (ASIN: {book_asin})")
            
            if not temp_file_path or not os.path.exists(temp_file_path):
                error_msg = f"Downloaded file not found at {temp_file_path}"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='CONVERSION_FAILED',
                    error_message=error_msg
                )
                return
            
            if not self.state_machine.transition(download_id, 'CONVERTING'):
                self.logger.error(f"Failed to transition download {download_id} to CONVERTING state")
                return
            
            self.event_emitter.emit_conversion_started(download_id)
            
            db_service = container.get_database_service()
            book_data = db_service.books.get_book_by_asin(book_asin)
            
            actual_file = _find_downloaded_file(temp_file_path)
            if not actual_file:
                error_msg = f"Could not locate downloaded audiobook file in {temp_file_path}"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='CONVERSION_FAILED',
                    error_message=error_msg
                )
                return

            if Path(actual_file).suffix.lower() == '.aaxc':
                if not voucher_file_path:
                    error_msg = (
                        f"Voucher file missing for AAXC download {download_id}; "
                        "conversion cannot continue"
                    )
                    self.logger.error(error_msg)
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='CONVERSION_FAILED',
                        error_message=error_msg
                    )
                    return
                if not os.path.exists(voucher_file_path):
                    error_msg = (
                        f"Voucher file not found at {voucher_file_path} for AAXC "
                        f"download {download_id}"
                    )
                    self.logger.error(error_msg)
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='CONVERSION_FAILED',
                        error_message=error_msg
                    )
                    return
            
            conversion_temp_dir = os.path.join(self.conversion_path, f"convert_{download_id}")
            os.makedirs(conversion_temp_dir, exist_ok=True)
            
            output_filename = f"{book_data.get('Title', 'audiobook')}.m4b"
            output_file = os.path.join(conversion_temp_dir, output_filename)
            
            conversion_service = container.get_conversion_service()

            progress_callback = self._conversion_progress_callback(download_id)

            conversion_result = conversion_service.convert_audiobook(
                input_file=actual_file,
                output_file=output_file,
                progress_callback=progress_callback,
                metadata=book_data,
                voucher_file=voucher_file_path
            )

            if not conversion_result.get('success'):
                error_msg = conversion_result.get('error', 'Conversion failed')
                self.logger.error(f"Conversion failed for {download_id}: {error_msg}")
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='CONVERSION_FAILED',
                    error_message=error_msg
                )
                return
            
            converted_file_path = conversion_result.get('output_file', output_file)
            
            self.queue_manager.update_download(download_id, {
                'converted_file_path': converted_file_path,
                'original_file_path': actual_file,
                'last_error': None
            })
            
            if self.state_machine.transition(download_id, 'CONVERTED'):
                self.event_emitter.emit_conversion_completed(download_id)
                self.logger.success(
                    "Conversion completed successfully",
                    extra={"download_id": download_id, "converted_file": converted_file_path},
                )
                self._submit_import(download_id)
            
        except Exception as e:
            self.logger.exception("Error during conversion for download %s", download_id)
            self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='CONVERSION_FAILED',
                    error_message=str(e)
                )

    def _download_has_mp3(self, item: Dict[str, Any]) -> bool:
        """Return True when the download's files include at least one MP3."""
        base_path = (item.get('temp_file_path') or '').strip()
        return bool(_find_audio_files(base_path, ('.mp3',)))

    def _start_mp3_conversion(self, download_id: int):
        """Merge a completed multi-file MP3 download into a single chaptered M4B."""
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                self.logger.error(f"Download {download_id} not found")
                return

            book_asin = download.get('book_asin')
            temp_file_path = download.get('temp_file_path')

            mp3_files = _find_audio_files(temp_file_path, ('.mp3',))
            if not mp3_files:
                self.logger.info(f"Download {download_id} has no MP3 files; importing as-is")
                self._submit_import(download_id)
                return

            conversion_service = container.get_conversion_service()

            if not conversion_service.ffmpeg_available():
                self.logger.warning(
                    f"ffmpeg/ffprobe not available - skipping MP3->M4B merge for "
                    f"download {download_id}; importing original files instead."
                )
                self._submit_import(download_id)
                return

            self.logger.info(
                f"Starting MP3->M4B merge for download {download_id} "
                f"(ASIN: {book_asin}, files: {len(mp3_files)})"
            )

            if not self.state_machine.transition(download_id, 'CONVERTING'):
                self.logger.error(f"Failed to transition download {download_id} to CONVERTING state")
                return

            self.event_emitter.emit_conversion_started(download_id)

            db_service = container.get_database_service()
            book_data = db_service.books.get_book_by_asin(book_asin) if book_asin else None

            conversion_temp_dir = os.path.join(self.conversion_path, f"convert_{download_id}")
            os.makedirs(conversion_temp_dir, exist_ok=True)

            title = (book_data or {}).get('Title') or download.get('book_title') or f'audiobook_{download_id}'
            safe_title = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in str(title)).strip() or f'audiobook_{download_id}'
            output_file = os.path.join(conversion_temp_dir, f"{safe_title}.m4b")

            cover_file = _find_cover_image(temp_file_path)

            progress_callback = self._conversion_progress_callback(download_id)

            conversion_result = conversion_service.combine_mp3_to_m4b(
                input_files=mp3_files,
                output_file=output_file,
                asin=book_asin,
                metadata=book_data,
                cover_file=cover_file,
                progress_callback=progress_callback,
            )

            if not conversion_result.get('success'):
                error_msg = conversion_result.get('error', 'MP3 merge failed')
                self.logger.error(f"MP3 merge failed for {download_id}: {error_msg}")
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='CONVERSION_FAILED',
                    error_message=error_msg
                )
                return

            converted_file_path = conversion_result.get('output_file', output_file)

            self.queue_manager.update_download(download_id, {
                'converted_file_path': converted_file_path,
                'original_file_path': temp_file_path,
                'last_error': None
            })

            if self.state_machine.transition(download_id, 'CONVERTED'):
                self.event_emitter.emit_conversion_completed(download_id)
                self.logger.success(
                    "MP3->M4B merge completed",
                    extra={
                        "download_id": download_id,
                        "converted_file": converted_file_path,
                        "chapter_source": conversion_result.get('chapter_source'),
                        "chapter_count": conversion_result.get('chapter_count'),
                    },
                )
                self._submit_import(download_id)

        except Exception:
            self.logger.exception("Error during MP3->M4B merge for download %s", download_id)
            self.retry_handler.handle_failure(
                download_id=download_id,
                failure_status='CONVERSION_FAILED',
                error_message='MP3->M4B merge raised an exception'
            )

    def _start_import(self, download_id: int):
        """Start import to library for converted file."""
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                self.logger.error(f"Download {download_id} not found")
                return
            
            book_asin = download.get('book_asin')
            download_type = download.get('download_type', 'torrent')
            temp_file_path = download.get('temp_file_path')
            converted_file_path = download.get('converted_file_path')
            
            self.logger.info(f"Starting import for download {download_id} (ASIN: {book_asin})")

            if not converted_file_path or not os.path.exists(converted_file_path):
                resolved_path = None
                if download_type != 'audible' and temp_file_path:
                    if not os.path.exists(temp_file_path):
                        remapped = self._remap_sabnzbd_path(temp_file_path)
                        if remapped != temp_file_path:
                            self.logger.info(
                                "Import for download %s: remapping temp path %s → %s",
                                download_id, temp_file_path, remapped,
                            )
                            temp_file_path = remapped
                            self.queue_manager.update_download(download_id, {'temp_file_path': temp_file_path})
                    resolved_path = _find_downloaded_file(temp_file_path)
                elif temp_file_path and os.path.isfile(temp_file_path):
                    resolved_path = temp_file_path

                if resolved_path and os.path.exists(resolved_path):
                    converted_file_path = resolved_path
                    download['converted_file_path'] = converted_file_path
                    self.queue_manager.update_download(download_id, {
                        'converted_file_path': converted_file_path,
                        'last_error': None
                    })
                    self.logger.info(f"Resolved media path for download {download_id}: {converted_file_path}")
                else:
                    expected_path = converted_file_path or temp_file_path
                    error_msg = (
                        f"Converted media file not found for download {download_id}"
                        f" (expected at {expected_path})"
                    )
                    self.logger.error(error_msg)
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='IMPORT_FAILED',
                        error_message=error_msg
                    )
                    return

            current_status = download.get('status')
            transitioned = self.state_machine.transition(download_id, 'IMPORTING')
            if transitioned:
                self.event_emitter.emit_import_started(download_id)
                download['status'] = 'IMPORTING'
            elif current_status == 'IMPORTING':
                self.logger.debug(f"Download {download_id} already in IMPORTING state; continuing import workflow")
            else:
                self.logger.error(f"Failed to transition download {download_id} to IMPORTING state from {current_status}")
                return
            
            db_service = container.get_database_service()
            book_data = db_service.books.get_book_by_asin(book_asin)
            
            if not book_data:
                error_msg = f"Book data not found for ASIN {book_asin}"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='IMPORT_FAILED',
                    error_message=error_msg
                )
                return
            
            import_service = container.get_import_service()
            
            download_type = download.get('download_type', 'torrent')
            should_move = True
            
            if download_type == 'torrent' and self.seeding_enabled:
                should_move = False
                self.logger.debug(f"Using copy mode for download {download_id} (seeding enabled)")
            else:
                self.logger.debug(f"Using move mode for download {download_id} (download_type={download_type}, seeding={self.seeding_enabled})")
            
            route = container.get_library_router().resolve_for_asin(book_asin, download.get('target_library_id'))
            library = route['library']
            if route['fallback']:
                from services.notifications import notify, TYPE_STORAGE
                notify(TYPE_STORAGE, f"Imported {book_data.get('Title') or book_asin} into {library['name']}",
                       route['reason'], link='/settings#storage', level='warning',
                       dedupe_key=f"library_fallback:{download.get('target_library_id')}")
            success, message, destination_path = import_service.import_book(
                source_file_path=converted_file_path,
                book_data=book_data,
                template_name=library.get('naming_template') or None,
                library_path=library['root'],
                move=should_move,
                import_source='download_manager'
            )
            
            if not success:
                self.logger.error(f"Import failed for {download_id}: {message}")
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='IMPORT_FAILED',
                    error_message=message
                )
                return
            
            loguru_logger.bind(logger_name="Service.DownloadManagement.Service").success(
                f"Import successful for download {download_id}: {destination_path}"
            )
            
            self.queue_manager.update_download(download_id, {
                'final_file_path': destination_path,
                'last_error': None
            })
            
            if self.state_machine.transition(download_id, 'IMPORTED'):
                download['status'] = 'IMPORTED'
                self.event_emitter.emit_import_completed(download_id)
                from services.notifications.events import download_imported
                download_imported(download_id, book_data.get('Title') or download.get('book_title'), destination_path)
                loguru_logger.bind(logger_name="Service.DownloadManagement.Service").success(
                    f"Download {download_id} completed successfully"
                )
            elif download.get('status') == 'IMPORTED':
                self.logger.debug(f"Download {download_id} already marked as IMPORTED; continuing post-import workflow")
            else:
                self.logger.error(f"Download {download_id} could not enter IMPORTED state; skipping cleanup/seeding")
                return

            download_type = download.get('download_type', 'torrent')
            if self.seeding_enabled and download_type == 'torrent':
                self.cleanup_manager.cleanup_after_import(
                    download_id=download_id,
                    download_data=download,
                    seeding=True,
                    delete_source=False
                )

                if self.state_machine.transition(download_id, 'SEEDING'):
                    self.event_emitter.emit_seeding_started(download_id)
                    self.logger.info(f"Download {download_id} entered seeding state")
                else:
                    self.logger.warning(
                        "Download %s could not transition to SEEDING despite seeding being enabled",
                        download_id
                    )
            else:
                self.event_emitter.emit_download_completed(download_id)
                self.cleanup_manager.cleanup_after_import(
                    download_id=download_id,
                    download_data=download,
                    seeding=False,
                    delete_source=True
                )

        except Exception as e:
            self.logger.exception("Error during import for download %s", download_id)
            self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='IMPORT_FAILED',
                    error_message=str(e)
                )
