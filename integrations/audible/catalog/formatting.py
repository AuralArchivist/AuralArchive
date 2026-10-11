"""
Module Name: formatting.py
Author: AuralArchivist
Created: August 19, 2025
Last Modified: October 9, 2026
Description:
    Normalize Audible catalog API responses into UI/database-friendly book structures.

Location:
    /integrations/audible/catalog/formatting.py
"""

import re
from typing import List, Dict, Any, Optional

from core.logging import get_module_logger
from .cover_utils import cover_utils
from core.text import format_runtime, runtime_to_minutes

class AudibleFormatter:
    """Handles formatting and processing of Audible API response data"""
    
    def __init__(self):
        self.logger = get_module_logger("Service.Audible.CatalogFormatting")
    
    def process_search_results(self, products: List[Dict], region: str = "us") -> List[Dict]:
        """Process raw API results into standardized book data format"""
        books = []
        
        for book in products:
            try:
                formatted_book = self._format_single_book(book, region)
                books.append(formatted_book)
                
            except Exception as e:
                self.logger.error("Error processing book data", extra={"error": str(e)}, exc_info=True)
                self.logger.debug("Problematic book data", extra={"title": book.get('title', 'Unknown'), "asin": book.get('asin')})
                continue
        
        self.logger.info("Processed books", extra={"count": len(books)})
        return books
    
    def _format_single_book(self, book: Dict, region: str) -> Dict[str, Any]:
        """Format a single book from API response to standardized format"""
        try:
            asin = book.get("asin", "")
            title = book.get("title", "Unknown Title")
            
            raw_series = book.get("series", [])
            if raw_series:
                self.logger.debug("Raw series data", extra={"title": title, "series": raw_series})
            
            runtime_minutes = book.get("runtime_length_min", 0)
            runtime_str = self._format_runtime(runtime_minutes)
            
            authors = book.get("authors", [])
            author_str = self._format_authors(authors)
            
            narrators = book.get("narrators", [])
            narrator_str = self._format_narrators(narrators)
            
            series_info = book.get("series", [])
            series_name, series_sequence, series_asin_from_series_array = self._format_series(series_info)
            
            series_asin = self._extract_series_asin_from_relationships(book)
            if not series_asin and series_asin_from_series_array:
                series_asin = series_asin_from_series_array
            
            if series_asin:
                self.logger.debug("Extracted series ASIN", extra={"title": title, "series_asin": series_asin})
            else:
                self.logger.debug("No series ASIN found", extra={"title": title})
            
            rating_info = book.get("rating", {})
            overall_rating = self._format_rating(rating_info)
            num_ratings = self._extract_num_ratings(rating_info)
            
            cover_image = cover_utils.extract_cover_image(book, asin)
            
            release_date = self._format_release_date(book.get("release_date"))
            language = self._format_language(book.get("language"))
            publisher = book.get("publisher_name", "Unknown Publisher")
            summary = self._format_summary(book.get("publisher_summary", "") or 
                                           book.get("merchandising_summary", "") or
                                           book.get("description", "") or
                                           book.get("editorial_review", "") or
                                           "")
            
            book_data = {
                "Title": title,
                "Author": author_str,
                "Series": series_name,
                "Sequence": series_sequence,
                "series_asin": series_asin,
                "Narrator": narrator_str,
                "Summary": summary,
                "Runtime": runtime_str,
                "Release Date": release_date,
                "Language": language,
                "Publisher": publisher,
                "Overall Rating": overall_rating,
                "num_ratings": num_ratings,
                "ASIN": asin,
                "Cover Image": cover_image,
                "Region": region,
                "Categories": book.get("category_ladders") or [],
            }
            
            formatted_contributors = self._format_contributors(book)
            if formatted_contributors:
                book_data["Contributors"] = formatted_contributors
                book_data["contributors"] = formatted_contributors

            self.logger.debug("Formatted book", extra={"title": title, "author": author_str})
            return book_data
            
        except Exception as e:
            self.logger.exception("Error formatting book", extra={"title": book.get('title', 'Unknown'), "error": str(e)})
            raise
    
    def _format_runtime(self, runtime_minutes: int) -> str:
        """Format runtime from minutes to 'X hrs Y mins' format"""
        if not isinstance(runtime_minutes, int) or runtime_minutes <= 0:
            return "Unknown Runtime"
        return format_runtime(runtime_minutes)

    def _join_names(self, people: List[Any], fallback: str) -> str:
        names = []
        for person in people or []:
            if isinstance(person, dict) and person.get("name"):
                names.append(person["name"])
            elif isinstance(person, str):
                names.append(person)
        return ", ".join(names) if names else fallback

    def _format_authors(self, authors: List[Dict]) -> str:
        return self._join_names(authors, "Unknown Author")

    def _format_narrators(self, narrators: List[Dict]) -> str:
        return self._join_names(narrators, "Unknown Narrator")

    def _format_series(self, series_info: List[Dict]) -> tuple[str, str, str]:
        """Series name, sequence and ASIN of the first listed series."""
        if not series_info or not isinstance(series_info, list) or not isinstance(series_info[0], dict):
            return "N/A", "N/A", None
        primary = series_info[0]
        return primary.get("title", "N/A"), str(primary.get("sequence", "N/A")), primary.get("asin")

    def _extract_series_asin_from_relationships(self, book: Dict) -> Optional[str]:
        """Extract series ASIN from relationships (relationships response group required)"""
        try:
            product = book.get('product', book)
            series_array = product.get('series', [])
            if series_array and len(series_array) > 0:
                series_asin = series_array[0].get('asin')
                if series_asin:
                    return series_asin
            
            relationships = product.get('relationships', [])
            if not relationships:
                return None
            
            for relationship in relationships:
                if (relationship.get('relationship_type') == 'series' and 
                    relationship.get('relationship_to_product') == 'parent'):
                    series_asin = relationship.get('asin')
                    if series_asin:
                        return series_asin
            
            return None
            
        except Exception as e:
            self.logger.exception("Error extracting series ASIN from relationships", extra={"error": str(e)})
            return None
    
    def _format_rating(self, rating_info: Dict) -> str:
        if not isinstance(rating_info, dict):
            return "N/A"
        overall = rating_info.get("overall_distribution")
        if isinstance(overall, dict) and overall.get("display_average_rating"):
            return str(overall["display_average_rating"])
        rating_value = rating_info.get("overall_rating") or rating_info.get("rating")
        return str(rating_value) if rating_value else "N/A"

    def _extract_num_ratings(self, rating_info: Dict) -> int:
        overall = rating_info.get("overall_distribution") if isinstance(rating_info, dict) else None
        if not isinstance(overall, dict):
            return 0
        try:
            return int(overall.get("num_ratings") or 0)
        except (TypeError, ValueError):
            return 0

    def _format_release_date(self, release_date: Any) -> str:
        if not release_date:
            return "Unknown"
        return str(release_date)[:10]

    def _format_language(self, language: Any) -> str:
        if not language:
            return "English"
        return language.capitalize() if isinstance(language, str) else str(language)

    def _format_summary(self, summary: Any) -> str:
        """Collapse whitespace and cap the summary at 2000 characters."""
        if not summary:
            return "No summary available."
        if not isinstance(summary, str):
            return str(summary)
        cleaned = re.sub(r'\s+', ' ', summary.strip())
        if len(cleaned) > 2000:
            cleaned = cleaned[:1997] + "..."
        return cleaned or "No summary available."

    def _format_contributors(self, book: Dict) -> List[Dict[str, Any]]:
        """Normalize contributor data from Audible responses."""
        source = book.get("contributors")
        if not source and isinstance(book.get("product"), dict):
            source = book["product"].get("contributors")
        if not isinstance(source, list):
            return []

        formatted: List[Dict[str, Any]] = []
        for contributor in source:
            if not isinstance(contributor, dict):
                continue
            name = contributor.get("name") or contributor.get("Name")
            role = contributor.get("role") or contributor.get("Role")
            if name and role:
                formatted.append({
                    "Name": name,
                    "Role": role,
                    "ASIN": contributor.get("asin") or contributor.get("ASIN"),
                })
        return formatted

    def format_book_for_display(self, book_data: Dict) -> Dict[str, Any]:
        """Format book data specifically for UI display"""
        display_data = book_data.copy()
        display_data["author_short"] = self._truncate_text(book_data.get("Author", ""), 50)
        display_data["title_short"] = self._truncate_text(book_data.get("Title", ""), 60)
        display_data["summary_short"] = self._truncate_text(book_data.get("Summary", ""), 200)
        display_data["runtime_minutes"] = runtime_to_minutes(book_data.get("Runtime"))
        display_data["rating_stars"] = self._format_rating_stars(book_data.get("Overall Rating", "N/A"))
        return display_data

    def _truncate_text(self, text: str, max_length: int) -> str:
        """Truncate text to specified length with ellipsis"""
        if not isinstance(text, str) or len(text) <= max_length:
            return text
        return text[:max_length - 3] + "..."

    def _format_rating_stars(self, rating: str) -> str:
        try:
            full_stars = max(0, min(5, int(float(rating) + 0.5)))
        except (TypeError, ValueError):
            full_stars = 0
        return "★" * full_stars + "☆" * (5 - full_stars)


formatter = AudibleFormatter()

