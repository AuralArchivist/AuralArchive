"""
Module Name: metadata_processor.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Normalize and clean metadata for Audible sync workflows.

Location:
    /integrations/audible/metadata_sync/metadata_processor.py
"""

import re
from datetime import datetime
from typing import Dict, Any, Optional

from core.logging import get_module_logger


class MetadataProcessor:
    """Helper class for processing and normalizing metadata"""
    
    def __init__(self, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.MetadataSync.Processor")
    
    def normalize_metadata_to_db_format(self, metadata: Dict[str, Any], asin: str) -> Dict[str, Any]:
        """Convert metadata from various sources to standardized database format."""
        normalized = {
            'asin': asin,
            'title': self._clean_text(metadata.get('Title')),
            'author': self._clean_text(metadata.get('Author')),
            'authors': self._clean_text(metadata.get('Author')),
            'narrator': self._clean_text(metadata.get('Narrator')),
            'narrators': self._clean_text(metadata.get('Narrator')),
            'series_title': self._clean_text(metadata.get('Series')),
            'series_sequence': self._extract_series_sequence(metadata.get('Series')),
            'publisher': self._clean_text(metadata.get('Publisher')),
            'publication_date': self._normalize_date(metadata.get('Publication Date')),
            'release_date': self._normalize_date(metadata.get('Release Date')),
            'summary': self._clean_text(metadata.get('Summary')),
            'description': self._clean_text(metadata.get('Summary')),
            'cover_image_url': metadata.get('Cover Image'),
            'rating': self._parse_rating(metadata.get('Rating')),
            'num_ratings': self._parse_num_ratings(metadata.get('Rating')),
            'language': self._clean_text(metadata.get('Language')),
            'genres': self._process_genres(metadata.get('Genres')),
            'runtime_length_min': self._parse_runtime(metadata.get('Runtime')),
            'duration_minutes': self._parse_runtime(metadata.get('Runtime')),
            'added_date': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'metadata_source': 'metadata_service',
            'sync_status': 'completed'
        }
        
        normalized = {k: v for k, v in normalized.items() if v is not None and v != ''}
        
        return normalized
    
    def create_basic_book_entry(self, basic_book: Dict[str, str]) -> Dict[str, Any]:
        """Create a basic book entry when full metadata is not available."""
        return {
            'asin': basic_book['asin'],
            'title': self._clean_text(basic_book.get('title', 'Unknown Title')),
            'author': self._clean_text(basic_book.get('author', 'Unknown Author')),
            'authors': self._clean_text(basic_book.get('author', 'Unknown Author')),
            'purchase_date': self._normalize_date(basic_book.get('purchase_date'), keep_time=True),
            'added_date': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'sync_status': 'basic_only',
            'metadata_source': 'audible_api_basic'
        }
    
    def _clean_text(self, text: Any) -> Optional[str]:
        """Clean and normalize text fields"""
        if not text:
            return None
        
        if isinstance(text, list):
            text = ', '.join(str(item) for item in text if item)
        
        text = str(text).strip()
        
        text = re.sub(r'\s+', ' ', text)
        
        text = re.sub(r'<[^>]+>', '', text)
        
        return text if text else None
    
    def _normalize_date(self, date_value: Any, keep_time: bool = False) -> Optional[str]:
        """ISO date ("2024-03-26"), or date and time when keep_time is set."""
        if not date_value:
            return None
        
        try:
            if isinstance(date_value, datetime):
                return date_value.isoformat() if keep_time else date_value.date().isoformat()
            if hasattr(date_value, 'isoformat'):
                return date_value.isoformat()
            
            date_str = str(date_value).strip()
            
            date_formats = [
                '%Y-%m-%d',
                '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S',
                '%d/%m/%Y',
                '%m/%d/%Y',
                '%Y/%m/%d',
                '%d-%m-%Y',
                '%m-%d-%Y'
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_str, fmt)
                    return parsed_date.isoformat() if keep_time else parsed_date.date().isoformat()
                except ValueError:
                    continue
            
            year_match = re.search(r'\b(19|20)\d{2}\b', date_str)
            if year_match:
                year = year_match.group()
                return f"{year}-01-01T00:00:00" if keep_time else f"{year}-01-01"
            
        except Exception as exc:
            self.logger.warning(
                "Failed to parse date",
                extra={"value": date_value, "exc": exc}
            )
        
        return None
    
    def _parse_rating(self, rating_value: Any) -> Optional[float]:
        """Parse rating from various formats"""
        if not rating_value:
            return None
        
        try:
            if isinstance(rating_value, (int, float)):
                return float(rating_value)
            
            rating_str = str(rating_value)
            
            patterns = [
                r'(\d+\.?\d*)\s*out\s*of\s*\d+',
                r'(\d+\.?\d*)\s*/\s*\d+',
                r'(\d+\.?\d*)\s*stars?',
                r'(\d+\.?\d*)'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, rating_str, re.IGNORECASE)
                if match:
                    rating = float(match.group(1))
                    if 0 <= rating <= 5:
                        return rating
                    elif 0 <= rating <= 10:
                        return rating / 2
            
        except (ValueError, TypeError) as exc:
            self.logger.warning(
                "Failed to parse rating",
                extra={"value": rating_value, "exc": exc}
            )
        
        return None
    
    def _parse_num_ratings(self, rating_value: Any) -> int:
        """Extract number of ratings from rating string"""
        if not rating_value:
            return 0
        
        try:
            rating_str = str(rating_value)
            
            patterns = [
                r'\(?([\d,]+)\s*ratings?\)?',
                r'\(?([\d,]+)\s*reviews?\)?',
                r'([\d,]+)\s*people\s*rated',
                r'rated\s*by\s*([\d,]+)'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, rating_str, re.IGNORECASE)
                if match:
                    num_str = match.group(1).replace(',', '')
                    return int(num_str)
            
        except (ValueError, TypeError) as exc:
            self.logger.warning(
                "Failed to parse num_ratings",
                extra={"value": rating_value, "exc": exc}
            )
        
        return 0
    
    def _extract_series_sequence(self, series_value: Any) -> Optional[str]:
        """Extract series sequence number from series string"""
        if not series_value:
            return None
        
        try:
            series_str = str(series_value)
            
            patterns = [
                r'#(\d+\.?\d*)',
                r'book\s+(\d+\.?\d*)',
                r'volume\s+(\d+\.?\d*)',
                r'part\s+(\d+\.?\d*)'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, series_str, re.IGNORECASE)
                if match:
                    return match.group(1)
            
        except (ValueError, TypeError) as exc:
            self.logger.warning(
                "Failed to extract series sequence",
                extra={"value": series_value, "exc": exc}
            )
        
        return None
    
    def _process_genres(self, genres_value: Any) -> Optional[str]:
        """Process genres into a consistent format"""
        if not genres_value:
            return None
        
        try:
            if isinstance(genres_value, list):
                genres = [str(g).strip() for g in genres_value if g]
            else:
                genres_str = str(genres_value)
                genres = [g.strip() for g in re.split(r'[,;|]', genres_str) if g.strip()]
            
            cleaned_genres = []
            for genre in genres:
                genre = re.sub(r'\s+', ' ', genre.strip())
                genre = genre.title()
                if genre and genre not in cleaned_genres:
                    cleaned_genres.append(genre)
            
            return ', '.join(cleaned_genres) if cleaned_genres else None
            
        except Exception as exc:
            self.logger.warning(
                "Failed to process genres",
                extra={"value": genres_value, "exc": exc},
                exc_info=True,
            )
            return None
    
    def _parse_runtime(self, runtime_value: Any) -> Optional[int]:
        """Parse runtime to minutes"""
        if not runtime_value:
            return None
        
        try:
            runtime_str = str(runtime_value).lower()
            
            total_minutes = 0
            
            hour_match = re.search(r'(\d+)\s*h', runtime_str)
            if hour_match:
                total_minutes += int(hour_match.group(1)) * 60
            
            minute_match = re.search(r'(\d+)\s*m', runtime_str)
            if minute_match:
                total_minutes += int(minute_match.group(1))
            
            if total_minutes == 0:
                minute_match = re.search(r'(\d+)\s*minutes?', runtime_str)
                if minute_match:
                    total_minutes = int(minute_match.group(1))
            
            return total_minutes if total_minutes > 0 else None
            
        except (ValueError, TypeError) as exc:
            self.logger.warning(
                "Failed to parse runtime",
                extra={"value": runtime_value, "exc": exc}
            )
            return None