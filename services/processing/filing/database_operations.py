"""
Module Name: database_operations.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Database helper for import tracking: updates book records with library
    paths, formats, quality, and import timestamps.

Location:
    /services/processing/filing/database_operations.py
"""

from datetime import datetime

from sqlalchemy import text

from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Import.DatabaseOperations")


class ImportDatabaseOperations:
    """Handles database operations for import tracking."""

    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER

    def update_book_import_info(self, asin: str, file_path: str, file_size: int,
                                file_format: str, file_quality: str, naming_template: str,
                                database_service, source_label: str = 'manual_import') -> bool:
        """Update book record with import information."""
        try:
            import_date = int(datetime.now().timestamp())
            engine = database_service.connection_manager.get_engine()

            with engine.begin() as conn:
                result = conn.execute(
                    text("""
                        UPDATE books SET
                            file_path = :file_path,
                            file_size = :file_size,
                            file_format = :file_format,
                            file_quality = :file_quality,
                            library_status = :library_status,
                            source = :source,
                            imported_to_library = 1,
                            import_date = :import_date,
                            naming_template = :naming_template
                        WHERE asin = :asin
                    """),
                    {
                        "file_path": file_path,
                        "file_size": file_size,
                        "file_format": file_format,
                        "file_quality": file_quality,
                        "library_status": "owned",
                        "source": source_label,
                        "import_date": import_date,
                        "naming_template": naming_template,
                        "asin": asin,
                    },
                )
                updated = result.rowcount > 0

            if updated:
                self.logger.info(f"Updated import info for ASIN: {asin}")

                try:
                    if database_service.series:
                        updated_count = database_service.series.sync_library_status()
                        self.logger.debug(f"Synced series library status: {updated_count} records updated")
                except Exception as e:
                    self.logger.warning(f"Failed to sync series library status: {e}")

                return True
            else:
                self.logger.warning(f"No book found with ASIN: {asin}")
                return False

        except Exception as e:
            self.logger.error(f"Error updating import info for ASIN {asin}: {e}", exc_info=True)
            return False
