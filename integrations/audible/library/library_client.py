"""
Module Name: library_client.py
Author: AuralArchivist
Created: August 22, 2025
Last Modified: October 9, 2026
Description:
    Access and manage the user's Audible library using the Python audible package.

Location:
    /integrations/audible/library/library_client.py
"""

import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auth_handler import AudibleAuthHandler
from .library_parser import AudibleLibraryParser
from ..download.audible_download_helper import AudibleDownloadHelper
from ..metadata_sync.audible_api_helper import AudibleApiHelper
from core.logging import get_module_logger
from core.paths import ensure_within_directory, get_path_resolver

_GLOBAL_PROGRESS_STORE = {}


class AudibleLibraryClient:
    """
    Client for the user's Audible library through the Python audible package.
    
    It provides secure access to the user's Audible library, enabling
    viewing of owned content, metadata extraction, and download management while
    respecting Audible's terms of service.
    """
    
    def __init__(self, config_service=None, logger=None, socketio=None):
        self.config_service = config_service
        self.logger = logger or get_module_logger("Service.Audible.Library")
        self.socketio = socketio
        
        self.auth_handler = AudibleAuthHandler(config_service, self.logger)
        self.library_parser = AudibleLibraryParser(self.logger)
        
        self.is_authenticated = False
        self.last_library_update = None
        self.cached_library = None
        self.cached_parsed_library = None
        self.api_helper = None
        self._auth_helper_mtime: Optional[float] = None
        
        self.download_progress_store = _GLOBAL_PROGRESS_STORE
        self.logger.success(
            "Audible library service started successfully",
            extra={"instance_id": id(self), "progress_store_size": len(self.download_progress_store)},
        )
        self.active_downloads = {}
    
    def _get_api_helper(self, force_reload: bool = False) -> Optional[AudibleApiHelper]:
        """Lazily load the shared Audible API helper."""
        auth_path = self.auth_handler.get_default_auth_file()
        auth_mtime: Optional[float] = None
        if auth_path:
            try:
                auth_mtime = os.path.getmtime(auth_path)
            except OSError:
                auth_mtime = None

        needs_reload = force_reload or self.api_helper is None
        if not needs_reload and auth_mtime is not None:
            needs_reload = self._auth_helper_mtime != auth_mtime

        if needs_reload:
            try:
                self.api_helper = AudibleApiHelper(auth_file=auth_path) if auth_path else AudibleApiHelper()
                self._auth_helper_mtime = auth_mtime
            except Exception as exc:
                self.logger.error(
                    "Failed to initialize Audible API helper",
                    extra={"auth_file": auth_path, "exc": exc}, exc_info=True
                )
                self.api_helper = None
                self._auth_helper_mtime = None
        return self.api_helper

    @staticmethod
    def _sanitize_filename(value: str, fallback: str) -> str:
        """Return a filesystem-safe filename component."""
        base = (value or fallback or "audible_download").strip()
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
        return safe or (fallback or "audible_download")

    @staticmethod
    def _resolve_output_directory(output_dir: Optional[str]) -> Path:
        """Return the download directory, creating it if needed.

        Always contained to the configured downloads directory: an override
        is resolved relative to it and rejected (ValueError) if it escapes.
        """
        downloads_root = get_path_resolver().get_downloads_dir()
        target_dir = Path(ensure_within_directory(output_dir or '.', downloads_root, what='Output directory'))
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def test_audible_cli_availability(self) -> Dict[str, Any]:
        """Test if Python audible package is available and properly installed."""
        try:
            import audible

            package_version = audible.__version__
            helper = self._get_api_helper()
            helper_status = helper.check_status() if helper else {
                'available': False,
                'authenticated': False
            }

            authenticated = helper_status.get('authenticated', False)
            message = (
                f'Audible API ready (package v{package_version})'
                if authenticated
                else 'Python audible package installed. Authenticate via Settings > Audible to enable full functionality.'
            )

            response = {
                'available': True,
                'version': package_version,
                'message': message,
                'authenticated': authenticated,
                'package': 'audible'
            }

            if helper_status.get('marketplace'):
                response['marketplace'] = helper_status['marketplace']
            if helper_status.get('account'):
                response['account'] = helper_status['account']
            if helper_status.get('error'):
                response['error'] = helper_status['error']

            self.logger.debug(
                "Audible API status",
                extra={"status": response}
            )
            return response

        except ImportError as exc:
            self.logger.error(
                "Python audible package import failed",
                extra={"exc": exc}
            )
            return {
                'available': False,
                'error': str(exc),
                'message': 'Python audible package is not installed or not accessible'
            }
        except Exception as exc:
            self.logger.error(
                "Error testing Audible API availability",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'available': False,
                'error': str(exc),
                'message': 'Error testing Audible API availability'
            }
    
    def check_authentication_status(self) -> Dict[str, Any]:
        """Check if user is authenticated with Audible by looking for auth files."""
        try:
            auth_files = self.auth_handler.discover_auth_files()
            helper = self._get_api_helper()
            status = helper.check_status() if helper else {'authenticated': False}

            self.is_authenticated = status.get('authenticated', False)

            if self.is_authenticated:
                self.logger.debug(
                    "User is authenticated with Audible",
                    extra={"auth_files": len(auth_files)}
                )
                message = 'Successfully authenticated with Audible'
            elif auth_files:
                message = 'Authentication data found but verification failed. Re-authenticate to refresh tokens.'
            else:
                message = 'Not authenticated with Audible. No auth files found.'

            response = {
                'authenticated': self.is_authenticated,
                'auth_files': auth_files,
                'message': message
            }

            if status.get('error'):
                response['error'] = status['error']
            if status.get('marketplace'):
                response['marketplace'] = status['marketplace']
            if status.get('account'):
                response['account'] = status['account']

            return response

        except Exception as exc:
            self.logger.error(
                "Error checking authentication",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'authenticated': False,
                'error': str(exc),
                'message': f'Error checking authentication: {exc}'
            }
    
    def export_library(self, output_format='json', force_refresh=False) -> Dict[str, Any]:
        """Export the user's Audible library."""
        try:
            if output_format.lower() not in ('json', 'csv', 'tsv'):
                return {
                    'success': False,
                    'error': 'Unsupported format',
                    'message': 'Only JSON export is supported by the Audible API integration'
                }

            cache_valid = (
                not force_refresh
                and self.cached_library is not None
                and self.cached_parsed_library is not None
                and self.last_library_update
                and (datetime.now() - self.last_library_update).seconds < 3600
            )

            if cache_valid:
                self.logger.debug(
                    "Using cached Audible library data",
                    extra={"last_update": self.last_library_update.isoformat() if self.last_library_update else None}
                )
                return {
                    'success': True,
                    'data': self.cached_parsed_library,
                    'raw_data': self.cached_library,
                    'format': 'json',
                    'book_count': len(self.cached_parsed_library.get('books', [])),
                    'last_updated': self.last_library_update.isoformat(),
                    'cached': True
                }

            helper = self._get_api_helper(force_reload=force_refresh)
            if not helper or not helper.is_available():
                self.logger.warning(
                    "Cannot export library: Audible API helper unavailable or not authenticated",
                    extra={"helper_available": helper.is_available() if helper else None}
                )
                return {
                    'success': False,
                    'error': 'not_authenticated',
                    'message': 'Authenticate with Audible to export your library'
                }

            self.logger.info("Fetching library via Audible API")
            library_items = helper.get_library_list()
            parsed_data = self.library_parser.parse_library_data(library_items, 'json')

            self.cached_library = library_items
            self.cached_parsed_library = parsed_data
            self.last_library_update = datetime.now()

            book_count = len(parsed_data.get('books', []))
            self.logger.info(
                "Exported library",
                extra={"book_count": book_count}
            )

            return {
                'success': True,
                'data': parsed_data,
                'raw_data': library_items,
                'format': 'json',
                'book_count': book_count,
                'last_updated': self.last_library_update.isoformat(),
                'cached': False
            }

        except Exception as exc:
            self.logger.error(
                "Error exporting library via API",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'success': False,
                'error': str(exc),
                'message': f'Error exporting library: {exc}'
            }
    
    def get_library_stats(self) -> Dict[str, Any]:
        """Get statistics about the user's Audible library."""
        try:
            library_result = self.export_library()
            
            if not library_result.get('success'):
                return library_result
                
            library_data = library_result.get('data', {})
            books = library_data.get('books', [])
            
            stats = self.library_parser.calculate_library_stats(books)
            
            self.logger.info(
                "Generated library statistics",
                extra={"book_count": len(books)}
            )
            
            return {
                'success': True,
                'stats': stats,
                'book_count': len(books),
                'last_updated': self.last_library_update.isoformat() if self.last_library_update else None
            }
            
        except Exception as exc:
            self.logger.error(
                "Error calculating library stats",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'success': False,
                'error': str(exc),
                'message': f'Error calculating library stats: {exc}'
            }
    
    def search_library(self, query: str, search_fields: List[str] = None) -> Dict[str, Any]:
        """Search within the user's Audible library."""
        try:
            library_result = self.export_library()
            
            if not library_result.get('success'):
                return library_result
                
            library_data = library_result.get('data', {})
            books = library_data.get('books', [])            
            search_results = self.library_parser.search_books(books, query, search_fields)
            
            self.logger.info(
                "Library search completed",
                extra={"query": query, "result_count": len(search_results), "search_fields": search_fields or ['title', 'author', 'narrator']}
            )
            
            return {
                'success': True,
                'query': query,
                'results': search_results,
                'result_count': len(search_results),
                'search_fields': search_fields or ['title', 'author', 'narrator']
            }
            
        except Exception as exc:
            self.logger.error(
                "Error searching library",
                extra={"query": query, "exc": exc}, exc_info=True
            )
            return {
                'success': False,
                'error': str(exc),
                'message': f'Error searching library: {exc}'
            }
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get status of the Audible Library Service."""
        try:
            api_status = self.test_audible_cli_availability()
            auth_status = self.check_authentication_status()

            authenticated = auth_status.get('authenticated', False)

            capabilities = {
                'library_export': api_status.get('available', False) and authenticated,
                'library_search': (self.cached_parsed_library is not None),
                'download_support': authenticated,
                'download_single_book': authenticated,
                'download_all_books': authenticated,
                'activation_bytes': authenticated,
                'format_conversion': True,
                'metadata_extraction': True
            }

            cached_info = {
                'available': self.cached_library is not None,
                'last_updated': self.last_library_update.isoformat() if self.last_library_update else None,
                'book_count': self._get_cached_book_count()
            }

            status = {
                'service_name': 'AudibleLibraryClient',
                'status': 'operational' if api_status.get('available') else 'offline',
                'audible_api': api_status,
                'audible_cli': api_status,
                'authentication': auth_status,
                'capabilities': capabilities,
                'cached_library': cached_info
            }

            self.logger.info(
                "Generated service status report",
                extra={"authenticated": authenticated, "cached_books": cached_info.get('book_count')}
            )
            return status
            
        except Exception as exc:
            self.logger.error(
                "Error getting service status",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'service_name': 'AudibleLibraryClient',
                'status': 'error',
                'error': str(exc),
                'message': f'Error getting service status: {exc}'
            }
    
    def _get_cached_book_count(self) -> int:
        if isinstance(self.cached_parsed_library, dict) and self.cached_parsed_library:
            return len(self.cached_parsed_library.get('books') or [])
        if isinstance(self.cached_library, dict):
            return len(self.cached_library.get('books') or [])
        if isinstance(self.cached_library, list):
            return len(self.cached_library)
        return 0
    
    def _emit_progress(self, download_id: str, step: str, message: str, progress: int = 0, title: str = None):
        """Store and emit download progress information."""
        progress_data = {
            'download_id': download_id,
            'step': step,
            'message': message,
            'progress': progress,
            'title': title or 'Unknown Book',
            'timestamp': datetime.now().isoformat(),
            'status': 'active' if step not in ['complete', 'error'] else step
        }

        self.download_progress_store[download_id] = progress_data
        self.logger.debug(
            "Stored download progress",
            extra=progress_data
        )

    
    def refresh_library_cache(self) -> Dict[str, Any]:
        """Force refresh of the library cache."""
        self.logger.info("Forcing library cache refresh", extra={"force_refresh": True})
        
        self.cached_library = None
        self.last_library_update = None
        
        return self.export_library(force_refresh=True)
    
    def download_book(self, asin: str = None, title: str = None, 
                     output_dir: str = None, format: str = "aaxc",
                     quality: str = "best", include_pdf: bool = False,
                     include_cover: bool = True, include_chapters: bool = True,
                     download_id: str = None) -> Dict[str, Any]:
        """Download a single audiobook from the user's library."""
        try:
            if not asin and not title:
                return {
                    'success': False,
                    'error': 'Missing identifier',
                    'message': 'Either ASIN or title must be provided'
                }

            auth_status = self.check_authentication_status()
            if not auth_status.get('authenticated'):
                return {
                    'success': False,
                    'error': 'Not authenticated',
                    'message': 'Authenticate with Audible before downloading books'
                }

            helper = self._get_api_helper()
            if not helper or not helper.is_available():
                return {
                    'success': False,
                    'error': 'api_unavailable',
                    'message': 'Audible API helper unavailable. Re-authenticate and try again.'
                }

            book_asin = asin
            if not book_asin and title:
                library_result = self.export_library()
                if library_result.get('success'):
                    for book in library_result['data'].get('books', []):
                        if book.get('title', '').lower() == title.lower():
                            book_asin = book.get('asin')
                            break
            if not book_asin:
                return {
                    'success': False,
                    'error': 'asin_not_found',
                    'message': 'Could not determine ASIN for requested book'
                }

            if not download_id:
                download_id = f"download_{book_asin}_{int(time.time())}"

            book_title = title or f"Book {book_asin}"
            target_dir = self._resolve_output_directory(output_dir)
            sanitized_name = self._sanitize_filename(book_title, book_asin)
            format_preference = format if format in ("aax", "aaxc", "aax-fallback") else "aaxc"

            self._emit_progress(download_id, 'preparing', f"Preparing to download {book_title}...", 0, book_title)

            warnings = []
            if include_pdf or include_cover or include_chapters:
                warnings.append('Supplementary downloads (PDF/Cover/Chapters) are not yet supported with the API workflow.')

            self.active_downloads[download_id] = {
                'asin': book_asin,
                'title': book_title,
                'status': 'starting',
                'start_time': datetime.now()
            }

            self._emit_progress(download_id, 'starting', f"Starting download for {book_title}...", 10, book_title)

            def progress_callback(downloaded: int, total: int, message: str):
                percent = int(downloaded / total * 100) if total else 0
                self._emit_progress(download_id, 'downloading', f"{book_title}: {message}", percent, book_title)

            def run_download():
                try:
                    download_helper = AudibleDownloadHelper(progress_callback=progress_callback)
                    result = download_helper.download_book(
                        asin=book_asin,
                        output_dir=target_dir,
                        filename=sanitized_name,
                        format_preference=format_preference,
                        quality=quality,
                        aax_fallback=(format_preference == 'aax-fallback')
                    )

                    if result.get('success'):
                        self.logger.info(
                            "Download completed",
                            extra={"asin": book_asin, "title": book_title, "download_id": download_id}
                        )
                        self._emit_progress(download_id, 'complete', f"{book_title} downloaded successfully", 100, book_title)
                    else:
                        error_message = result.get('error', 'Unknown error')
                        self.logger.error(
                            "Download failed",
                            extra={"asin": book_asin, "title": book_title, "download_id": download_id, "error": error_message}
                        )
                        self._emit_progress(download_id, 'error', f"Download failed: {error_message}", 0, book_title)

                except Exception as exc:
                    self.logger.error(
                        "Error during download",
                        extra={"asin": book_asin, "title": book_title, "download_id": download_id, "exc": exc}, exc_info=True
                    )
                    self._emit_progress(download_id, 'error', f"Error downloading {book_title}: {exc}", 0, book_title)
                finally:
                    self.active_downloads.pop(download_id, None)

            worker = threading.Thread(target=run_download, daemon=True)
            worker.start()

            response = {
                'success': True,
                'message': 'Download started successfully',
                'download_id': download_id,
                'asin': book_asin,
                'title': book_title,
                'format': format_preference,
                'quality': quality,
                'status': 'started'
            }
            if warnings:
                response['warnings'] = warnings

            return response

        except Exception as exc:
            self.logger.error(
                "Error starting download",
                extra={"asin": asin, "title": title, "download_id": download_id, "exc": exc}, exc_info=True
            )
            if download_id:
                self._emit_progress(download_id, 'error', f"Error starting download: {exc}", 0)
                self.active_downloads.pop(download_id, None)
            return {
                'success': False,
                'error': str(exc),
                'message': f'Error starting download: {exc}'
            }
    
    def download_all_books(self, download_service, output_dir: str = None, format: str = "aaxc",
                          quality: str = "best", start_date: str = None,
                          end_date: str = None, jobs: int = 3,
                          include_pdf: bool = False, include_cover: bool = True,
                          include_chapters: bool = True) -> Dict[str, Any]:
        """Queue every book in the user's Audible library on download_service."""
        try:
            auth_status = self.check_authentication_status()
            if not auth_status.get('authenticated'):
                return {
                    'success': False,
                    'error': 'Not authenticated',
                    'message': 'Authenticate with Audible before downloading books'
                }

            helper = self._get_api_helper()
            if not helper or not helper.is_available():
                return {
                    'success': False,
                    'error': 'api_unavailable',
                    'message': 'Audible API helper unavailable. Re-authenticate and try again.'
                }

            library_result = self.export_library(force_refresh=True)
            if not library_result.get('success'):
                return library_result

            books = library_result['data'].get('books', [])
            if not books:
                return {
                    'success': True,
                    'message': 'No books found in library to download',
                    'download_count': 0
                }

            format_preference = format if format in ('aax', 'aaxc', 'aax-fallback') else 'aaxc'
            quality_preference = quality
            ownership_status = 'audible_library'

            def parse_date(value: str) -> Optional[datetime]:
                if not value:
                    return None
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except ValueError:
                    try:
                        return datetime.strptime(value, '%Y-%m-%d')
                    except ValueError:
                        return None

            start_dt = parse_date(start_date) if start_date else None
            end_dt = parse_date(end_date) if end_date else None

            filtered_books = []
            for book in books:
                purchase = parse_date(book.get('purchase_date') or book.get('release_date'))
                if start_dt and purchase and purchase < start_dt:
                    continue
                if end_dt and purchase and purchase > end_dt:
                    continue
                filtered_books.append(book)

            if not filtered_books:
                return {
                    'success': True,
                    'message': 'No books matched the provided date filters',
                    'download_count': 0
                }

            target_dir = self._resolve_output_directory(output_dir)

            if not download_service:
                return {
                    'success': False,
                    'error': 'download_service_unavailable',
                    'message': 'Download management service is unavailable'
                }

            warnings = []
            if include_pdf or include_cover or include_chapters:
                warnings.append('Supplementary downloads (PDF/Cover/Chapters) are not yet supported with the API workflow.')

            config_jobs = None
            if self.config_service:
                try:
                    audible_defaults = self.config_service.get_section('audible') or {}
                    config_jobs = (
                        audible_defaults.get('concurrent_downloads')
                        or audible_defaults.get('max_concurrent_downloads')
                        or audible_defaults.get('download_concurrency')
                    )
                except Exception as cfg_exc:
                    self.logger.debug(
                        "Unable to read Audible concurrency defaults",
                        extra={"exc": cfg_exc}
                    )

            effective_jobs = jobs if jobs is not None else config_jobs
            try:
                jobs_value = int(effective_jobs) if effective_jobs is not None else 1
            except (TypeError, ValueError):
                jobs_value = 1
            jobs = max(1, min(jobs_value, 8))

            try:
                download_service.set_audible_concurrency(jobs)
            except Exception as exc:
                self.logger.debug(
                    "Unable to set Audible concurrency",
                    extra={"jobs": jobs, "exc": exc}
                )

            queue_priority = 5
            if self.config_service:
                try:
                    dm_config = self.config_service.get_section('download_management') or {}
                    queue_priority = int(dm_config.get('queue_priority_default', queue_priority))
                except Exception as cfg_exc:
                    self.logger.debug(
                        "Unable to read queue priority default",
                        extra={"exc": cfg_exc}
                    )

            queue_priority = max(1, min(queue_priority, 10))

            self.logger.info(
                "Preparing bulk download queue",
                extra={
                    "requested_count": len(books),
                    "filtered_count": len(filtered_books),
                    "format": format_preference,
                    "quality": quality_preference,
                    "start_date": start_date,
                    "end_date": end_date,
                    "jobs": jobs,
                    "queue_priority": queue_priority,
                    "output_dir": str(target_dir),
                },
            )

            bulk_id = f"bulk_{int(time.time())}"
            self.active_downloads[bulk_id] = {
                'asin': None,
                'title': 'Bulk Download',
                'status': 'queued',
                'start_time': datetime.now()
            }

            total_books = len(filtered_books)
            queued_ids: List[int] = []
            skipped_entries: List[Dict[str, Any]] = []
            seen_asins = set()

            self._emit_progress(
                bulk_id,
                'preparing',
                f"Queuing {total_books} book(s) for download",
                0,
                'Bulk Download'
            )

            for index, book in enumerate(filtered_books, start=1):
                asin = book.get('asin')
                title = book.get('title') or asin or f"Book {index}"
                author = book.get('author') or 'Unknown Author'

                authors_field = book.get('authors')
                if isinstance(authors_field, list):
                    author_names = []
                    for entry in authors_field:
                        if isinstance(entry, dict):
                            name_value = entry.get('name') or entry.get('full_name')
                            if name_value:
                                author_names.append(name_value)
                        elif entry:
                            author_names.append(str(entry))
                    if author_names:
                        author = ', '.join(author_names)
                elif isinstance(authors_field, str) and authors_field.strip():
                    author = authors_field.strip()

                if not asin:
                    skipped_entries.append({'title': title, 'error': 'Missing ASIN'})
                    continue

                if asin in seen_asins:
                    skipped_entries.append({'asin': asin, 'title': title, 'error': 'Duplicate ASIN in request'})
                    continue

                seen_asins.add(asin)

                queue_message = f"Queuing {title} ({index}/{total_books})"
                percent = int((index / total_books) * 100) if total_books else 100

                try:
                    queue_result = download_service.add_to_queue(
                        book_asin=asin,
                        priority=queue_priority,
                        download_type='audible',
                        title=title,
                        author=author,
                        audible_format=format_preference,
                        audible_quality=quality_preference,
                        search_result_id=None,
                        ownership_details={
                            'ownership_status': ownership_status,
                            'source': 'audible_library',
                            'timestamp': datetime.utcnow().isoformat()
                        }
                    )

                    if queue_result.get('success'):
                        download_id = queue_result.get('download_id')
                        queued_ids.append(download_id)
                        self._emit_progress(bulk_id, 'queuing', f"{queue_message} - queued", percent, title)
                    else:
                        error_message = queue_result.get('message', 'Failed to queue download')
                        skipped_entries.append({'asin': asin, 'title': title, 'error': error_message})
                        self._emit_progress(bulk_id, 'error', f"{title}: {error_message}", percent, title)
                except Exception as exc:
                    skipped_entries.append({'asin': asin, 'title': title, 'error': str(exc)})
                    self._emit_progress(bulk_id, 'error', f"{title}: {exc}", percent, title)

            queued_count = len(queued_ids)
            skipped_count = len(skipped_entries)
            final_message = f"Queued {queued_count} of {total_books} book(s)"

            self.logger.info(
                "Bulk download queued",
                extra={
                    "bulk_id": bulk_id,
                    "queued": queued_count,
                    "skipped": skipped_count,
                    "total": total_books,
                    "jobs": jobs,
                },
            )

            progress_step = 'complete' if queued_count else 'error'
            self._emit_progress(bulk_id, progress_step, final_message, 100, 'Bulk Download')
            self.active_downloads.pop(bulk_id, None)

            response = {
                'success': queued_count > 0,
                'message': final_message if queued_count else 'No books were queued for download',
                'download_id': bulk_id,
                'book_count': total_books,
                'queued_count': queued_count,
                'skipped_count': skipped_count,
                'queued_download_ids': queued_ids,
                'format': format_preference,
                'quality': quality,
                'jobs': jobs
            }

            if warnings:
                response['warnings'] = warnings
            if skipped_entries:
                response['skipped'] = skipped_entries

            return response

        except Exception as exc:
            self.logger.error(
                "Error during bulk download",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'success': False,
                'error': str(exc),
                'message': f'Error during bulk download: {exc}'
            }
    
    def get_activation_bytes(self, reload: bool = False) -> Dict[str, Any]:
        """Retrieve activation bytes using the Python audible API."""
        try:
            auth_status = self.check_authentication_status()
            if not auth_status.get('authenticated'):
                return {
                    'success': False,
                    'error': 'Not authenticated',
                    'message': 'Must be authenticated with Audible to get activation bytes'
                }

            helper = self._get_api_helper(force_reload=reload)
            if not helper or not helper.is_available():
                return {
                    'success': False,
                    'error': 'Audible API helper unavailable',
                    'message': 'Audible Python API is not available or not authenticated'
                }

            self.logger.info(
                "Fetching activation bytes via Audible API",
                extra={"reload": reload}
            )
            result = helper.get_activation_bytes(reload=reload)

            if result.get('success'):
                activation_bytes = result.get('activation_bytes')
                self.logger.info(
                    "Successfully retrieved activation bytes via API",
                    extra={"reload": reload}
                )
                return {
                    'success': True,
                    'activation_bytes': activation_bytes,
                    'message': 'Activation bytes retrieved successfully'
                }

            error_message = result.get('error', 'Unknown error')
            self.logger.error(
                "Failed to retrieve activation bytes via API",
                extra={"reload": reload, "error": error_message}
            )
            return {
                'success': False,
                'error': error_message,
                'message': 'Failed to retrieve activation bytes'
            }

        except Exception as exc:
            self.logger.error(
                "Error getting activation bytes",
                extra={"reload": reload, "exc": exc}, exc_info=True
            )
            return {
                'success': False,
                'error': str(exc),
                'message': f'Error getting activation bytes: {exc}'
            }
