"""
Module Name: path_generator.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Generates complete file paths from templates and metadata, combining naming
    parsing and sanitization to create AudioBookShelf-compatible paths.

Location:
    /services/processing/naming/path_generator.py
"""

import os
import re
from typing import Dict, Optional
from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.FileNaming.PathGenerator")


class PathGenerator:
    """Generates complete file paths for audiobooks."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        self.asin_pattern = re.compile(r'\[([A-Z0-9]+)\]')
    
    def generate_file_path(self, book_data: Dict, base_path: str, template: str, 
                          file_extension: str, include_asin: bool, sanitizer) -> str:
        """Generate a complete file path."""
        try:
            from .template_parser import TemplateParser
            parser = TemplateParser()
            
            parsed_path = parser.parse_template(template, book_data)
            
            if include_asin and '{asin}' not in template:
                asin = book_data.get('ASIN', book_data.get('asin'))
                if asin:
                    parsed_path = f"{parsed_path} [{asin}]"
            
            components = parsed_path.split('/')
            
            sanitized_components = [sanitizer.sanitize_path_component(comp) for comp in components]
            
            folder_path = os.path.join(base_path, *sanitized_components[:-1]) if len(sanitized_components) > 1 else base_path
            filename = sanitized_components[-1] if sanitized_components else 'unknown'
            
            if not filename.lower().endswith(f'.{file_extension.lower()}'):
                filename = f"{filename}.{file_extension}"
            
            full_path = os.path.join(folder_path, filename)
            
            full_path = sanitizer.sanitize_path(full_path)
            
            self.logger.debug(f"Generated path: {full_path}")
            return full_path
            
        except Exception as e:
            self.logger.error(f"Error generating file path: {e}")
            raise
    
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
    
    def generate_filename(self, book_data: Dict, template: str, file_extension: str,
                         include_asin: bool, sanitizer) -> str:
        """Generate just the filename (no path)."""
        try:
            from .template_parser import TemplateParser
            parser = TemplateParser()
            
            parsed = parser.parse_template(template, book_data)
            
            if '/' in parsed:
                parsed = parsed.split('/')[-1]
            
            if include_asin and '{asin}' not in template:
                asin = book_data.get('ASIN', book_data.get('asin'))
                if asin:
                    parsed = f"{parsed} [{asin}]"
            
            filename = sanitizer.sanitize_filename(parsed)
            
            if not filename.lower().endswith(f'.{file_extension.lower()}'):
                filename = f"{filename}.{file_extension}"
            
            return filename
            
        except Exception as e:
            self.logger.error(f"Error generating filename: {e}")
            raise
    
    def parse_abs_path(self, file_path: str, sanitizer) -> Dict[str, Optional[str]]:
        """Parse an existing ABS file path to extract metadata.

        Attempts to extract:
        - Author (from folder structure)
        - Series (from folder structure)
        - Title
        - ASIN (from brackets)
        - Year (from parentheses)
        - Series number (from "Book XX" pattern)
        """
        try:
            normalized = sanitizer.sanitize_path(file_path)
            
            components = normalized.split(os.sep)
            
            filename = components[-1] if components else ''
            
            name_without_ext = os.path.splitext(filename)[0]
            
            metadata = {
                'author': None,
                'series': None,
                'title': None,
                'asin': None,
                'year': None,
                'series_number': None,
                'narrator': None
            }
            
            asin_match = self.asin_pattern.search(name_without_ext)
            if asin_match:
                metadata['asin'] = asin_match.group(1)
                name_without_ext = self.asin_pattern.sub('', name_without_ext).strip()
            
            year_match = re.search(r'\((\d{4})\)', name_without_ext)
            if year_match:
                metadata['year'] = year_match.group(1)
                name_without_ext = re.sub(r'\(\d{4}\)', '', name_without_ext).strip()
            
            series_num_match = re.search(r'(?:Book\s+)?(\d+(?:\.\d+)?)\s*-', name_without_ext)
            if series_num_match:
                metadata['series_number'] = series_num_match.group(1)
            
            if len(components) >= 3:
                metadata['author'] = components[-3]
                metadata['series'] = components[-2]
            elif len(components) >= 2:
                metadata['author'] = components[-2]
            
            parts = name_without_ext.split(' - ')
            
            if parts:
                title_part = parts[0].strip()
                title_part = re.sub(r'^(?:Book\s+)?\d+(?:\.\d+)?\s*-?\s*', '', title_part).strip()
                metadata['title'] = title_part
            
            if len(parts) > 1:
                metadata['narrator'] = parts[-1].strip()
            
            self.logger.debug(f"Parsed path metadata: {metadata}")
            return metadata
            
        except Exception as e:
            self.logger.error(f"Error parsing ABS path: {e}", exc_info=True)
            return {
                'author': None,
                'series': None,
                'title': None,
                'asin': None,
                'year': None,
                'series_number': None,
                'narrator': None
            }
