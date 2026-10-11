"""
Module Name: library_parser.py
Author: AuralArchivist
Created: August 24, 2025
Last Modified: October 9, 2026
Description:
    Parse and analyze Audible library data from API/CLI exports for search and stats.

Location:
    /integrations/audible/library/library_parser.py
"""

import csv
import re
from typing import Dict, List, Any, Optional
from datetime import datetime
from collections import defaultdict, Counter

from core.logging import get_module_logger


class AudibleLibraryParser:
    """
    Parses and processes Audible library data from various export formats.
    
    This class provides parsing capabilities for Audible library
    exports, including metadata extraction, search functionality, and statistics
    generation.
    """
    
    def __init__(self, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.Library.Parser")
        
        self.logger.debug("AudibleLibraryParser initialized")
    
    def parse_library_data(self, raw_data: Any, format_type: str = 'json') -> Dict[str, Any]:
        """Parse raw library data into standardized format."""
        try:
            if format_type == 'json':
                return self._parse_json_library(raw_data)
            elif format_type in ['csv', 'tsv']:
                return self._parse_csv_library(raw_data, format_type)
            else:
                raise ValueError(f"Unsupported format type: {format_type}")
                
        except Exception as exc:
            self.logger.error(
                "Error parsing library data",
                extra={"format_type": format_type, "exc": exc}, exc_info=True
            )
            return {
                'books': [],
                'error': str(exc),
                'format': format_type,
                'parsed_count': 0
            }
    
    def _parse_json_library(self, json_data: Any) -> Dict[str, Any]:
        """Parse JSON format library data."""
        books = []
        
        try:
            if isinstance(json_data, list):
                book_list = json_data
            elif isinstance(json_data, dict):
                book_list = (json_data.get('items') or 
                           json_data.get('books') or 
                           json_data.get('library') or 
                           [json_data])
            else:
                book_list = []
            
            for book_data in book_list:
                parsed_book = self._parse_book_data(book_data)
                if parsed_book:
                    books.append(parsed_book)
            
            self.logger.info(
                "Parsed books from JSON",
                extra={"parsed_count": len(books)}
            )
            
            return {
                'books': books,
                'format': 'json',
                'parsed_count': len(books),
                'parse_date': datetime.now().isoformat()
            }
            
        except Exception as exc:
            self.logger.error(
                "Error parsing JSON library",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'books': [],
                'error': str(exc),
                'format': 'json',
                'parsed_count': 0
            }
    
    def _parse_csv_library(self, csv_data: str, format_type: str) -> Dict[str, Any]:
        """Parse CSV/TSV format library data."""
        books = []
        delimiter = '\t' if format_type == 'tsv' else ','
        
        try:
            lines = csv_data.strip().split('\n')
            if not lines:
                return {'books': [], 'format': format_type, 'parsed_count': 0}
            
            csv_reader = csv.DictReader(lines, delimiter=delimiter)
            
            for row in csv_reader:
                parsed_book = self._parse_book_data(row)
                if parsed_book:
                    books.append(parsed_book)
            
            self.logger.info(
                "Parsed books from delimited export",
                extra={"parsed_count": len(books), "format": format_type}
            )
            
            return {
                'books': books,
                'format': format_type,
                'parsed_count': len(books),
                'parse_date': datetime.now().isoformat()
            }
            
        except Exception as exc:
            self.logger.error(
                "Error parsing delimited library",
                extra={"format": format_type, "exc": exc}, exc_info=True
            )
            return {
                'books': [],
                'error': str(exc),
                'format': format_type,
                'parsed_count': 0
            }
    
    def _parse_book_data(self, book_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse individual book data into standardized format."""
        try:
            parsed_book = {}
            
            parsed_book['title'] = self._extract_field(book_data, ['title', 'product_title', 'name'])
            
            authors = self._extract_field(book_data, ['authors', 'author', 'author_name'])
            parsed_book['authors'] = self._normalize_list_field(authors)
            
            narrators = self._extract_field(book_data, ['narrators', 'narrator', 'narrator_name'])
            parsed_book['narrators'] = self._normalize_list_field(narrators)
            
            parsed_book['asin'] = self._extract_field(book_data, ['asin', 'id', 'product_id'])
            
            series = self._extract_field(book_data, ['series', 'series_title'])
            parsed_book['series'] = self._normalize_series_field(series)
            
            length = self._extract_field(book_data, ['length', 'runtime_length_min', 'duration'])
            parsed_book['length_minutes'] = self._normalize_duration_field(length)
            
            genres = self._extract_field(book_data, ['genre', 'genres', 'categories'])
            parsed_book['genres'] = self._normalize_list_field(genres)
            
            rating = self._extract_field(book_data, ['rating', 'customer_rating', 'overall_rating'])
            parsed_book['rating'] = self._normalize_rating_field(rating)
            
            num_ratings = self._extract_field(book_data, ['num_ratings', 'rating_count', 'total_ratings'])
            parsed_book['num_ratings'] = self._normalize_integer_field(num_ratings)
            
            parsed_book['purchase_date'] = self._extract_field(book_data, ['purchase_date', 'date_added'])
            parsed_book['release_date'] = self._extract_field(book_data, ['release_date', 'publication_date'])
            
            parsed_book['subtitle'] = self._extract_field(book_data, ['subtitle', 'sub_title'])
            parsed_book['language'] = self._extract_field(book_data, ['language', 'lang'])
            parsed_book['publisher'] = self._extract_field(book_data, ['publisher', 'publisher_name'])
            
            if parsed_book.get('title') and parsed_book.get('asin'):
                return parsed_book
            else:
                self.logger.debug(
                    "Skipping book with insufficient data",
                    extra={"book": parsed_book}
                )
                return None
                
        except Exception as exc:
            self.logger.error(
                "Error parsing individual book data",
                extra={"exc": exc}, exc_info=True
            )
            return None
    
    def _extract_field(self, data: Dict[str, Any], field_names: List[str]) -> Any:
        """Extract field value from data using multiple possible field names."""
        for field_name in field_names:
            if field_name in data and data[field_name] is not None:
                return data[field_name]
        return None
    
    def _normalize_list_field(self, value: Any) -> List[str]:
        """Normalize a field that could be a string, list, or None into a list of strings."""
        if not value:
            return []
        
        if isinstance(value, list):
            return [str(item).strip() for item in value if item]
        elif isinstance(value, str):
            if ',' in value:
                return [item.strip() for item in value.split(',') if item.strip()]
            else:
                return [value.strip()]
        else:
            return [str(value).strip()]
    
    def _normalize_series_field(self, value: Any) -> Optional[Dict[str, Any]]:
        """Normalize series field into standardized format."""
        if not value:
            return None
        
        if isinstance(value, dict):
            return {
                'title': value.get('title', ''),
                'sequence': value.get('sequence', ''),
                'position': value.get('position', '')
            }
        elif isinstance(value, str):
            series_match = re.match(r'^(.+?)\s*(?:#|Book\s+)(\d+)$', value.strip())
            if series_match:
                return {
                    'title': series_match.group(1).strip(),
                    'sequence': series_match.group(2),
                    'position': series_match.group(2)
                }
            else:
                return {
                    'title': value.strip(),
                    'sequence': '',
                    'position': ''
                }
        
        return None
    
    def _normalize_duration_field(self, value: Any) -> Optional[int]:
        """Normalize duration field to minutes."""
        if not value:
            return None
        
        if isinstance(value, (int, float)):
            return int(value)
        
        if isinstance(value, str):
            value = value.strip().lower()
            
            hrs_mins_match = re.search(r'(\d+)\s*hrs?\s*(?:and\s*)?(\d+)\s*mins?', value)
            if hrs_mins_match:
                hours = int(hrs_mins_match.group(1))
                minutes = int(hrs_mins_match.group(2))
                return hours * 60 + minutes
            
            time_match = re.search(r'(\d+):(\d+):(\d+)', value)
            if time_match:
                hours = int(time_match.group(1))
                minutes = int(time_match.group(2))
                return hours * 60 + minutes
            
            mins_match = re.search(r'(\d+)\s*mins?', value)
            if mins_match:
                return int(mins_match.group(1))
            
            hrs_match = re.search(r'(\d+)\s*hrs?', value)
            if hrs_match:
                return int(hrs_match.group(1)) * 60
            
            if value.isdigit():
                return int(value)
        
        return None
    
    def _normalize_rating_field(self, value: Any) -> Optional[float]:
        """Normalize rating field to float value."""
        if not value:
            return None
        
        if isinstance(value, (int, float)):
            return float(value)
        elif isinstance(value, str):
            rating_match = re.search(r'(\d+\.?\d*)', value.strip())
            if rating_match:
                return float(rating_match.group(1))
        
        return None

    def _normalize_integer_field(self, value: Any) -> Optional[int]:
        """Normalize integer-like values into an int."""
        if value in (None, ""):
            return None

        try:
            if isinstance(value, int):
                return value

            if isinstance(value, float):
                return int(value)

            if isinstance(value, str):
                normalized = value.strip().lower()

                if not normalized:
                    return None

                if normalized.endswith("k"):
                    number = float(normalized[:-1].replace(",", ""))
                    return int(number * 1000)

                normalized = normalized.replace(",", "")

                if normalized.isdigit():
                    return int(normalized)

                match = re.search(r"(\d+)", normalized)
                if match:
                    return int(match.group(1))

            return int(value)
        except (ValueError, TypeError):
            return None
    
    def calculate_library_stats(self, books: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate statistics for the library."""
        try:
            total_books = len(books)
            
            if total_books == 0:
                return {'total_books': 0, 'message': 'No books in library'}
            
            stats = {
                'total_books': total_books,
                'total_duration_minutes': 0,
                'average_rating': 0,
                'books_with_ratings': 0
            }
            
            authors = []
            narrators = []
            genres = []
            series = []
            ratings = []
            durations = []
            
            for book in books:
                if book.get('authors'):
                    authors.extend(book['authors'])
                
                if book.get('narrators'):
                    narrators.extend(book['narrators'])
                
                if book.get('genres'):
                    genres.extend(book['genres'])
                
                if book.get('series') and book['series'].get('title'):
                    series.append(book['series']['title'])
                
                if book.get('rating') is not None:
                    ratings.append(book['rating'])
                
                if book.get('length_minutes'):
                    durations.append(book['length_minutes'])
            
            stats['total_duration_minutes'] = sum(durations)
            stats['total_duration_hours'] = round(stats['total_duration_minutes'] / 60, 1)
            stats['average_duration_minutes'] = round(sum(durations) / len(durations), 0) if durations else 0
            
            if ratings:
                stats['average_rating'] = round(sum(ratings) / len(ratings), 2)
                stats['books_with_ratings'] = len(ratings)
            
            stats['unique_authors'] = len(set(authors))
            stats['unique_narrators'] = len(set(narrators))
            stats['unique_genres'] = len(set(genres))
            stats['unique_series'] = len(set(series))
            
            author_counts = Counter(authors)
            narrator_counts = Counter(narrators)
            genre_counts = Counter(genres)
            series_counts = Counter(series)
            
            stats['top_authors'] = author_counts.most_common(10)
            stats['top_narrators'] = narrator_counts.most_common(10)
            stats['top_genres'] = genre_counts.most_common(10)
            stats['top_series'] = series_counts.most_common(10)
            
            if ratings:
                rating_dist = defaultdict(int)
                for rating in ratings:
                    rating_key = f"{int(rating)}-{int(rating)}.9"
                    rating_dist[rating_key] += 1
                stats['rating_distribution'] = dict(rating_dist)
            
            self.logger.info(
                "Calculated library statistics",
                extra={"book_count": total_books}
            )
            return stats
            
        except Exception as exc:
            self.logger.error(
                "Error calculating library stats",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'total_books': len(books) if books else 0,
                'error': str(exc)
            }
    
    def search_books(self, books: List[Dict[str, Any]], query: str, 
                    search_fields: List[str] = None) -> List[Dict[str, Any]]:
        """Search books in the library based on query and fields."""
        try:
            if not query or not books:
                return []
            
            if search_fields is None:
                search_fields = ['title', 'authors', 'narrators', 'series']
            
            query_lower = query.lower()
            matching_books = []
            
            for book in books:
                match_found = False
                
                for field in search_fields:
                    if self._search_in_field(book, field, query_lower):
                        match_found = True
                        break
                
                if match_found:
                    matching_books.append(book)
            
            self.logger.debug(
                "Completed library search",
                extra={"query": query, "matches": len(matching_books), "fields": search_fields}
            )
            return matching_books
            
        except Exception as exc:
            self.logger.error(
                "Error searching books",
                extra={"query": query, "exc": exc}, exc_info=True
            )
            return []
    
    def _search_in_field(self, book: Dict[str, Any], field: str, query: str) -> bool:
        """Search for query in a specific book field."""
        field_value = book.get(field)
        
        if not field_value:
            return False
        
        if isinstance(field_value, str):
            return query in field_value.lower()
        elif isinstance(field_value, list):
            return any(query in str(item).lower() for item in field_value)
        elif isinstance(field_value, dict):
            if field == 'series':
                series_title = field_value.get('title') or ''
                return query in series_title.lower()
            else:
                return any(query in str(value).lower() for value in field_value.values())
        else:
            return query in str(field_value).lower()
