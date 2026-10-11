"""
Module Name: connection.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Manages SQLite connections and applies performance pragmas.

Location:
    /core/database/connection.py
"""

import threading

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from core.logging import get_module_logger


class DatabaseConnection:
    """Handles database connection management and optimization."""

    def __init__(self, db_file: str, *, logger=None):
        self.db_file = db_file
        self.logger = logger or get_module_logger("Service.Database.Connection")
        self._engine: Engine | None = None
        self._engine_lock = threading.Lock()

    def get_engine(self) -> Engine:
        """Return (and lazily create) the shared SQLAlchemy engine for this database.

        All operations-layer code should use this engine via context managers:
            with self.connection_manager.get_engine().begin() as conn:   # writes
            with self.connection_manager.get_engine().connect() as conn: # reads
        Pragmas are applied via a 'connect' event listener, which only fires
        when the pool actually opens a new physical connection (pooled
        connections are reused rather than reapplying pragmas on every call).
        """
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is not None:
                return self._engine
            engine = create_engine(
                f"sqlite:///{self.db_file}",
                connect_args={"timeout": 30},
            )

            pragmas = [
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA cache_size=10000",
                "PRAGMA temp_store=memory",
                "PRAGMA busy_timeout=30000",
            ]

            @event.listens_for(engine, "connect")
            def _apply_pragmas(dbapi_conn, _connection_record):
                cursor = dbapi_conn.cursor()
                for pragma in pragmas:
                    cursor.execute(pragma)
                cursor.close()

            # Published only after the pragma listener is attached
            self._engine = engine
        return self._engine
    
    def test_connection(self) -> bool:
        """Test database connection and return success status"""
        try:
            with self.get_engine().connect() as conn:
                result = conn.execute(text("SELECT 1")).fetchone()

            success = result is not None
            if success:
                self.logger.info("Database connection test successful")
            else:
                self.logger.error("Database connection test failed - no result")

            return success

        except Exception:
            self.logger.exception(
                "Database connection test failed",
                extra={"database_file": self.db_file},
            )
            return False
    
    def get_database_info(self) -> dict:
        """Get database file information"""
        try:
            import os
            if os.path.exists(self.db_file):
                size_bytes = os.path.getsize(self.db_file)
                size_mb = round(size_bytes / (1024 * 1024), 2)
                
                return {
                    'file_path': self.db_file,
                    'size_bytes': size_bytes,
                    'size_mb': size_mb,
                    'exists': True
                }
            else:
                return {
                    'file_path': self.db_file,
                    'exists': False
                }
        
        except Exception as exc:
            self.logger.exception(
                "Error getting database info",
                extra={"database_file": self.db_file},
            )
            return {'error': str(exc)}
