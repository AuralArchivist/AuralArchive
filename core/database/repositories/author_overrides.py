"""
Module Name: author_overrides.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Manages canonical author name overrides scoped optionally by ASIN.

Location:
    /core/database/repositories/author_overrides.py
"""

from typing import List, Optional
from sqlalchemy import text
from core.logging import get_module_logger
from core.convert import normalize_asin


class AuthorOverrideOperations:
    """Manages canonical author name overrides scoped by optional ASIN."""

    GLOBAL_SCOPE = ""

    def __init__(self, connection_manager, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.AuthorOverrides")

    def _normalize_name(self, name: Optional[str]) -> str:
        if not name:
            return ""
        cleaned = str(name).replace("\u00a0", " ")
        return " ".join(cleaned.strip().split())

    def get_preferred_author_name(self, source_author_name: str, asin: Optional[str] = None) -> Optional[str]:
        """Return the preferred author name if an override exists."""
        normalized_source = self._normalize_name(source_author_name)
        scope_asin = normalize_asin(asin) or self.GLOBAL_SCOPE

        if not normalized_source:
            return None

        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()

            if scope_asin != self.GLOBAL_SCOPE:
                row = conn.execute(
                    text("""
                    SELECT preferred_author_name
                    FROM author_name_overrides
                    WHERE source_author_name = :source COLLATE NOCASE
                      AND asin = :asin
                    LIMIT 1
                    """),
                    {"source": normalized_source, "asin": scope_asin}
                ).fetchone()
                if row:
                    preferred = row[0]
                    return self._normalize_name(preferred) or preferred

            row = conn.execute(
                text("""
                SELECT preferred_author_name
                FROM author_name_overrides
                WHERE source_author_name = :source COLLATE NOCASE
                  AND asin = :asin
                LIMIT 1
                """),
                {"source": normalized_source, "asin": self.GLOBAL_SCOPE}
            ).fetchone()
            if row:
                preferred = row[0]
                return self._normalize_name(preferred) or preferred

            row = conn.execute(
                text("""
                SELECT preferred_author_name
                FROM author_name_overrides
                WHERE source_author_name = :source COLLATE NOCASE
                LIMIT 1
                """),
                {"source": normalized_source}
            ).fetchone()
            if row:
                preferred = row[0]
                return self._normalize_name(preferred) or preferred

            return None
        except Exception as exc:
            self.logger.error(f"Error fetching author override for '{source_author_name}': {exc}", exc_info=True)
            return None
        finally:
            if conn is not None:
                conn.close()

    def get_aliases_for_preferred(self, preferred_author_name: str) -> List[str]:
        """Return alternate spellings that map to the preferred author name."""
        normalized_preferred = self._normalize_name(preferred_author_name)
        if not normalized_preferred:
            return []

        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            result = conn.execute(
                text("""
                SELECT DISTINCT source_author_name
                FROM author_name_overrides
                WHERE preferred_author_name = :preferred COLLATE NOCASE
                """),
                {"preferred": normalized_preferred}
            )
            values = [self._normalize_name(row[0]) for row in result.fetchall() if row[0]]
            return values
        except Exception as exc:
            self.logger.error(f"Error fetching aliases for '{preferred_author_name}': {exc}", exc_info=True)
            return []
        finally:
            if conn is not None:
                conn.close()
