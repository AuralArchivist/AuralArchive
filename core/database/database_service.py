"""
Module Name: database_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Singleton coordinator for database connection, migrations, and data access
    modules.

Location:
    /core/database/database_service.py
"""

import os
from typing import Optional

from .repositories.author_overrides import AuthorOverrideOperations
from .repositories.audible_library import AudibleLibraryOperations
from .repositories.authors import AuthorOperations
from .repositories.books import BookOperations
from .repositories.categories import CategoryOperations
from .connection import DatabaseConnection
from .migrations import DatabaseMigrations
from .repositories.search_preferences import SearchPreferencesOperations
from .repositories.series import SeriesOperations
from .repositories.stats import DatabaseStats
from .repositories.title_visibility import TitleVisibilityOperations
from core.logging import get_module_logger
from core.paths import DATABASE_FILENAME as DEFAULT_DB_FILENAME, get_path_resolver

DEFAULT_DB_PATH = get_path_resolver().get_database_path()

class DatabaseService:
    """Enhanced singleton service for database operations with modular components"""

    def __init__(
        self,
        db_file: str = DEFAULT_DB_PATH,
        *,
        logger=None,
        connection_manager: Optional[DatabaseConnection] = None,
        migrations: Optional[DatabaseMigrations] = None,
        author_overrides: Optional[AuthorOverrideOperations] = None,
        books: Optional[BookOperations] = None,
        authors: Optional[AuthorOperations] = None,
        audible_library: Optional[AudibleLibraryOperations] = None,
        stats: Optional[DatabaseStats] = None,
        series: Optional[SeriesOperations] = None,
        search_preferences: Optional[SearchPreferencesOperations] = None,
        title_visibility: Optional[TitleVisibilityOperations] = None,
        categories: Optional[CategoryOperations] = None,
        **_kwargs,
    ):
        self.logger = logger or get_module_logger("Service.Database.Service")
        self.db_file = self._normalize_db_file(db_file)
        os.makedirs(os.path.dirname(self.db_file), exist_ok=True)

        self.connection_manager = connection_manager or DatabaseConnection(self.db_file, logger=self.logger)
        self.migrations = migrations or DatabaseMigrations(self.connection_manager, logger=self.logger)
        self.author_overrides = author_overrides or AuthorOverrideOperations(self.connection_manager, logger=self.logger)
        self.books = books or BookOperations(self.connection_manager, self.author_overrides, logger=self.logger)
        self.authors = authors or AuthorOperations(self.connection_manager, self.author_overrides, logger=self.logger)
        self.audible_library = audible_library or AudibleLibraryOperations(self.connection_manager, logger=self.logger)
        self.stats = stats or DatabaseStats(self.connection_manager, logger=self.logger)
        self.series = series or SeriesOperations(self.connection_manager, self.author_overrides, logger=self.logger)
        self.search_preferences = search_preferences or SearchPreferencesOperations(self.connection_manager, logger=self.logger)
        self.title_visibility = title_visibility or TitleVisibilityOperations(self.connection_manager, logger=self.logger)
        self.categories = categories or CategoryOperations(self.connection_manager, logger=self.logger)

        self._initialize_service()

    def _normalize_db_file(self, db_file: Optional[str]) -> str:
        """Force all database operations to use the canonical filename."""
        target = db_file or DEFAULT_DB_PATH
        normalized = os.path.normpath(target)
        filename = os.path.basename(normalized)

        if filename != DEFAULT_DB_FILENAME:
            preferred_dir = os.path.dirname(DEFAULT_DB_PATH) or ""
            corrected_path = os.path.normpath(os.path.join(preferred_dir, DEFAULT_DB_FILENAME))
            self.logger.warning(
                "Ignoring custom database file; using default instead",
                extra={"requested": normalized, "using": corrected_path},
            )
            return corrected_path

        return normalized
    
    def _initialize_service(self):
        """Initialize database and perform necessary migrations."""
        try:
            self.migrations.initialize_database()
            self.migrations.migrate_database()
            self.logger.success(
                "Database ready",
                extra={"database_file": self.db_file, "migrations": "applied"},
            )
        except Exception:
            self.logger.exception(
                "Failed to initialize DatabaseService",
                extra={"database_file": self.db_file},
            )
            raise
