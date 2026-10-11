"""
Module Name: migrations.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Initializes and migrates the SQLite schema for AuralArchive. Migrations
    are additive-only and idempotent: each one checks PRAGMA table_info
    before issuing ALTER TABLE ADD COLUMN, so re-running on an already-
    migrated database is always a safe no-op.

Location:
    /core/database/migrations.py
"""

from typing import TYPE_CHECKING

from core.logging import get_module_logger

if TYPE_CHECKING:
    from .connection import DatabaseConnection


class DatabaseMigrations:
    """Handles database initialization and schema migrations (frozen)."""

    def __init__(self, connection_manager: "DatabaseConnection", *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.Migrations")

    def initialize_database(self):
        """Initialize base schema for a fresh database."""
        try:
            conn = self.connection_manager.get_engine().raw_connection()
            cursor = conn.cursor()

            self._create_books_table(cursor)
            self._create_authors_table(cursor)
            self._create_author_name_overrides_table(cursor)
            self._create_audible_library_table(cursor)

            self._create_indexer_status_table(cursor)
            self._create_search_preferences_table(cursor)
            self._create_download_queue_table(cursor)
            self._create_hardcover_tables(cursor)
            self._create_title_visibility_table(cursor)
            self._create_categories_tables(cursor)
            self._create_library_review_table(cursor)
            self._create_notifications_table(cursor)

            conn.commit()
            conn.close()

            self.logger.info("Database initialization completed")

        except Exception as exc:
            self.logger.error(
                "Error initializing database",
                extra={"error": str(exc)},
                exc_info=True,
            )
            raise

    def _create_indexer_status_table(self, cursor):
        """Create the indexer_status table for indexer health and performance tracking"""
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS indexer_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indexer_name TEXT NOT NULL UNIQUE,
                indexer_type TEXT,
                base_url TEXT,
                api_key TEXT,
                enabled BOOLEAN DEFAULT 1,
                last_check TIMESTAMP,
                last_success TIMESTAMP,
                last_failure TIMESTAMP,
                total_searches INTEGER DEFAULT 0,
                successful_searches INTEGER DEFAULT 0,
                failed_searches INTEGER DEFAULT 0,
                average_response_time REAL,
                health_status TEXT DEFAULT 'unknown',
                error_message TEXT,
                priority INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        
        cursor.execute(create_table_sql)
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_indexer_status_name ON indexer_status(indexer_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_indexer_status_enabled ON indexer_status(enabled)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_indexer_status_health ON indexer_status(health_status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_indexer_status_priority ON indexer_status(priority)')
        
        self.logger.debug("Indexer status table created or verified")
    
    def _create_search_preferences_table(self, cursor):
        """Create the search_preferences table for user quality and format preferences"""
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS search_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preference_key TEXT NOT NULL UNIQUE,
                preference_value TEXT,
                value_type TEXT DEFAULT 'string',
                category TEXT DEFAULT 'general',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        
        cursor.execute(create_table_sql)
        
        default_preferences = [
            ('min_bitrate', '64', 'integer', 'quality', 'Minimum acceptable bitrate in kbps'),
            ('preferred_bitrate', '128', 'integer', 'quality', 'Preferred bitrate in kbps'),
            ('preferred_format', 'M4B', 'string', 'quality', 'Preferred audiobook format'),
            ('format_priority', 'M4B,M4A,MP3,FLAC', 'string', 'quality', 'Format priority order'),
            ('min_quality_score', '7.0', 'float', 'quality', 'Minimum quality score for auto-download'),
            ('min_match_score', '80.0', 'float', 'search', 'Minimum match score percentage'),
            ('auto_download_enabled', 'false', 'boolean', 'automation', 'Enable automatic downloads'),
            ('max_search_results', '50', 'integer', 'search', 'Maximum search results to process'),
            ('search_timeout', '30', 'integer', 'search', 'Search timeout in seconds'),
        ]
        
        for key, value, value_type, category, description in default_preferences:
            cursor.execute("""
                INSERT OR IGNORE INTO search_preferences 
                (preference_key, preference_value, value_type, category, description)
                VALUES (?, ?, ?, ?, ?)
            """, (key, value, value_type, category, description))
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_preferences_key ON search_preferences(preference_key)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_preferences_category ON search_preferences(category)')
        
        self.logger.debug("Search preferences table created or verified with default values")
    
    def _create_download_queue_table(self, cursor):
        """Create the download_queue table for automated download management"""
        create_table_sql = """
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
        """
        
        cursor.execute(create_table_sql)
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_download_queue_status ON download_queue(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_download_queue_book_title ON download_queue(book_title)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_download_queue_asin ON download_queue(book_asin)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_download_queue_queued_at ON download_queue(queued_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_download_queue_client_id ON download_queue(download_client_id)')
        
        self.logger.debug("Download queue table created or verified")

    def _create_books_table(self, cursor):
        """Create the books table for library storage."""
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT,
                series TEXT,
                sequence TEXT,
                narrator TEXT,
                runtime INTEGER,
                release_date TEXT,
                language TEXT,
                publisher TEXT,
                overall_rating TEXT,
                rating REAL,
                num_ratings INTEGER DEFAULT 0,
                asin TEXT,
                summary TEXT,
                cover_image TEXT,
                source TEXT,
                library_status TEXT DEFAULT 'wanted',
                metadata_source TEXT DEFAULT 'audible',
                monitored BOOLEAN DEFAULT 1,
                file_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                series_asin TEXT,
                file_size INTEGER,
                file_format TEXT,
                file_quality TEXT,
                imported_to_library BOOLEAN DEFAULT 0,
                import_date INTEGER,
                naming_template TEXT
            )
        """
        cursor.execute(create_table_sql)
        self.logger.debug("Books table created or verified")

    def _create_authors_table(self, cursor):
        """Create the authors table for author metadata."""
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS authors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                audible_author_id TEXT,
                author_image_url TEXT,
                author_bio TEXT,
                author_page_url TEXT,
                total_books_count INTEGER DEFAULT 0,
                audible_books_count INTEGER DEFAULT 0,
                last_fetched_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        cursor.execute(create_table_sql)
        self.logger.debug("Authors table created or verified")

    def _create_author_name_overrides_table(self, cursor):
        """Create the author_name_overrides table for canonical names."""
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS author_name_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_author_name TEXT NOT NULL,
                preferred_author_name TEXT NOT NULL,
                asin TEXT DEFAULT '',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_author_name, asin)
            )
        """
        cursor.execute(create_table_sql)
        self.logger.debug("Author name overrides table created or verified")

    def _create_audible_library_table(self, cursor):
        """Create the audible_library table for Audible sync."""
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS audible_library (
                asin TEXT PRIMARY KEY,
                title TEXT,
                author TEXT,
                authors TEXT,
                narrator TEXT,
                narrators TEXT,
                series_title TEXT,
                series_sequence TEXT,
                publisher TEXT,
                publication_date TEXT,
                release_date TEXT,
                duration_minutes INTEGER,
                runtime_length_min INTEGER,
                summary TEXT,
                description TEXT,
                genres TEXT,
                tags TEXT,
                language TEXT,
                format TEXT,
                rating REAL,
                num_ratings INTEGER,
                purchase_date TEXT,
                listened BOOLEAN DEFAULT 0,
                progress REAL DEFAULT 0,
                favorite BOOLEAN DEFAULT 0,
                cover_image_url TEXT,
                local_cover_path TEXT,
                file_path TEXT,
                file_size INTEGER,
                added_date TEXT,
                last_updated TEXT,
                metadata_source TEXT,
                sync_status TEXT
            )
        """
        cursor.execute(create_table_sql)
        self.logger.debug("Audible library table created or verified")

    def _create_hardcover_tables(self, cursor):
        """Create tables backing the Hardcover.app integration (see ROADMAP.md)."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hardcover_link (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL UNIQUE,
                hardcover_book_id INTEGER NOT NULL,
                hardcover_edition_id INTEGER,
                asin TEXT,
                match_confidence TEXT DEFAULT 'unverified',
                matched_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hardcover_shelf_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL UNIQUE,
                hardcover_user_book_id INTEGER,
                hardcover_status_id INTEGER,
                hardcover_owned BOOLEAN DEFAULT 0,
                last_pulled_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hardcover_push_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                payload TEXT,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 5,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hardcover_trending_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window TEXT NOT NULL,
                rank INTEGER NOT NULL,
                hardcover_book_id INTEGER NOT NULL,
                title TEXT,
                author TEXT,
                cover_url TEXT,
                stat_label TEXT,
                rating REAL,
                ratings_count INTEGER,
                asin TEXT,
                narrator TEXT,
                audio_seconds INTEGER,
                refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hardcover_new_releases_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hardcover_book_id INTEGER NOT NULL,
                title TEXT,
                author TEXT,
                cover_url TEXT,
                release_date TEXT,
                asin TEXT,
                narrator TEXT,
                audio_seconds INTEGER,
                refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_hardcover_link_book_id ON hardcover_link(book_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_hardcover_shelf_state_book_id ON hardcover_shelf_state(book_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_hardcover_push_queue_status ON hardcover_push_queue(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_hardcover_push_queue_book_id ON hardcover_push_queue(book_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_hardcover_trending_window ON hardcover_trending_cache(window)')
        self.logger.debug("Hardcover integration tables created or verified")

    def _create_title_visibility_table(self, cursor):
        """Create the title_visibility table backing the hide/unmonitor feature.

        Presence of an ASIN in this table means the title is hidden from
        series/author browsing and excluded from tracking - used for titles
        that aren't owned yet (no `books` row to attach a monitored flag to).
        Re-monitoring a title simply deletes its row.
        """
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS title_visibility (
                asin TEXT PRIMARY KEY,
                title TEXT,
                author TEXT,
                series_asin TEXT,
                series_title TEXT,
                source_context TEXT,
                hidden_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_title_visibility_series_asin ON title_visibility(series_asin)')
        self.logger.debug("title_visibility table created or verified")

    def _create_library_review_table(self, cursor):
        """Create library_review: folders found in the library by the periodic
        library check that could not be imported automatically (no confident
        metadata match). Shown on the Import page until the user accepts the
        suggestion, adopts the folder via Library Import, or ignores it.

        status: 'pending' (shown, counted in the header alert) or 'ignored'
        (kept so the check doesn't re-flag it). state: 'needs_review' (a
        low-confidence suggestion exists), 'unmatched' (no suggestion) or
        'duplicate' (a second copy of a book already in the library).
        """
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS library_review (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_path TEXT NOT NULL UNIQUE,
                rel_path TEXT,
                name TEXT,
                primary_file TEXT,
                file_count INTEGER DEFAULT 0,
                size_bytes INTEGER DEFAULT 0,
                state TEXT,
                status TEXT DEFAULT 'pending',
                suggested_title TEXT,
                suggested_author TEXT,
                suggested_asin TEXT,
                confidence REAL,
                strategy TEXT,
                metadata_json TEXT,
                message TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_library_review_status ON library_review(status)')
        self.logger.debug("library_review table created or verified")

    def _create_notifications_table(self, cursor):
        """Create notifications: the header bell's alerts (book missing, book
        released, new title in a series, library review, ...).

        dedupe_key makes each event notify once; resolving an event (e.g. a
        missing book turning up again) deletes its row so a later recurrence
        notifies afresh. read_at drives the unread count; dismissed_at hides
        it from the list while keeping the key, so it isn't raised again.
        """
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                level TEXT DEFAULT 'info',
                title TEXT NOT NULL,
                message TEXT,
                link TEXT,
                dedupe_key TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP,
                dismissed_at TIMESTAMP
            )
        """)
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_notifications_open ON notifications(dismissed_at, read_at)')
        self.logger.debug("notifications table created or verified")

    def _create_categories_tables(self, cursor):
        """Create the audible_categories tree cache and user_categories favorites tables."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audible_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audible_category_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                parent_id INTEGER REFERENCES audible_categories(id),
                level INTEGER NOT NULL,
                path TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audible_category_id TEXT NOT NULL UNIQUE REFERENCES audible_categories(audible_category_id),
                enabled BOOLEAN NOT NULL DEFAULT 1,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audible_categories_parent ON audible_categories(parent_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audible_categories_audible_id ON audible_categories(audible_category_id)')
        self.logger.debug("Audible categories tables created or verified")

    def _seed_default_author_overrides(self, cursor):
        """Insert curated overrides to keep metadata consistent."""
        defaults = [
            ("A. C. Cobble", "AC Cobble", "B06XB1KBV4", "Normalize punctuation for Endless Flight")
        ]

        for source, preferred, asin, notes in defaults:
            asin_value = (asin or '').strip().upper()
            cursor.execute(
                """
                INSERT OR IGNORE INTO author_name_overrides
                    (source_author_name, preferred_author_name, asin, notes)
                VALUES (?, ?, ?, ?)
                """,
                (source, preferred, asin_value, notes)
            )
    
    def migrate_database(self):
        """Check and perform database migrations."""
        try:
            conn = self.connection_manager.get_engine().raw_connection()
            cursor = conn.cursor()
            
            cursor.execute("PRAGMA table_info(books)")
            columns = [column[1] for column in cursor.fetchall()]
            
            migrations_applied = 0
            
            # Migration 1: Add cover_image column
            if 'cover_image' not in columns:
                self.logger.info("Adding cover_image column to books table...")
                cursor.execute("ALTER TABLE books ADD COLUMN cover_image TEXT")
                migrations_applied += 1
                self.logger.info("Cover image column added successfully")
            
            # Migration 2: Add created_at/updated_at columns. SQLite's ADD COLUMN
            # can't take a non-constant default (CURRENT_TIMESTAMP), so add the
            # column bare and backfill existing rows.
            timestamp_columns_added = False
            for timestamp_column in ('created_at', 'updated_at'):
                if timestamp_column not in columns:
                    self.logger.info(f"Adding {timestamp_column} column to books table...")
                    cursor.execute(f"ALTER TABLE books ADD COLUMN {timestamp_column} TIMESTAMP")
                    cursor.execute(f"UPDATE books SET {timestamp_column} = CURRENT_TIMESTAMP WHERE {timestamp_column} IS NULL")
                    timestamp_columns_added = True
            if timestamp_columns_added:
                migrations_applied += 1
                self.logger.info("Timestamp columns added successfully")
            
            # Migration 3: authors table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='authors'")
            authors_table_exists = cursor.fetchone() is not None
            
            if not authors_table_exists:
                self.logger.info("Creating authors table...")
                self._create_authors_table(cursor)
                migrations_applied += 1
                self.logger.info("Authors table created successfully")

            # Migration 3b: author_name_overrides table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='author_name_overrides'")
            overrides_table_exists = cursor.fetchone() is not None

            if not overrides_table_exists:
                self.logger.info("Creating author_name_overrides table...")
                self._create_author_name_overrides_table(cursor)
                migrations_applied += 1
                self.logger.info("author_name_overrides table created successfully")

            self._seed_default_author_overrides(cursor)
            
            # Migration 4: Add num_ratings column
            if 'num_ratings' not in columns:
                self.logger.info("Adding num_ratings column to books table...")
                cursor.execute("ALTER TABLE books ADD COLUMN num_ratings INTEGER DEFAULT 0")
                migrations_applied += 1
                self.logger.info("Num_ratings column added successfully")
            
            # Migration 4b: audible_library table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audible_library'")
            audible_library_table_exists = cursor.fetchone() is not None
            
            if not audible_library_table_exists:
                self.logger.info("Creating audible_library table...")
                self._create_audible_library_table(cursor)
                migrations_applied += 1
                self.logger.info("Audible library table created successfully")
            else:
                # Migration 4c: rebuild audible_library with an ASIN primary key
                cursor.execute("PRAGMA table_info(audible_library)")
                audible_columns = {row[1]: row for row in cursor.fetchall()}
                
                asin_column = audible_columns.get('asin')
                if asin_column and not asin_column[5]:
                    self.logger.info("Migrating audible_library table to use ASIN as primary key...")
                    
                    cursor.execute("SELECT * FROM audible_library")
                    existing_data = cursor.fetchall()
                    
                    cursor.execute("DROP TABLE audible_library")
                    
                    self._create_audible_library_table(cursor)
                    
                    if existing_data:
                        for row in existing_data:
                            asin = None
                            for i, value in enumerate(row):
                                if isinstance(value, str) and len(value) == 10 and value.isalnum():
                                    asin = value
                                    break
                            
                            if asin:
                                try:
                                    cursor.execute("""
                                        INSERT OR IGNORE INTO audible_library 
                                        (asin, title, author, last_updated) 
                                        VALUES (?, ?, ?, datetime('now'))
                                    """, (asin, row[1] if len(row) > 1 else None, row[2] if len(row) > 2 else None))
                                except Exception as e:
                                    self.logger.warning(f"Could not migrate row with ASIN {asin}: {e}")
                    
                    migrations_applied += 1
                    self.logger.info("Audible library table migrated to ASIN primary key successfully")
            
            # Migration 5: Add series_asin column to books table
            cursor.execute("PRAGMA table_info(books)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'series_asin' not in columns:
                cursor.execute("ALTER TABLE books ADD COLUMN series_asin TEXT")
                migrations_applied += 1
                self.logger.info("Added series_asin column to books table")
            
            # Migration 6: Create series_metadata table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS series_metadata (
                    series_asin TEXT PRIMARY KEY,
                    series_title TEXT NOT NULL,
                    series_url TEXT,
                    sku TEXT,
                    sku_lite TEXT,
                    total_books INTEGER DEFAULT 0,
                    description TEXT,
                    cover_url TEXT,
                    last_synced TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("SELECT COUNT(*) FROM series_metadata")
            if cursor.fetchone()[0] == 0:
                migrations_applied += 1
                self.logger.info("Created series_metadata table")
            
            # Migration 7: Create series_books table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS series_books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_asin TEXT NOT NULL,
                    book_asin TEXT NOT NULL,
                    book_title TEXT,
                    sequence TEXT,
                    sort_order TEXT,
                    relationship_type TEXT DEFAULT 'child',
                    in_library BOOLEAN DEFAULT 0,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(series_asin, book_asin),
                    FOREIGN KEY (series_asin) REFERENCES series_metadata(series_asin)
                )
            """)
            cursor.execute("SELECT COUNT(*) FROM series_books")
            if cursor.fetchone()[0] == 0:
                migrations_applied += 1
                self.logger.info("Created series_books table")
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_series_books_series_asin 
                ON series_books(series_asin)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_series_books_book_asin 
                ON series_books(book_asin)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_series_books_in_library 
                ON series_books(in_library)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_books_series_asin 
                ON books(series_asin)
            """)
            
            # Migration 8: series_books metadata columns
            cursor.execute("PRAGMA table_info(series_books)")
            existing_columns = [column[1] for column in cursor.fetchall()]
            
            columns_to_add = {
                'author': 'TEXT',
                'narrator': 'TEXT',
                'publisher': 'TEXT',
                'release_date': 'TEXT',
                'runtime': 'INTEGER DEFAULT 0',
                'rating': 'TEXT',
                'num_ratings': 'INTEGER DEFAULT 0',
                'summary': 'TEXT',
                'cover_image': 'TEXT',
                'language': 'TEXT DEFAULT "English"'
            }
            
            columns_added = 0
            for column_name, column_type in columns_to_add.items():
                if column_name not in existing_columns:
                    try:
                        cursor.execute(f"""
                            ALTER TABLE series_books 
                            ADD COLUMN {column_name} {column_type}
                        """)
                        columns_added += 1
                        self.logger.info(f"Added column '{column_name}' to series_books table")
                    except Exception as e:
                        self.logger.warning(f"Could not add column '{column_name}' to series_books: {e}")
            
            if migrations_applied > 0:
                migrations_applied += 1
                self.logger.info(f"Added {columns_added} metadata columns to series_books table")
            
            # Migration 9: Add import tracking columns to books table
            cursor.execute("PRAGMA table_info(books)")
            existing_book_columns = [column[1] for column in cursor.fetchall()]
            
            import_columns_to_add = {
                'file_path': 'TEXT',
                'file_size': 'INTEGER',
                'file_format': 'TEXT',
                'file_quality': 'TEXT',
                'imported_to_library': 'BOOLEAN DEFAULT 0',
                'import_date': 'INTEGER',
                'naming_template': 'TEXT'
            }
            
            import_columns_added = 0
            for column_name, column_type in import_columns_to_add.items():
                if column_name not in existing_book_columns:
                    try:
                        cursor.execute(f"""
                            ALTER TABLE books 
                            ADD COLUMN {column_name} {column_type}
                        """)
                        import_columns_added += 1
                        self.logger.info(f"Added import tracking column '{column_name}' to books table")
                    except Exception as e:
                        self.logger.warning(f"Could not add column '{column_name}' to books: {e}")
            
            if import_columns_added > 0:
                migrations_applied += 1
                self.logger.info(f"Added {import_columns_added} import tracking columns to books table")
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_books_imported_to_library 
                ON books(imported_to_library)
            """)
            
            # Migration 10: Dedupe trigger for series_books, preferring ASINs over ISBNs
            cursor.execute("""
                DROP TRIGGER IF EXISTS prevent_series_book_duplicates
            """)

            cursor.execute("""
                CREATE TRIGGER prevent_series_book_duplicates
                BEFORE INSERT ON series_books
                FOR EACH ROW
                WHEN (
                    -- Check if a similar book already exists for this series
                    EXISTS (
                        SELECT 1 FROM series_books 
                        WHERE series_asin = NEW.series_asin
                        AND book_asin != NEW.book_asin
                        AND (
                            -- Same title (case-insensitive, normalized)
                            LOWER(TRIM(SUBSTR(COALESCE(book_title, ''), 1, INSTR(COALESCE(book_title, '') || ':', ':') - 1))) = 
                            LOWER(TRIM(SUBSTR(COALESCE(NEW.book_title, ''), 1, INSTR(COALESCE(NEW.book_title, '') || ':', ':') - 1)))
                            OR COALESCE(book_title, '') = COALESCE(NEW.book_title, '')
                        )
                        AND (sequence = NEW.sequence OR (sequence IS NULL AND NEW.sequence IS NULL))
                        -- Existing entry is an Audible ASIN (B0...), new entry is ISBN (numeric)
                        AND book_asin LIKE 'B0%'
                        AND LENGTH(book_asin) = 10
                        AND NEW.book_asin NOT LIKE 'B%'
                    )
                )
                BEGIN
                    -- Abort the insert - better entry already exists
                    SELECT RAISE(IGNORE);
                END
            """)
            
            cursor.execute("""
                SELECT COUNT(*) FROM sqlite_master 
                WHERE type='trigger' AND name='prevent_series_book_duplicates'
            """)
            if cursor.fetchone()[0] > 0:
                migrations_applied += 1
                self.logger.info("Created series_books deduplication trigger")
            
            # Migration 11: Add download management columns to download_queue table
            cursor.execute("PRAGMA table_info(download_queue)")
            download_queue_columns = [column[1] for column in cursor.fetchall()]
            
            download_columns_to_add = {
                'seeding_enabled': 'BOOLEAN DEFAULT 0',
                'delete_source': 'BOOLEAN DEFAULT 0',
                'temp_file_path': 'TEXT',
                'converted_file_path': 'TEXT',
                'original_file_path': 'TEXT',
                'priority': 'INTEGER DEFAULT 5',
                'next_retry_at': 'TEXT',
                'seeding_ratio': 'REAL DEFAULT 0.0',
                'seeding_time_seconds': 'INTEGER DEFAULT 0'
            }
            
            download_columns_added = 0
            for column_name, column_type in download_columns_to_add.items():
                if column_name not in download_queue_columns:
                    try:
                        cursor.execute(f"""
                            ALTER TABLE download_queue 
                            ADD COLUMN {column_name} {column_type}
                        """)
                        download_columns_added += 1
                        self.logger.info(f"Added download management column '{column_name}' to download_queue table")
                    except Exception as e:
                        self.logger.warning(f"Could not add column '{column_name}' to download_queue: {e}")
            
            if download_columns_added > 0:
                migrations_applied += 1
                self.logger.info(f"Added {download_columns_added} download management columns to download_queue table")
            
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_download_queue_unique_active_asin 
                ON download_queue(book_asin) 
                WHERE status NOT IN ('IMPORTED', 'FAILED', 'CANCELLED')
            """)
            
            cursor.execute("""
                SELECT COUNT(*) FROM sqlite_master 
                WHERE type='index' AND name='idx_download_queue_unique_active_asin'
            """)
            if cursor.fetchone()[0] > 0:
                migrations_applied += 1
                self.logger.info("Created unique ASIN constraint for active downloads")
            
            # Migration 12: Add unified download pipeline columns to download_queue table
            cursor.execute("PRAGMA table_info(download_queue)")
            pipeline_columns = [column[1] for column in cursor.fetchall()]
            
            unified_pipeline_columns = {
                'download_type': 'TEXT DEFAULT "torrent"',
                'temp_file_path': 'TEXT',
                'converted_file_path': 'TEXT',
                'final_file_path': 'TEXT',
                'voucher_file_path': 'TEXT',
                'indexer': 'TEXT',
                'priority': 'INTEGER DEFAULT 5',
                'last_error': 'TEXT',
                'next_retry_at': 'TEXT',
                'info_hash': 'TEXT'
            }
            
            pipeline_columns_added = 0
            for column_name, column_type in unified_pipeline_columns.items():
                if column_name not in pipeline_columns:
                    try:
                        cursor.execute(f"""
                            ALTER TABLE download_queue 
                            ADD COLUMN {column_name} {column_type}
                        """)
                        pipeline_columns_added += 1
                        self.logger.info(f"Added unified pipeline column '{column_name}' to download_queue table")
                    except Exception as e:
                        self.logger.warning(f"Could not add column '{column_name}' to download_queue: {e}")
            
            if pipeline_columns_added > 0:
                migrations_applied += 1
                self.logger.info(f"Added {pipeline_columns_added} unified download pipeline columns to download_queue table")
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_queue_download_type
                ON download_queue(download_type)
            """)

            # Migration 13: Add library_status, metadata_source, monitored (old columns kept)
            cursor.execute("PRAGMA table_info(books)")
            columns = [column[1] for column in cursor.fetchall()]

            title_state_columns_added = 0
            if 'library_status' not in columns:
                cursor.execute("ALTER TABLE books ADD COLUMN library_status TEXT")
                title_state_columns_added += 1
                self.logger.info("Added library_status column to books table")
            if 'metadata_source' not in columns:
                cursor.execute("ALTER TABLE books ADD COLUMN metadata_source TEXT")
                title_state_columns_added += 1
                self.logger.info("Added metadata_source column to books table")
            if 'monitored' not in columns:
                cursor.execute("ALTER TABLE books ADD COLUMN monitored BOOLEAN DEFAULT 1")
                title_state_columns_added += 1
                self.logger.info("Added monitored column to books table")

            if title_state_columns_added > 0:
                migrations_applied += 1
                self.logger.info(f"Added {title_state_columns_added} title-state columns to books table")

                cursor.execute("""
                    UPDATE books
                    SET library_status = CASE
                        WHEN ownership_status IN ('owned', 'audible_library', 'imported') THEN 'owned'
                        ELSE 'wanted'
                    END
                    WHERE library_status IS NULL
                """)
                cursor.execute("""
                    UPDATE books
                    SET metadata_source = CASE
                        WHEN source IN ('manual_import', 'manual', 'manual_override') THEN 'manual'
                        ELSE 'audible'
                    END
                    WHERE metadata_source IS NULL
                """)
                cursor.execute("UPDATE books SET monitored = 1 WHERE monitored IS NULL")
                self.logger.info("Backfilled library_status/metadata_source/monitored from legacy columns")

            # Migration 14: Rescale min_quality_score default from 7.0 to 70.0
            cursor.execute(
                "UPDATE search_preferences SET preference_value = '70.0', updated_at = CURRENT_TIMESTAMP "
                "WHERE preference_key = 'min_quality_score' AND preference_value = '7.0'"
            )
            if cursor.rowcount > 0:
                migrations_applied += 1
                self.logger.info("Rescaled min_quality_score preference default from 7.0 to 70.0")

            # Migration 15: Create Hardcover.app integration tables (idempotent -
            # safe to run against a database that already has them).
            cursor.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='hardcover_link'"
            )
            hardcover_tables_existed = cursor.fetchone()[0] > 0
            self._create_hardcover_tables(cursor)
            if not hardcover_tables_existed:
                migrations_applied += 1
                self.logger.info("Created Hardcover.app integration tables")

            # Migration 16: Create title_visibility table (hide/unmonitor feature)
            cursor.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='title_visibility'"
            )
            title_visibility_existed = cursor.fetchone()[0] > 0
            self._create_title_visibility_table(cursor)
            if not title_visibility_existed:
                migrations_applied += 1
                self.logger.info("Created title_visibility table")

            # Migration 17: Create audible_categories/user_categories tables
            cursor.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='audible_categories'"
            )
            categories_tables_existed = cursor.fetchone()[0] > 0
            self._create_categories_tables(cursor)
            if not categories_tables_existed:
                migrations_applied += 1
                self.logger.info("Created audible_categories/user_categories tables")

            # Migration 18: Drop unused series_books.in_audiobookshelf (needs SQLite 3.35+)
            cursor.execute("PRAGMA table_info(series_books)")
            if 'in_audiobookshelf' in [column[1] for column in cursor.fetchall()]:
                try:
                    cursor.execute("ALTER TABLE series_books DROP COLUMN in_audiobookshelf")
                    migrations_applied += 1
                    self.logger.info("Dropped unused series_books.in_audiobookshelf column")
                except Exception as drop_error:
                    self.logger.debug(f"Could not drop series_books.in_audiobookshelf: {drop_error}")

            # Migration 19: library_review table (periodic library check)
            cursor.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='library_review'"
            )
            library_review_existed = cursor.fetchone()[0] > 0
            self._create_library_review_table(cursor)
            if not library_review_existed:
                migrations_applied += 1
                self.logger.info("Created library_review table")

            # Migration 20: notifications table (header notification centre)
            cursor.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='notifications'"
            )
            notifications_existed = cursor.fetchone()[0] > 0
            self._create_notifications_table(cursor)
            if not notifications_existed:
                migrations_applied += 1
                self.logger.info("Created notifications table")

            # Migration 21: hardcover_match_attempts (books with no Hardcover match yet)
            cursor.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='hardcover_match_attempts'"
            )
            attempts_existed = cursor.fetchone()[0] > 0
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hardcover_match_attempts (
                    book_id INTEGER PRIMARY KEY,
                    attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            if not attempts_existed:
                migrations_applied += 1
                self.logger.info("Created hardcover_match_attempts table")

            # Migration 22: book_categories (Audible category ladders per book)
            cursor.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='book_categories'"
            )
            book_categories_existed = cursor.fetchone()[0] > 0
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS book_categories (
                    book_id INTEGER NOT NULL,
                    audible_category_id TEXT NOT NULL,
                    depth INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (book_id, audible_category_id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_categories_category ON book_categories(audible_category_id)")
            if not book_categories_existed:
                migrations_applied += 1
                self.logger.info("Created book_categories table")

            # Migration 23: clear download_queue.search_result_id left over from the
            # removed search_results table (Repair reported them as foreign key violations)
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='search_results'")
            if cursor.fetchone()[0] == 0:
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='download_queue'")
                if cursor.fetchone()[0]:
                    cursor.execute("UPDATE download_queue SET search_result_id = NULL WHERE search_result_id IS NOT NULL")
                    if cursor.rowcount:
                        migrations_applied += 1
                        self.logger.info(f"Cleared {cursor.rowcount} stale download_queue.search_result_id values")

            # Migration 24: release calendar (watches and the titles they find)
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='release_calendar'")
            calendar_existed = cursor.fetchone()[0] > 0
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS release_calendar_watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    query TEXT,
                    category_id TEXT,
                    label TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_synced_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS release_calendar (
                    asin TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    subtitle TEXT,
                    authors TEXT,
                    narrators TEXT,
                    series_title TEXT,
                    series_sequence TEXT,
                    release_date TEXT NOT NULL,
                    runtime_min INTEGER,
                    language TEXT,
                    publisher TEXT,
                    summary TEXT,
                    cover_url TEXT,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS release_calendar_hits (
                    asin TEXT NOT NULL,
                    watch_id INTEGER NOT NULL,
                    PRIMARY KEY (asin, watch_id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_release_calendar_date ON release_calendar(release_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_release_calendar_hits_watch ON release_calendar_hits(watch_id)")
            if not calendar_existed:
                migrations_applied += 1
                self.logger.info("Created release calendar tables")

            # Migration 25: multiple libraries. The first library has no root_path
            # and follows the Storage library folder (env var or [paths]).
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='libraries'")
            libraries_existed = cursor.fetchone()[0] > 0
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS libraries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    root_path TEXT UNIQUE,
                    naming_template TEXT,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS library_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    library_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0
                )
            """)
            for table, column in (('books', 'library_id'), ('library_review', 'library_id'),
                                  ('download_queue', 'target_library_id')):
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,))
                if not cursor.fetchone()[0]:
                    continue
                cursor.execute(f"PRAGMA table_info({table})")
                if column not in [row[1] for row in cursor.fetchall()]:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_library_id ON books(library_id)")
            if not libraries_existed:
                cursor.execute("INSERT INTO libraries (name, root_path, is_default, sort_order) VALUES ('Main', NULL, 1, 0)")
                main_id = cursor.lastrowid
                # Every catalogued file lived in the single library until now
                cursor.execute(
                    "UPDATE books SET library_id = ? WHERE library_id IS NULL "
                    "AND file_path IS NOT NULL AND TRIM(file_path) != ''", (main_id,)
                )
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='library_review'")
                if cursor.fetchone()[0]:
                    cursor.execute("UPDATE library_review SET library_id = ? WHERE library_id IS NULL", (main_id,))
                migrations_applied += 1
                self.logger.info("Created libraries tables and the Main library")

            # Migration 26: Audiobookshelf connection (library mapping, item links,
            # listening progress, background jobs)
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='abs_links'")
            abs_existed = cursor.fetchone()[0] > 0
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS abs_library_map (
                    library_id INTEGER PRIMARY KEY,
                    abs_library_id TEXT NOT NULL,
                    abs_folder_id TEXT NOT NULL,
                    abs_folder_path TEXT NOT NULL,
                    watcher_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS abs_links (
                    book_id INTEGER PRIMARY KEY,
                    abs_item_id TEXT NOT NULL UNIQUE,
                    abs_media_id TEXT,
                    abs_library_id TEXT NOT NULL,
                    link_method TEXT NOT NULL,
                    rel_path TEXT,
                    missed_syncs INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS abs_progress (
                    book_id INTEGER NOT NULL,
                    abs_user_id TEXT NOT NULL,
                    abs_username TEXT,
                    progress REAL NOT NULL DEFAULT 0,
                    position_sec REAL NOT NULL DEFAULT 0,
                    duration REAL NOT NULL DEFAULT 0,
                    is_finished INTEGER NOT NULL DEFAULT 0,
                    finished_at INTEGER,
                    started_at INTEGER,
                    last_update INTEGER,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (book_id, abs_user_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS abs_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER,
                    action TEXT NOT NULL,
                    payload TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    state TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_abs_jobs_due ON abs_jobs(state, next_run_at)")
            if not abs_existed:
                migrations_applied += 1
                self.logger.info("Created Audiobookshelf tables")

            # Migration 27: release dates are calendar dates; the Audible metadata
            # sync stored them with a midnight time ("2024-03-26T00:00:00")
            trimmed = 0
            for table in ('books', 'audible_library'):
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,))
                if cursor.fetchone()[0]:
                    cursor.execute(
                        f"UPDATE {table} SET release_date = substr(release_date, 1, 10) "
                        "WHERE release_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*'"
                    )
                    trimmed += cursor.rowcount
            if trimmed:
                migrations_applied += 1
                self.logger.info(f"Trimmed the time from {trimmed} release dates")

            if migrations_applied > 0:
                conn.commit()
                self.logger.info(f"Applied {migrations_applied} database migrations")
            else:
                self.logger.debug("No database migrations needed")
            
            conn.close()
        
        except Exception as e:
            self.logger.error(f"Error during database migration: {e}")
            raise
    
    def get_schema_version(self) -> dict:
        """Get current database schema information"""
        try:
            conn = self.connection_manager.get_engine().raw_connection()
            cursor = conn.cursor()
            
            cursor.execute("PRAGMA table_info(books)")
            columns = cursor.fetchall()
            
            cursor.execute("PRAGMA index_list(books)")
            indexes = cursor.fetchall()
            
            conn.close()
            
            return {
                'columns': [{'name': col[1], 'type': col[2], 'not_null': bool(col[3]), 'primary_key': bool(col[5])} for col in columns],
                'indexes': [{'name': idx[1], 'unique': bool(idx[2])} for idx in indexes],
                'total_columns': len(columns),
                'total_indexes': len(indexes)
            }
        
        except Exception as e:
            self.logger.error(f"Error getting schema version: {e}", exc_info=True)
            return {'error': str(e)}
    
    def verify_schema_integrity(self) -> bool:
        """Verify database schema integrity"""
        try:
            conn = self.connection_manager.get_engine().raw_connection()
            cursor = conn.cursor()
            
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            
            conn.close()
            
            if result[0] == 'ok':
                self.logger.info("Database schema integrity check passed")
                return True
            else:
                self.logger.error(f"Database schema integrity check failed: {result[0]}")
                return False
        
        except Exception as e:
            self.logger.error(f"Error verifying schema integrity: {e}", exc_info=True)
            return False
