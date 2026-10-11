"""
Module Name: template_parser.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Parses and validates naming templates, handling variable substitution and
    validation for AudioBookShelf-friendly paths.

Location:
    /services/processing/naming/template_parser.py
"""

import re
from typing import Dict, Optional, Tuple
from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.FileNaming.TemplateParser")


class TemplateParser:
    """
    Parses naming templates with variable substitution.
    
    Supported variables:
    - {author} - Primary author name
    - {title} - Book title
    - {series} - Series name
    - {series_number} - Book number in series (e.g., "Book 01")
    - {year} - Publication year
    - {narrator} - Primary narrator name
    - {asin} - Audible ASIN
    - {publisher} - Publisher name
    - {runtime} - Runtime in format "Xh Ym"
    """
    
    VALID_VARIABLES = {
        'author', 'title', 'series', 'series_number', 'year',
        'narrator', 'asin', 'publisher', 'runtime'
    }
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        self.variable_pattern = re.compile(r'\{(\w+)\}')
    
    def get_template(self, template_name: str, templates: Dict[str, str]) -> str:
        """Get a template by name, falling back to 'simple' if not found."""
        template = templates.get(template_name)
        if not template:
            self.logger.warning(f"Template '{template_name}' not found, using 'simple'")
            return templates.get('simple', '{author}/{series}/{title}/{title}')
        return template
    
    def add_custom_template(self, name: str, template: str, templates: Dict[str, str]) -> bool:
        """Add or update a custom template after validation."""
        try:
            is_valid, error = self.validate_template(template)
            if not is_valid:
                self.logger.error(f"Invalid template '{name}': {error}")
                return False
            
            templates[name] = template
            self.logger.info(f"Added custom template '{name}': {template}")
            return True
        except Exception as e:
            self.logger.error(f"Error adding template '{name}': {e}", exc_info=True)
            return False
    
    def validate_template(self, template: str) -> Tuple[bool, Optional[str]]:
        """Validate a template string.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(template, str) or not template.strip():
            return False, "Template cannot be empty"
        
        variables = self.variable_pattern.findall(template)
        
        invalid_vars = [var for var in variables if var not in self.VALID_VARIABLES]
        if invalid_vars:
            return False, f"Invalid variables: {', '.join(invalid_vars)}"
        
        if '..' in template:
            return False, "Template cannot contain path traversal sequences (..)"
        
        if template.startswith('/') or template.startswith('\\'):
            return False, "Template cannot start with absolute path separator"
        
        if re.match(r'^[a-zA-Z]:', template):
            return False, "Template cannot contain Windows drive letters"
        
        return True, None
    
    def parse_template(self, template: str, book_data: Dict) -> str:
        """Parse a template by substituting variables with book data."""
        try:
            result = template
            
            for var in self.VALID_VARIABLES:
                placeholder = '{' + var + '}'
                if placeholder in result:
                    value = self._get_variable_value(var, book_data)
                    if value is None:
                        value_str = ''
                    elif isinstance(value, str):
                        value_str = value
                    else:
                        value_str = str(value)
                    result = result.replace(placeholder, value_str)
            
            while '//' in result:
                result = result.replace('//', '/')

            result = result.replace('/N/A/', '/').replace('N/A/', '').replace('/N/A', '')

            return result.strip('/')
        except Exception as e:
            self.logger.error(f"Error parsing template: {e}")
            raise
    
    def _get_variable_value(self, variable: str, book_data: Dict) -> str:
        """Get the value for a template variable from book data."""
        
        if variable == 'author':
            authors = (
                book_data.get('AuthorName')
                or book_data.get('author')
                or book_data.get('Author')
                or book_data.get('authors')
            )

            if isinstance(authors, list):
                author_name = authors[0] if authors else 'Unknown Author'
            else:
                author_name = authors or 'Unknown Author'
            return author_name
        elif variable == 'title':
            value = book_data.get('Title', book_data.get('title'))
            return str(value) if value else 'Unknown Title'
        elif variable == 'series':
            return self._get_series_name(book_data)
        elif variable == 'series_number':
            return self._get_series_number(book_data)
        elif variable == 'year':
            return self._get_year(book_data)
        elif variable == 'narrator':
            return self._get_narrator_name(book_data)
        elif variable == 'asin':
            value = book_data.get('ASIN', book_data.get('asin'))
            return str(value) if value else ''
        elif variable == 'publisher':
            value = book_data.get('Publisher', book_data.get('publisher'))
            return str(value) if value else 'Unknown Publisher'
        elif variable == 'runtime':
            return self._get_runtime(book_data)
        else:
            return ''
    
    def _get_series_name(self, book_data: Dict) -> str:
        """Extract series name from book data."""
        series = (book_data.get('SeriesName') or 
                 book_data.get('Series') or 
                 book_data.get('series'))
        return str(series) if series else ''
    
    def _get_series_number(self, book_data: Dict) -> str:
        """
        Extract and format series number.
        
        Returns a numeric string for use in templates (e.g., "1", "1.5").
        """
        book_number = (book_data.get('book_number') or 
                      book_data.get('BookNumber'))
        
        if not book_number:
            return ''
        
        try:
            number = float(book_number)
            if number == int(number):
                return str(int(number))
            return f"{number:g}"
        except (ValueError, TypeError):
            return str(book_number)
    
    def _get_year(self, book_data: Dict) -> str:
        """Extract publication year."""
        year = (book_data.get('release_date') or 
               book_data.get('ReleaseDate') or 
               book_data.get('year'))
        
        if year:
            year_str = str(year)
            if '-' in year_str:
                year_str = year_str.split('-')[0]
            return year_str
        
        return ''
    
    def _get_narrator_name(self, book_data: Dict) -> str:
        """Extract primary narrator name."""
        narrator = (book_data.get('narrator_name') or 
                   book_data.get('Narrator') or 
                   book_data.get('narrator'))
        
        if not narrator:
            narrators = book_data.get('Narrators', book_data.get('narrators', []))
            if narrators:
                narrator = narrators[0] if isinstance(narrators, list) else narrators
        
        return str(narrator) if narrator else ''
    
    def _get_runtime(self, book_data: Dict) -> str:
        """
        Extract and format runtime.
        
        Returns formatted string like "12h 34m" or empty string if not available.
        """
        runtime = book_data.get('RuntimeLengthMin', book_data.get('runtime_minutes'))
        
        if not runtime:
            return ''
        
        try:
            minutes = int(runtime)
            hours = minutes // 60
            mins = minutes % 60
            return f"{hours}h {mins:02d}m"
        except (ValueError, TypeError):
            return ''
    