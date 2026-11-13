"""
Module Name: path_generator.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: November 12, 2026
Description:
    Builds an author and series folder path for a book.

Location:
    /services/processing/naming/path_generator.py
"""

import os
from typing import Dict
from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.FileNaming.PathGenerator")


class PathGenerator:
    """Generates complete file paths for audiobooks."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
    
    def generate_folder_path(self, book_data: Dict, base_path: str, 
                            create_author_folders: bool, create_series_folders: bool,
                            sanitizer) -> str:
        """Generate just the folder path (no filename)."""
        try:
            path_components = [base_path]
            
            if create_author_folders:
                author = book_data.get('Author', book_data.get('author', 'Unknown Author'))
                if isinstance(author, list):
                    author = author[0]
                path_components.append(sanitizer.sanitize_path_component(str(author)))
            
            if create_series_folders:
                series = book_data.get('Series', book_data.get('series'))
                if series and series.lower() != 'standalone':
                    path_components.append(sanitizer.sanitize_path_component(str(series)))
            
            folder_path = os.path.join(*path_components)
            return sanitizer.sanitize_path(folder_path)
            
        except Exception as e:
            self.logger.error(f"Error generating folder path: {e}")
            raise
