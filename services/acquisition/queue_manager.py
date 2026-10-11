"""
Module Name: queue_manager.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Handles download queue operations, priority, and statistics. Enforces one
    active download per ASIN and orders the queue by priority with helpers for
    status-aware filtering.

Location:
    /services/acquisition/queue_manager.py
"""

from typing import Optional, Dict, Any, List
from datetime import datetime

from sqlalchemy import text

from core.logging import get_module_logger
from services.container import container


class QueueManager:
    """Manages the download queue database operations."""

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.DownloadManagement.QueueManager")
        self._table_initialized = False

    def _get_engine(self):
        return container.get_database_service().connection_manager.get_engine()

    def _ensure_table_exists(self):
        """Ensure download_queue table exists (for frozen schema)."""
        if self._table_initialized:
            return

        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS download_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        book_title TEXT NOT NULL,
                        book_author TEXT,
                        book_asin TEXT,
                        search_result_id INTEGER,
                        download_url TEXT,
                        download_client TEXT,
                        download_client_id TEXT,
                        status TEXT DEFAULT 'queued',
                        quality_score REAL,
                        match_score REAL,
                        file_format TEXT,
                        file_size INTEGER,
                        download_progress REAL DEFAULT 0.0,
                        eta_seconds INTEGER,
                        error_message TEXT,
                        last_error TEXT,
                        retry_count INTEGER DEFAULT 0,
                        max_retries INTEGER DEFAULT 3,
                        seeding_ratio REAL DEFAULT 0.0,
                        seeding_time_seconds INTEGER DEFAULT 0,
                        queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        started_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (search_result_id) REFERENCES search_results(id)
                    )
                """))

                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_download_queue_asin ON download_queue(book_asin)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_download_queue_status ON download_queue(status)"))

            self._table_initialized = True
            self.logger.debug("Download queue table verified/created")

        except Exception as e:
            self.logger.error(f"Error ensuring download_queue table exists: {e}")
            raise

    def add_to_queue(self, book_asin: str, search_result_id: Optional[int] = None,
                     priority: int = 5, **kwargs) -> int:
        """Add item to download queue."""
        self._ensure_table_exists()
        engine = self._get_engine()

        insert_data = {
            'book_asin': book_asin,
            'search_result_id': search_result_id,
            'status': 'QUEUED',
            'priority': priority,
            'queued_at': datetime.now().isoformat(),
            'retry_count': 0,
            'max_retries': kwargs.get('max_retries', 3),
            'download_type': kwargs.get('download_type', 'torrent'),
            'next_retry_at': None
        }

        field_mapping = {
            'title': 'book_title',
            'author': 'book_author'
        }

        optional_fields = [
            'book_title', 'book_author', 'download_url', 'download_client',
            'quality_score', 'match_score', 'file_format', 'file_size',
            'temp_file_path', 'converted_file_path', 'final_file_path',
            'voucher_file_path', 'indexer', 'next_retry_at', 'info_hash', 'target_library_id'
        ]

        if insert_data['download_type'] == 'audible':
            audible_metadata = {}
            if 'audible_format' in kwargs:
                audible_metadata['format'] = kwargs['audible_format']
            if 'audible_quality' in kwargs:
                audible_metadata['quality'] = kwargs['audible_quality']

            if audible_metadata:
                insert_data['file_format'] = f"{audible_metadata.get('format', 'aaxc')}:{audible_metadata.get('quality', 'best')}"

            insert_data['indexer'] = 'Audible'

        for field in optional_fields:
            if field in kwargs:
                insert_data[field] = kwargs[field]

        for api_name, db_name in field_mapping.items():
            if api_name in kwargs and db_name not in insert_data:
                insert_data[db_name] = kwargs[api_name]

        columns = ', '.join(insert_data.keys())
        placeholders = ', '.join(f':{key}' for key in insert_data.keys())
        query = f"INSERT INTO download_queue ({columns}) VALUES ({placeholders})"

        with engine.begin() as conn:
            result = conn.execute(text(query), insert_data)
            download_id = result.lastrowid

        download_type = insert_data['download_type']
        self.logger.debug("Added download to queue", extra={
            "asin": book_asin,
            "download_id": download_id,
            "download_type": download_type,
            "priority": priority
        })

        return download_id

    def get_downloads_for_asin(self, asin: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Queue entries for one book, newest first (the book's download history)."""
        self._ensure_table_exists()
        with self._get_engine().connect() as conn:
            rows = conn.execute(text(
                """
                SELECT status, download_client, file_format, file_size,
                       queued_at, started_at, completed_at, error_message
                FROM download_queue
                WHERE book_asin = :asin
                ORDER BY queued_at DESC
                LIMIT :limit
                """
            ), {"asin": asin, "limit": limit}).mappings().fetchall()
        return [dict(row) for row in rows]

    def get_download(self, download_id: int) -> Optional[Dict[str, Any]]:
        """Get download by ID."""
        self._ensure_table_exists()
        engine = self._get_engine()

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM download_queue WHERE id=:id"),
                {"id": download_id},
            ).mappings().fetchone()

        return dict(row) if row else None

    def get_active_download_by_asin(self, book_asin: str) -> Optional[Dict[str, Any]]:
        """Check if book has active download (not IMPORTED, FAILED, or CANCELLED)."""
        self._ensure_table_exists()
        engine = self._get_engine()

        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT * FROM download_queue
                    WHERE book_asin=:asin
                    AND status NOT IN ('IMPORTED', 'FAILED', 'CANCELLED')
                    LIMIT 1
                """),
                {"asin": book_asin},
            ).mappings().fetchone()

        return dict(row) if row else None

    def get_queue(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all queue items, optionally filtered by status."""
        self._ensure_table_exists()
        engine = self._get_engine()

        with engine.connect() as conn:
            if status_filter:
                normalized_status = str(status_filter).strip().upper()
                _HISTORY_STATUSES = {'IMPORTED', 'SEEDING', 'SEEDING_COMPLETE'}
                if normalized_status in _HISTORY_STATUSES:
                    query = """
                        SELECT dq.*,
                               COALESCE(b.cover_image, al.cover_image_url) AS cover_image
                        FROM download_queue dq
                        LEFT JOIN books b ON dq.book_asin = b.asin
                        LEFT JOIN audible_library al ON dq.book_asin = al.asin
                        WHERE dq.status=:status
                        ORDER BY COALESCE(dq.completed_at, dq.updated_at, dq.queued_at) DESC
                    """
                else:
                    query = """
                        SELECT dq.*,
                               COALESCE(b.cover_image, al.cover_image_url) AS cover_image
                        FROM download_queue dq
                        LEFT JOIN books b ON dq.book_asin = b.asin
                        LEFT JOIN audible_library al ON dq.book_asin = al.asin
                        WHERE dq.status=:status
                        ORDER BY dq.queued_at ASC
                    """
                rows = conn.execute(text(query), {"status": status_filter}).mappings().fetchall()
            else:
                query = """
                    SELECT dq.*,
                           COALESCE(b.cover_image, al.cover_image_url) AS cover_image
                    FROM download_queue dq
                    LEFT JOIN books b ON dq.book_asin = b.asin
                    LEFT JOIN audible_library al ON dq.book_asin = al.asin
                    WHERE dq.status NOT IN ('IMPORTED', 'FAILED', 'CANCELLED')
                    ORDER BY
                        CASE WHEN dq.status IN ('SEEDING', 'SEEDING_COMPLETE') THEN 1 ELSE 0 END,
                        dq.queued_at ASC
                """
                rows = conn.execute(text(query)).mappings().fetchall()

        return [dict(row) for row in rows]

    def _get_column_names(self, engine) -> set:
        """Actual download_queue column names (cached after the first lookup)."""
        cached = getattr(self, '_column_names', None)
        if cached:
            return cached
        with engine.connect() as conn:
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(download_queue)"))}
        self._column_names = columns
        return columns

    def update_download(self, download_id: int, updates: Dict[str, Any]):
        """Update download record."""
        self._ensure_table_exists()
        engine = self._get_engine()

        updates = dict(updates)
        updates['updated_at'] = datetime.now().isoformat()

        valid_columns = self._get_column_names(engine)
        unknown = [key for key in updates if key not in valid_columns]
        if unknown:
            raise ValueError(f"Unknown download_queue column(s): {', '.join(sorted(unknown))}")

        set_clause = ', '.join(f"{key}=:{key}" for key in updates.keys())
        query = f"UPDATE download_queue SET {set_clause} WHERE id=:download_id"

        params = dict(updates)
        params['download_id'] = download_id

        with engine.begin() as conn:
            conn.execute(text(query), params)

        self.logger.debug("Updated download", extra={
            "download_id": download_id,
            "updated_fields": list(updates.keys())
        })

    def delete_download(self, download_id: int):
        """Delete download from queue."""
        self._ensure_table_exists()
        engine = self._get_engine()

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM download_queue WHERE id=:id"), {"id": download_id})

        self.logger.debug("Deleted download from queue", extra={
            "download_id": download_id
        })

    def get_queue_statistics(self) -> Dict[str, int]:
        """Get queue statistics by status."""
        self._ensure_table_exists()
        engine = self._get_engine()

        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT status, COUNT(*) as count
                FROM download_queue
                GROUP BY status
            """)).fetchall()

        stats = {}
        for row in rows:
            status, count = row[0], row[1]
            stats[status] = count
        stats['total_active'] = sum(
            count for status, count in stats.items()
            if status not in ('IMPORTED', 'FAILED', 'CANCELLED')
        )

        return stats
