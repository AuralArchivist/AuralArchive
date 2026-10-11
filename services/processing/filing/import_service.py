"""
Module Name: import_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Manages audiobook importing into the library, coordinating file moves,
    database tracking, validation, and ASIN tagging. Follows the singleton
    service pattern used across the application.

Location:
    /services/processing/filing/import_service.py
"""

from typing import Any, Dict, Optional, Tuple
import os
from pathlib import Path

from .file_operations import FileOperations
from .database_operations import ImportDatabaseOperations
from .validation import ImportValidator
from .asin_tag_embedder import AsinTagEmbedder
from core.logging import get_module_logger
from core.paths import get_path_resolver
from core.convert import normalize_asin
from services.library import record_book_file
from services.container import container


_LOGGER = get_module_logger("Service.Import.Main")


class ImportService:
    """Main import service following DatabaseService singleton pattern."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER

        self.file_ops = FileOperations()
        self.db_ops = ImportDatabaseOperations()
        self.validator = ImportValidator()
        self.asin_embedder = AsinTagEmbedder()

        self._config_loaded = False

        self.naming_template = 'simple'
        self.verify_after_import = True
        self.create_backup_on_error = True
        self.overwrite_existing_files = False

        self._initialize_service()
    
    @property
    def library_base_path(self) -> str:
        """Configured library root (same source as api/system/imports.py)."""
        return get_path_resolver().get_library_dir()

    @property
    def import_base_path(self) -> str:
        """Configured import root (same source as api/system/imports.py)."""
        return get_path_resolver().get_import_dir()

    def _initialize_service(self):
        """Initialize the import service."""
        try:
            self.logger.debug("Initializing ImportService...")
            
            
            self.logger.success("Import service started successfully")
        except Exception as e:
            self.logger.error("Failed to initialize ImportService", extra={"error": str(e)})
            raise
    
    def reload_configuration(self) -> None:
        """Read the settings again (called when they're saved)."""
        self._config_loaded = False
        self._load_configuration()

    def _load_configuration(self):
        """Load import configuration from ConfigService (called lazily)."""
        if self._config_loaded:
            return
            
        try:
            config_service = container.get_config_service()
            
            if config_service:
                naming_config = config_service.get_section('naming')
                self.naming_template = naming_config.get('naming_template', 'simple')
                
                import_config = config_service.get_section('import')
                self.verify_after_import = import_config.get('verify_after_import', True)
                self.create_backup_on_error = import_config.get('create_backup_on_error', True)
                self.overwrite_existing_files = import_config.get('overwrite_existing_files', False)
                
                self._config_loaded = True
                self.logger.debug("Loaded import configuration", extra={"library_base_path": self.library_base_path, "naming_template": self.naming_template})
        except Exception as e:
            self.logger.warning("Could not load configuration, using defaults", extra={"error": str(e)})
            self.naming_template = 'simple'
            self._config_loaded = True

    def _cancel_pending_downloads(self, book_data: Dict) -> None:
        """A book that is now in the library no longer needs to be downloaded."""
        asin = normalize_asin(book_data.get('ASIN') or book_data.get('asin'), uppercase=False)
        if not asin:
            return
        try:
            container.get_download_management_service().cancel_pending_for_asin(asin)
        except Exception as exc:
            self.logger.warning("Could not clear pending downloads after import", extra={"asin": asin, "error": str(exc)})

    def import_book(self, source_file_path: str, book_data: Dict,
                    template_name: Optional[str] = None,
                    library_path: Optional[str] = None,
                    move: bool = True,
                    import_source: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
        """Import an audiobook file to the library.

        Returns:
            Tuple of (success: bool, message: str, destination_path: Optional[str])
        """
        try:
            self._load_configuration()

            if not isinstance(book_data, dict):
                self.logger.error("Import request has invalid book_data type: %s", type(book_data).__name__)
                return False, "Invalid book metadata supplied", None

            raw_asin = book_data.get('ASIN') or book_data.get('asin')
            normalized_asin = normalize_asin(raw_asin, uppercase=False)
            if normalized_asin:
                book_data['ASIN'] = normalized_asin
                book_data['asin'] = normalized_asin

            nice_title = book_data.get('Title') or book_data.get('title') or 'Unknown Title'
            tracker = container.get_status_service()
            status_id = None
            if tracker:
                asin = book_data.get('ASIN') or book_data.get('asin')
                event = tracker.start_event(
                    category='import',
                    title=f"Importing {nice_title}",
                    message='Preparing files…',
                    source='Import Service',
                    entity_id=asin or source_file_path,
                    metadata={'asin': asin, 'source': source_file_path}
                )
                status_id = event['id']

            def mark_failure(message: str):
                if tracker and status_id:
                    tracker.fail_event(status_id, message=message)

            def mark_progress(message: str, progress: float = None):
                if tracker and status_id:
                    updates = {'message': message}
                    if progress is not None:
                        updates['progress'] = progress
                    tracker.update_event(status_id, **updates)

            def mark_success(message: str):
                if tracker and status_id:
                    tracker.complete_event(status_id, message=message)
            
            is_valid, error_msg = self.validator.validate_import_request(source_file_path, book_data)
            if not is_valid:
                mark_failure(error_msg)
                return False, error_msg, None
            
            template_name = template_name or self.naming_template
            library_path = library_path or self.library_base_path
            
            if not os.path.exists(library_path):
                self.logger.error("Library path does not exist", extra={"library_path": library_path})
                mark_failure(f"Library path does not exist: {library_path}")
                return False, f"Library path does not exist: {library_path}", None
            
            file_extension = Path(source_file_path).suffix.lstrip('.')
            
            naming_service = container.get_file_naming_service()
            destination_path = naming_service.generate_file_path(
                book_data=book_data,
                base_path=library_path,
                template_name=template_name,
                file_extension=file_extension
            )
            
            operation = "Moving" if move else "Copying"
            self.logger.info("Import file move/copy", extra={"operation": operation, "title": book_data.get('Title', 'Unknown'), "destination": destination_path})
            mark_progress(f"{operation} files…", progress=25.0)
            
            if os.path.exists(destination_path):
                if self.overwrite_existing_files:
                    self.logger.warning(
                        "Destination file already exists but will be overwritten (config enabled): %s",
                        destination_path,
                    )
                    try:
                        os.remove(destination_path)
                    except Exception as exc:
                        self.logger.error(
                            "Failed to remove existing destination file %s: %s",
                            destination_path,
                            exc, exc_info=True,
                        )
                        mark_failure(f"Could not overwrite existing file: {destination_path}")
                        return False, f"Could not overwrite existing file: {destination_path}", None
                else:
                    self.logger.warning("Destination file already exists", extra={"destination": destination_path})
                    mark_failure(f"File already exists at destination: {destination_path}")
                    return False, f"File already exists at destination: {destination_path}", None
            
            if move:
                success, message = self.file_ops.move_file_atomic(
                    source_file_path, 
                    destination_path,
                    verify=self.verify_after_import
                )
            else:
                success, message = self.file_ops.copy_file_atomic(
                    source_file_path,
                    destination_path,
                    verify=self.verify_after_import
                )
            
            if not success:
                operation_name = "move" if move else "copy"
                mark_failure(f"File {operation_name} failed: {message}")
                return False, f"File {operation_name} failed: {message}", None
            
            asin = normalize_asin(book_data.get('ASIN') or book_data.get('asin'), uppercase=False)
            stored_record = self._persist_book_record(book_data)
            if isinstance(stored_record, dict):
                book_data = stored_record
                asin = normalize_asin(book_data.get('ASIN') or book_data.get('asin'), uppercase=False)

            normalized_source = self._determine_import_source(import_source, book_data)
            if normalized_source and book_data.get('source') != normalized_source:
                book_data['source'] = normalized_source

            if asin and self.asin_embedder:
                embedded = self.asin_embedder.embed_asin(destination_path, asin)
                if embedded:
                    self.logger.debug("Embedded ASIN %s into %s", asin, destination_path)

            file_size = os.path.getsize(destination_path)
            file_format = file_extension.upper()
            file_quality = self.validator.detect_file_quality(destination_path)
            
            if asin:
                db_success = self.db_ops.update_book_import_info(
                    asin=asin,
                    file_path=destination_path,
                    file_size=file_size,
                    file_format=file_format,
                    file_quality=file_quality,
                    naming_template=template_name,
                    database_service=container.get_database_service(),
                    source_label=normalized_source
                )
                
                if not db_success:
                    self.logger.warning("File imported but database update failed", extra={"asin": asin})
                else:
                    record_book_file(asin, destination_path)
            
            operation_past = "moved" if move else "copied"
            self.logger.success(
                "Import completed successfully",
                extra={"operation": operation_past, "destination": destination_path},
            )
            mark_success(f"Successfully imported {nice_title}")
            self._cancel_pending_downloads(book_data)

            try:
                self._cleanup_source_dir(source_file_path, move=move)
            except Exception as cleanup_exc:
                self.logger.warning("Cleanup of source directory failed: %s", cleanup_exc)

            return True, f"Import successful ({operation_past})", destination_path
            
        except Exception as e:
            self.logger.error("Error importing book", extra={"error": str(e)}, exc_info=True)
            if 'mark_failure' in locals():
                mark_failure(f"Import error: {str(e)}")
            return False, f"Import error: {str(e)}", None

    def _cleanup_source_dir(self, source_file_path: str, move: bool = True):
        """Remove the source directory if not the import root and now empty.

        We only attempt cleanup when the file resides under the configured import root
        and is inside a subfolder (not directly in the root). Direct root files are left untouched.
        """
        if not move:
            return

        try:
            self._load_configuration()
        except Exception:
            self.logger.warning("Skipping import folder cleanup; configuration could not be loaded", exc_info=True)
            return

        import_root = getattr(self, 'import_base_path', None)
        if not import_root:
            return

        import_root = os.path.realpath(import_root)
        source_dir = os.path.dirname(os.path.realpath(source_file_path))

        if os.path.commonpath([source_dir, import_root]) != import_root:
            return
        if os.path.normpath(source_dir) == os.path.normpath(import_root):
            return

        try:
            if not os.listdir(source_dir):
                os.rmdir(source_dir)
                self.logger.info("Cleaned up empty source directory: %s", source_dir)
        except OSError:
            pass
    
    def _determine_import_source(self, requested_source: Optional[str], book_data: Dict[str, Any]) -> str:
        """Pick an appropriate source label once a file has been imported."""

        def normalize(value: Optional[str]) -> Optional[str]:
            if not value:
                return None
            value_str = str(value).strip().lower()
            return value_str or None

        candidate = normalize(requested_source)
        if not candidate:
            candidate = normalize(book_data.get('import_source'))
        if not candidate:
            candidate = normalize(book_data.get('source')) or normalize(book_data.get('metadata_source'))

        if not candidate or candidate in {'audible', 'audible_catalog'}:
            candidate = 'manual_import'

        return candidate

    def _persist_book_record(self, book_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ensure the imported book exists in the database after transfer."""
        database_service = container.get_database_service()

        if not database_service:
            return None

        asin = book_data.get('ASIN') or book_data.get('asin')
        asin = normalize_asin(asin, uppercase=False)
        try:
            if asin:
                existing = database_service.books.get_book_by_asin(asin)
                if existing:
                    return existing

            added = database_service.books.add_book(
                book_data,
                library_status=book_data.get('library_status') or 'owned'
            )
            if added and asin:
                return database_service.books.get_book_by_asin(asin)

            if asin:
                return database_service.books.get_book_by_asin(asin)
        except Exception as exc:
            self.logger.warning(
                "Unable to persist book metadata for '%s': %s",
                book_data.get('Title', 'Unknown'),
                exc
            )
        return None
    
