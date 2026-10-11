"""
Module Name: search_preferences.py
Author: AuralArchivist
Created: June 25, 2026
Last Modified: October 9, 2026
Description:
    CRUD for search_preferences, used by release quality scoring.

Location:
    /core/database/repositories/search_preferences.py
"""

from typing import Any, Dict

from sqlalchemy import text

from core.logging import get_module_logger

DEFAULT_PREFERENCES: Dict[str, Any] = {
    'min_bitrate': 64,
    'preferred_bitrate': 128,
    'preferred_format': 'M4B',
    'format_priority': 'M4B,M4A,MP3,FLAC',
    'min_quality_score': 70.0,
    'min_match_score': 80.0,
}

_VALUE_COERCERS = {
    'integer': int,
    'float': float,
    'boolean': lambda v: str(v).strip().lower() in ('1', 'true', 'yes', 'on'),
    'string': str,
}


class SearchPreferencesOperations:
    """Handles CRUD for the search_preferences table."""

    def __init__(self, connection_manager, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.SearchPreferences")

    def get_search_preferences(self) -> Dict[str, Any]:
        """Return all preferences as a flat dict, coerced by their value_type.

        Falls back to DEFAULT_PREFERENCES for any key missing from the
        table - defensive only, the table is always seeded by migrations.
        """
        prefs = dict(DEFAULT_PREFERENCES)
        engine = self.connection_manager.get_engine()
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT preference_key, preference_value, value_type FROM search_preferences")
                ).fetchall()
            for key, raw_value, value_type in rows:
                coerce = _VALUE_COERCERS.get(value_type, str)
                try:
                    prefs[key] = coerce(raw_value) if raw_value is not None else prefs.get(key)
                except (TypeError, ValueError):
                    self.logger.warning(
                        "Could not coerce search preference, using default",
                        extra={"key": key, "raw_value": raw_value, "value_type": value_type},
                    )
        except Exception as exc:
            self.logger.error("Failed to load search preferences, using defaults", extra={"error": str(exc)}, exc_info=True)
        return prefs

    def set_search_preferences(self, updates: Dict[str, Any]) -> bool:
        """Upsert preference values. Only known keys (DEFAULT_PREFERENCES) are accepted."""
        unknown = [key for key in updates if key not in DEFAULT_PREFERENCES]
        if unknown:
            self.logger.warning("Ignoring unknown search preference keys", extra={"keys": unknown})

        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                for key, value in updates.items():
                    if key not in DEFAULT_PREFERENCES:
                        continue
                    conn.execute(
                        text(
                            """
                            UPDATE search_preferences
                            SET preference_value = :value, updated_at = CURRENT_TIMESTAMP
                            WHERE preference_key = :key
                            """
                        ),
                        {"key": key, "value": str(value)},
                    )
            return True
        except Exception as exc:
            self.logger.error("Failed to update search preferences", extra={"error": str(exc)}, exc_info=True)
            return False
