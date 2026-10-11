"""
Module Name: audible_wishlist.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Audible wishlist sync: adds wishlist books to the library as wanted, on
    request and as a background task.

Location:
    /services/sync/audible_wishlist.py
"""

import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from core.logging import get_module_logger
from core import events
from core.scheduler import get_scheduler

TASK_NAME = 'wishlist_sync'
STARTUP_DELAY_SECONDS = 30
NOT_CONNECTED_RETRY_SECONDS = 60


class AudibleWishlistService:
    """Service for managing Audible wishlist integration and auto-sync."""
    
    def __init__(self, config_service, audible_manager, sync_interval_minutes: int = 15, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.Wishlist")
        self.config_service = config_service
        self.audible_manager = audible_manager
        self.sync_interval = sync_interval_minutes * 60
        self.last_sync_time = None
        self.last_sync_result = None
        self._lock = threading.Lock()
        self._sync_in_progress = False
        
        self.logger.success(
            "Audible wishlist service started successfully",
            extra={"interval_minutes": sync_interval_minutes},
        )
    
    def is_auto_sync_running(self) -> bool:
        """Whether scheduled wishlist syncs are active."""
        return get_scheduler().is_active(TASK_NAME)
    
    def _get_audible_wishlist(self):
        """Get wishlist items from Audible API."""
        try:
            self.logger.debug("Fetching Audible wishlist")
            
            wishlist_endpoints = [
                "1.0/wishlist",
                "1.0/customer/wishlist",
                "1.0/library/wishlist"
            ]
            
            response = self.audible_manager.try_multiple_endpoints(
                wishlist_endpoints,
                num_results=50,
                response_groups=(
                    "contributors,media,price,product_attrs,product_desc,"
                    "product_extended_attrs,rating,series,relationships"
                )
            )
            
            if response:
                wishlist_items = response.get("items", []) or response.get("products", [])
                if wishlist_items:
                    self.logger.debug("Found wishlist items", extra={"count": len(wishlist_items)})
                    return [self._format_wishlist_item(item) for item in wishlist_items]
            
                self.logger.debug("Wishlist endpoints returned no items")
            return []
            
        except Exception as e:
            self.logger.error("Error fetching Audible wishlist", extra={"error": str(e)}, exc_info=True)
            return []
    
    def _format_wishlist_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Format wishlist item using the shared manager."""
        book_data = item.get("product", item)
        
        formatted = self.audible_manager.format_book_data(book_data)
        normalized_series = self._normalize_series_value(formatted.get('series'))
        formatted['series'] = normalized_series
        formatted['Series'] = normalized_series
        formatted['source'] = 'audible_wishlist'
        formatted['status'] = 'Wanted'
        formatted['wishlist_date'] = item.get("date_added", "")
        
        return formatted
    
    def sync_wishlist_to_library(self) -> Dict[str, Any]:
        """Sync Audible wishlist to local library with 'Wanted' status."""
        try:
            wishlist_items = self._get_audible_wishlist()
            if not wishlist_items:
                return {
                    'success': True,
                    'added': 0,
                    'updated': 0,
                    'skipped': 0,
                    'message': 'No wishlist items found'
                }
            
            from services.container import container
            conn = container.get_database_service().connection_manager.get_engine().raw_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT asin, title, author FROM books WHERE asin IS NOT NULL OR (title IS NOT NULL AND author IS NOT NULL)")
            existing_books = cursor.fetchall()
            
            existing_asins = {asin.lower() for asin, _, _ in existing_books if asin}
            existing_titles = {(title.lower().strip(), author.lower().strip()) for _, title, author in existing_books if title and author}
            
            added = 0
            updated = 0
            skipped = 0
            series_sync_asins = set()
            added_books = []
            
            for item in wishlist_items:
                asin = item.get('asin', '').strip()
                title = item.get('title', '').strip()
                author = item.get('author', '').strip()
                series_value = self._normalize_series_value(item.get('series'))
                
                if not asin and not (title and author):
                    skipped += 1
                    self.logger.debug(
                        "Skipped wishlist item with no ASIN or title/author",
                        extra={"item": item},
                    )
                    continue
                
                is_duplicate = False
                
                if asin and asin.lower() in existing_asins:
                    is_duplicate = True
                    self.logger.debug("Skipped duplicate ASIN", extra={"title": title, "asin": asin})
                
                elif title and author and (title.lower(), author.lower()) in existing_titles:
                    is_duplicate = True
                    self.logger.debug(
                        "Skipped duplicate title/author",
                        extra={"title": title, "author": author},
                    )
                
                if is_duplicate:
                    skipped += 1
                    continue
                
                try:
                    existing_book = None
                    if asin:
                        cursor.execute("SELECT id, library_status, series, COALESCE(series_asin, '') FROM books WHERE asin = ?", (asin,))
                        existing_book = cursor.fetchone()

                    if existing_book:
                        book_id, current_status, current_series, current_series_asin = existing_book
                        current_status = (current_status or 'wanted').lower()
                        needs_series_sync = False
                        series_filled = False
                        if series_value:
                            existing_normalized = self._normalize_series_value(current_series)
                            if existing_normalized == '':
                                cursor.execute(
                                    "UPDATE books SET series = ? WHERE id = ?",
                                    (series_value, book_id)
                                )
                                needs_series_sync = True
                                series_filled = True
                            elif not current_series_asin:
                                needs_series_sync = True

                        if series_filled:
                            updated += 1
                        else:
                            skipped += 1
                        self.logger.debug(
                            "Skipped updating book library_status",
                            extra={"title": title, "library_status": current_status},
                        )

                        if needs_series_sync and asin:
                            series_sync_asins.add(asin)
                    else:
                        cursor.execute("""
                            INSERT INTO books (
                                title, author, series, narrator, runtime, release_date,
                                language, publisher, overall_rating, asin, summary, cover_image,
                                source, library_status, metadata_source
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            title or 'Unknown Title',
                            author or 'Unknown Author',
                                series_value,
                            item.get('narrator', 'Unknown Narrator'),
                            item.get('runtime', ''),
                            item.get('release_date', ''),
                            item.get('language', 'english'),
                            item.get('publisher', ''),
                            item.get('rating', ''),
                            asin or None,
                            item.get('summary', ''),
                            item.get('cover_image', ''),
                            'audible',
                            'wanted',
                            'audible',
                        ))
                        added += 1
                        added_books.append({'book_id': cursor.lastrowid, 'asin': asin or None,
                                            'cover_url': item.get('cover_image') or None, 'contributors': []})
                        self.logger.info(
                            "Added new book from wishlist",
                            extra={"title": title, "author": author, "asin": asin},
                        )
                
                except Exception as e:
                    self.logger.error(
                        "Error processing wishlist item",
                        extra={"asin": asin or None, "title": title or None, "error": str(e)},
                        exc_info=True,
                    )
                    skipped += 1
                    continue
            
            conn.commit()
            conn.close()

            for book in added_books:
                events.publish(events.BOOK_ADDED, **book)
            for series_asin in series_sync_asins:
                events.publish(events.SERIES_LOOKUP_NEEDED, asin=series_asin)
            
            self.logger.info(
                "Wishlist sync complete",
                extra={"added": added, "updated": updated, "skipped": skipped, "processed": len(wishlist_items)},
            )
            
            return {
                'success': True,
                'added': added,
                'updated': updated,
                'skipped': skipped,
                'total_processed': len(wishlist_items),
                'message': f'Sync complete: {added} new books added, {updated} updated to Wanted status, {skipped} skipped'
            }
            
        except Exception as e:
            self.logger.error("Wishlist sync failed", extra={"error": str(e)}, exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def _normalize_series_value(self, series_value: Optional[str]) -> str:
        """Normalize series strings and drop placeholder values."""
        if not series_value:
            return ''

        normalized = str(series_value).strip()
        if not normalized:
            return ''

        placeholder_values = {
            'n/a',
            'standalone',
            'none',
            'unknown',
            'unknown series'
        }

        return '' if normalized.lower() in placeholder_values else normalized

    def get_status(self) -> Dict[str, Any]:
        """Get current service status."""
        with self._lock:
            manager_status = self.audible_manager.get_service_status()
            
            return {
                **manager_status,
                'auto_sync_running': self.is_auto_sync_running(),
                'sync_interval_minutes': self.sync_interval // 60,
                'last_sync_time': self.last_sync_time.isoformat() if self.last_sync_time else None,
                'last_sync_result': self.last_sync_result,
                'next_sync_in_seconds': self._get_next_sync_seconds(),
                'service_configured': manager_status.get('configured', False)
            }
    
    def run_scheduled_sync(self) -> None:
        """One scheduled sync; skipped when a sync is already in progress."""
        with self._lock:
            if self._sync_in_progress:
                self.logger.debug("Sync already in progress, skipping scheduled sync")
                return
            self._sync_in_progress = True
        try:
            self.logger.debug("Performing scheduled wishlist sync")
            result = self.sync_wishlist_to_library()
            with self._lock:
                self.last_sync_time = datetime.now()
                self.last_sync_result = result
            if result.get('success'):
                added, updated, skipped = result.get('added', 0), result.get('updated', 0), result.get('skipped', 0)
                if added > 0 or updated > 0:
                    self.logger.info("Scheduled wishlist sync",
                                     extra={"added": added, "updated": updated, "skipped": skipped})
                else:
                    self.logger.debug("Scheduled wishlist sync: no changes", extra={"skipped": skipped})
            else:
                self.logger.warning("Scheduled wishlist sync failed",
                                    extra={"error": result.get('error', 'Unknown error')})
        except Exception as e:
            self.logger.error("Error in scheduled wishlist sync", extra={"error": str(e)}, exc_info=True)
            with self._lock:
                self.last_sync_result = {'success': False, 'error': str(e), 'sync_time': datetime.now().isoformat()}
        finally:
            with self._lock:
                self._sync_in_progress = False
        
    
    def _get_next_sync_seconds(self) -> int:
        """Get seconds until next scheduled sync."""
        if not self.last_sync_time:
            return 0
        
        next_sync = self.last_sync_time + timedelta(seconds=self.sync_interval)
        now = datetime.now()
        
        if next_sync <= now:
            return 0
        
        return int((next_sync - now).total_seconds())
