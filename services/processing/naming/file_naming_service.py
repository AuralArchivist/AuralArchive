"""
Module Name: file_naming_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: November 12, 2026
Description:
    Singleton service for generating Audiobookshelf-compatible file paths and
    filenames from the selected block layout, with sanitization helpers.

Location:
    /services/processing/naming/file_naming_service.py
"""

import os
from typing import Any, Dict, List

from .layout import BUILT_IN_LAYOUTS, render, uses_asin
from .layout_store import get_layout, marketing_words
from .path_generator import PathGenerator
from .sanitizer import PathSanitizer
from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.FileNaming.Main")


class FileNamingService:
    """Main file naming service following DatabaseService singleton pattern."""
    
    def __init__(self, *_, logger=None, **__):
        self.logger = logger or _LOGGER

        self.path_generator = PathGenerator()
        self.sanitizer = PathSanitizer()
        self._config_loaded = False

        self.include_asin = False
        self.create_author_folders = True
        self.create_series_folders = True

        self._initialize_service()
    
    def _initialize_service(self):
        """Initialize the file naming service."""
        try:
            self.logger.debug("Initializing FileNamingService...")
            
            
            self.logger.success("File naming service started successfully")
        except Exception as e:
            self.logger.error("Failed to initialize FileNamingService", extra={"error": str(e)})
            raise
    
    def reload_configuration(self) -> None:
        """Read the settings again (called when they're saved)."""
        self._config_loaded = False
        self._load_configuration()

    def _load_configuration(self):
        """Load naming configuration from ConfigService (called lazily)."""
        if self._config_loaded:
            return
            
        try:
            from services.container import container
            config_service = container.get_config_service()
            
            if config_service:
                naming_config = config_service.get_section('naming')
                
                self.include_asin = naming_config.get('include_asin_in_path', False)
                self.create_author_folders = naming_config.get('create_author_folders', True)
                self.create_series_folders = naming_config.get('create_series_folders', True)

                self._config_loaded = True
                self.logger.debug("Loaded naming configuration", extra={"include_asin": self.include_asin, "create_author_folders": self.create_author_folders, "create_series_folders": self.create_series_folders})
        except Exception as e:
            self.logger.warning("Could not load configuration, using defaults", extra={"error": str(e)})
            self._config_loaded = True
    
    def _layout(self, template_name: str) -> Dict[str, Any]:
        layout = get_layout(template_name)
        if layout is None:
            self.logger.warning("Naming layout %s not found, using Simple", template_name)
            return BUILT_IN_LAYOUTS['simple']
        return layout

    def _parts(self, book_data: Dict, template_name: str) -> List[str]:
        """Folders, then the file name, each sanitized."""
        self._load_configuration()
        layout = self._layout(template_name)
        parts = render(layout, book_data, marketing_words())
        asin = book_data.get('ASIN', book_data.get('asin'))
        if self.include_asin and asin and not uses_asin(layout):
            parts[-1] = f"{parts[-1]} [{asin}]"
        return [self.sanitizer.sanitize_path_component(part) for part in parts]

    def generate_file_path(self, book_data: Dict, base_path: str, template_name: str = 'simple',
                           file_extension: str = 'm4b') -> str:
        """Generate a complete file path for an audiobook."""
        *folders, filename = self._parts(book_data, template_name)
        full_path = os.path.join(base_path, *folders, f"{filename}.{file_extension}")
        return self.sanitizer.sanitize_path(full_path)

    def generate_folder_path(self, book_data: Dict, base_path: str) -> str:
        """Generate just the folder path (no filename)."""
        self._load_configuration()
        
        return self.path_generator.generate_folder_path(
            book_data=book_data,
            base_path=base_path,
            create_author_folders=self.create_author_folders,
            create_series_folders=self.create_series_folders,
            sanitizer=self.sanitizer
        )
    
    def generate_filename(self, book_data: Dict, template_name: str = 'simple',
                          file_extension: str = 'm4b') -> str:
        """Generate just the filename (no path)."""
        return f"{self._parts(book_data, template_name)[-1]}.{file_extension}"

    def sanitize_path(self, path: str) -> str:
        """Sanitize a file path for cross-platform compatibility."""
        return self.sanitizer.sanitize_path(path)
    
    def sanitize_filename(self, filename: str) -> str:
        """Sanitize a filename for cross-platform compatibility."""
        return self.sanitizer.sanitize_filename(filename)
