"""
Module Name: template_parser.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: November 12, 2026
Description:
    Reads the values naming layouts write (author, title, series, number, year,
    narrator, ASIN, publisher, runtime) from a book's data.

Location:
    /services/processing/naming/template_parser.py
"""

from typing import Dict
from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.FileNaming.TemplateParser")


class TemplateParser:
    """
    Reads the values naming layouts write from a book's data.

    Values: author, title, series, series_number ("1", "1.5"), year, narrator,
    asin, publisher and runtime ("Xh Ym").
    """
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER

    def _get_variable_value(self, variable: str, book_data: Dict) -> str:
        """Get one value from book data."""
        
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
            return str(value) if value else ''
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
                      book_data.get('BookNumber') or
                      book_data.get('Sequence') or
                      book_data.get('series_number'))

        if not book_number or book_number == 'N/A':
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
    