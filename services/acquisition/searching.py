"""
Module Name: searching.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Search stage: finds a release on the indexers for a queued book, off
    the monitor thread.

Location:
    /services/acquisition/searching.py
"""

from concurrent.futures import ThreadPoolExecutor

from .search.book_matcher import BookMatcher
from services.container import container


class SearchStageMixin:
    """Search stage of the download pipeline. Mixed into DownloadManagementService."""

    def _get_search_executor(self) -> ThreadPoolExecutor:
        """Lazy-create the search ThreadPoolExecutor, sized from max_active_searches.

        Sized once at creation time from whatever max_active_searches is at
        that moment - a ThreadPoolExecutor can't be resized after creation,
        so changing the setting later only takes effect after a restart.
        """
        if self._search_executor is None:
            with self._search_executor_lock:
                if self._search_executor is None:
                    worker_count = max(1, int(self.max_active_searches or 2))
                    self._search_executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="search")
        return self._search_executor

    def _submit_search(self, download_id: int):
        """Submit a search to the off-thread executor, skipping if already in-flight."""
        with self._searches_in_flight_lock:
            if download_id in self._searches_in_flight:
                self.logger.debug("Search already in flight for download %s; skipping", download_id)
                return
            self._searches_in_flight.add(download_id)

        def _run():
            try:
                self._start_search(download_id)
            finally:
                with self._searches_in_flight_lock:
                    self._searches_in_flight.discard(download_id)

        self._get_search_executor().submit(_run)

    def _start_search(self, download_id: int):
        """Initiate search for a book in indexers."""
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                self.logger.error(f"Download {download_id} not found")
                return

            current_type = (download.get('download_type') or '').strip().lower()
            if current_type == 'audible':
                self.logger.info(
                    "Download %s marked as Audible; skipping indexer search and transitioning to FOUND",
                    download_id
                )
                if not download.get('indexer'):
                    self.queue_manager.update_download(download_id, {'indexer': 'Audible', 'last_error': None})
                if self.state_machine.transition(download_id, 'FOUND'):
                    self.event_emitter.emit_search_found(download_id, source='Audible')
                return

            book_asin = download.get('book_asin')
            self.logger.info(f"Starting search for download {download_id} (ASIN: {book_asin})")
            
            db_service = container.get_database_service()
            book_data = db_service.books.get_book_by_asin(book_asin)
            
            if not book_data:
                error_msg = f"Book with ASIN {book_asin} not found in database"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='SEARCH_FAILED',
                    error_message=error_msg
                )
                return
            
            title = book_data.get('Title', '')
            author = book_data.get('Author', '')
            
            if not title or not author:
                error_msg = f"Missing title or author for ASIN {book_asin}"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='SEARCH_FAILED',
                    error_message=error_msg
                )
                return

            self.event_emitter.emit_search_started(download_id, title)

            search_service = container.get_search_engine_service()
            self.logger.info(f"Searching for: {title} by {author}")

            book_series = book_data.get('Series')
            book_sequence = book_data.get('Sequence')
            query_components = BookMatcher.from_known_components(
                book_series, book_sequence
            )

            search_results = search_service.search_for_audiobook(
                title=title,
                author=author,
                manual_search=False,
                query_components=query_components,
                series=book_series,
                sequence=book_sequence,
            )
            
            if not search_results.get('success'):
                error_msg = search_results.get('error', 'Search failed')
                self.logger.error(f"Search failed for {title}: {error_msg}")
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='SEARCH_FAILED',
                    error_message=error_msg
                )
                return
            
            results = search_results.get('results', [])
            if not results:
                error_msg = f"No search results found for {title} by {author}"
                self.logger.warning(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='SEARCH_FAILED',
                    error_message=error_msg
                )
                return
            
            prefs = db_service.search_preferences.get_search_preferences()
            min_match_score = prefs.get('min_match_score', 80.0)
            min_quality_score = prefs.get('min_quality_score', 70.0)

            best_result, failure_reason = self._select_best_candidate(
                results, min_match_score, min_quality_score, download.get('queued_at')
            )

            if best_result is None:
                error_msg = failure_reason or f"No eligible search results found for {title} by {author}"
                self.logger.warning(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='SEARCH_FAILED',
                    error_message=error_msg
                )
                return

            self.logger.info(
                "Found download source: %s - Quality: %s",
                best_result.get('indexer'),
                best_result.get('confidence_score', 0)
            )
            
            new_type = (
                best_result.get('download_type')
                or best_result.get('result_type')
                or ''
            ).strip().lower()
            if current_type == 'audible' and new_type != 'audible':
                new_type = 'audible'
            elif not new_type:
                new_type = current_type or 'torrent'

            self.queue_manager.update_download(download_id, {
                'download_url': best_result.get('download_url'),
                'indexer': best_result.get('indexer'),
                'quality_score': best_result.get('confidence_score', 0),
                'download_type': new_type,
                'last_error': None,
                'info_hash': best_result.get('info_hash')
            })
            
            if self.state_machine.transition(download_id, 'FOUND'):
                self.event_emitter.emit_search_found(
                    download_id,
                    source=best_result.get('indexer'),
                    title=title
                )
                from services.notifications.events import download_found
                download_found(download_id, download.get('book_title') or title, best_result.get('indexer'))
            
        except Exception as e:
            self.logger.exception("Error during search for download %s", download_id)
            self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='SEARCH_FAILED',
                    error_message=str(e)
                )
