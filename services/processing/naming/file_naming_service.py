"""
Module Name: file_naming_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Singleton service for generating AudioBookShelf-compatible file paths and
    filenames using user-configurable templates and sanitization helpers.

Location:
    /services/processing/naming/file_naming_service.py
"""

from typing import Dict, Optional, List

from .template_parser import TemplateParser
from .path_generator import PathGenerator
from .sanitizer import PathSanitizer
from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.FileNaming.Main")


class FileNamingService:
    """Main file naming service following DatabaseService singleton pattern."""
    
    def __init__(self, *_, logger=None, **__):
        self.logger = logger or _LOGGER

        self.template_parser = TemplateParser()
        self.path_generator = PathGenerator()
        self.sanitizer = PathSanitizer()
        self._config_loaded = False

        self.templates = {
            'simple': '{author}/{series}/{title}/{title}',
            'simple_asin': '{author}/{series}/{title}/{title} [{asin}]'
        }

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
                
                custom_templates = naming_config.get('naming_templates', {})
                if custom_templates:
                    self.templates.update(custom_templates)
                
                self._config_loaded = True
                self.logger.debug("Loaded naming configuration", extra={"include_asin": self.include_asin, "create_author_folders": self.create_author_folders, "create_series_folders": self.create_series_folders})
        except Exception as e:
            self.logger.warning("Could not load configuration, using defaults", extra={"error": str(e)})
            self._config_loaded = True
    
    def get_template(self, template_name: str = 'simple') -> str:
        """Get a naming template by name."""
        return self.template_parser.get_template(template_name, self.templates)
    
    def add_custom_template(self, name: str, template: str) -> bool:
        """Add or update a custom naming template."""
        return self.template_parser.add_custom_template(name, template, self.templates)
    
    def validate_template(self, template: str) -> tuple[bool, Optional[str]]:
        """Validate a naming template."""
        return self.template_parser.validate_template(template)
    
    def generate_file_path(self, book_data: Dict, base_path: str, template_name: str = 'simple', 
                          file_extension: str = 'm4b') -> str:
        """Generate a complete file path for an audiobook."""
        self._load_configuration()
        
        return self.path_generator.generate_file_path(
            book_data=book_data,
            base_path=base_path,
            template=self.get_template(template_name),
            file_extension=file_extension,
            include_asin=self.include_asin,
            sanitizer=self.sanitizer
        )
    
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
        self._load_configuration()
        
        return self.path_generator.generate_filename(
            book_data=book_data,
            template=self.get_template(template_name),
            file_extension=file_extension,
            include_asin=self.include_asin,
            sanitizer=self.sanitizer
        )
    
    def sanitize_path(self, path: str) -> str:
        """Sanitize a file path for cross-platform compatibility."""
        return self.sanitizer.sanitize_path(path)
    
    def sanitize_filename(self, filename: str) -> str:
        """Sanitize a filename for cross-platform compatibility."""
        return self.sanitizer.sanitize_filename(filename)
    
    def parse_abs_path(self, file_path: str) -> Dict[str, Optional[str]]:
        """Parse an existing ABS file path to extract metadata.

        Returns:
            Dictionary with extracted metadata (author, series, title, asin, etc.)
        """
        return self.path_generator.parse_abs_path(file_path, self.sanitizer)
    
    def get_available_templates(self) -> List[str]:
        """Get list of available template names."""
        return list(self.templates.keys())
    