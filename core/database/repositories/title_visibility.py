"""
Module Name: title_visibility.py
Author: AuralArchivist
Created: July 6, 2026
Last Modified: October 9, 2026
Description:
    Hidden titles that aren't owned yet. Owned titles use books.monitored.

Location:
    /core/database/repositories/title_visibility.py
"""

from typing import List, Optional, Dict
from sqlalchemy import text
from ..error_handling import error_handler
from core.logging import get_module_logger
from core.convert import normalize_asin


class TitleVisibilityOperations:
    """Manages the title_visibility table backing the hide/unmonitor feature for unowned titles."""

    def __init__(self, connection_manager, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.TitleVisibility")

    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def hide_title(
        self,
        asin: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        series_asin: Optional[str] = None,
        series_title: Optional[str] = None,
        source_context: Optional[str] = None,
    ) -> bool:
        """Mark a not-yet-owned title as hidden/unmonitored."""
        normalized_asin = normalize_asin(asin) or ""
        if not normalized_asin:
            self.logger.warning("Cannot hide title with empty ASIN")
            return False

        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                    INSERT INTO title_visibility (
                        asin, title, author, series_asin, series_title, source_context, hidden_at
                    ) VALUES (:asin, :title, :author, :series_asin, :series_title, :source_context, CURRENT_TIMESTAMP)
                    ON CONFLICT(asin) DO UPDATE SET
                        title=COALESCE(excluded.title, title_visibility.title),
                        author=COALESCE(excluded.author, title_visibility.author),
                        series_asin=COALESCE(excluded.series_asin, title_visibility.series_asin),
                        series_title=COALESCE(excluded.series_title, title_visibility.series_title),
                        source_context=COALESCE(excluded.source_context, title_visibility.source_context)
                    """),
                    {
                        "asin": normalized_asin,
                        "title": title,
                        "author": author,
                        "series_asin": series_asin,
                        "series_title": series_title,
                        "source_context": source_context,
                    },
                )
            self.logger.info("Hid title %s (%s)", normalized_asin, title or "unknown title")
            return True
        except Exception as exc:
            self.logger.error(f"Error hiding title {normalized_asin}: {exc}", exc_info=True)
            return False

    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def unhide_title(self, asin: str) -> bool:
        """Remove a title from title_visibility, re-monitoring it."""
        normalized_asin = normalize_asin(asin) or ""
        if not normalized_asin:
            return False

        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                result = conn.execute(
                    text("DELETE FROM title_visibility WHERE asin = :asin"),
                    {"asin": normalized_asin},
                )
            return result.rowcount > 0
        except Exception as exc:
            self.logger.error(f"Error unhiding title {normalized_asin}: {exc}", exc_info=True)
            return False

    def list_hidden_titles(self) -> List[Dict]:
        """Return all hidden not-yet-owned titles, most recently hidden first."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            result = conn.execute(
                text("""
                SELECT asin, title, author, series_asin, series_title, source_context, hidden_at
                FROM title_visibility
                ORDER BY hidden_at DESC
                """)
            )
            return [
                {
                    "asin": row[0],
                    "title": row[1],
                    "author": row[2],
                    "series_asin": row[3],
                    "series_title": row[4],
                    "source_context": row[5],
                    "hidden_at": row[6],
                }
                for row in result.fetchall()
            ]
        except Exception as exc:
            self.logger.error(f"Error listing hidden titles: {exc}", exc_info=True)
            return []
        finally:
            if conn is not None:
                conn.close()
