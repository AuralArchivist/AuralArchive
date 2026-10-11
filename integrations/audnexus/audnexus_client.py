"""
Module Name: audnexus_client.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Access the Audnexus API for author and book metadata with structured logging and fallbacks.

Location:
    /integrations/audnexus/audnexus_client.py
"""

from typing import List, Dict, Optional
from datetime import datetime

from core.logging import get_module_logger
from core.http import HttpSession

class AudnexusClient:
    """
    Service for interacting with the Audnexus API
    Provides author search, author details, and book metadata
    """
    
    def __init__(self, logger=None):
        self.logger = logger or get_module_logger("Service.Audnexus.Service")
        self.base_url = "https://api.audnex.us"
        self.request_timeout = (5, 8)
        self.session = self._setup_session()
        self.logger.success("Audnexus service started successfully")
    
    def _setup_session(self) -> HttpSession:
        """Session with the headers Audnexus expects"""
        return HttpSession(headers={
            "User-Agent": "AuralArchive/1.0 (Python Requests)",
            "Accept": "application/json",
            "Content-Type": "application/json"
        })
    
    def search_authors(self, name: str, region: str = "us", num_results: int = 20) -> List[Dict]:
        """Search for authors by name"""
        try:
            self.logger.info("Searching authors", extra={"author_name": name, "region": region})
            
            params = {
                "name": name,
                "region": region
            }
            
            response = self.session.get(
                f"{self.base_url}/authors",
                params=params,
                timeout=self.request_timeout
            )
            
            if response.status_code == 200:
                authors = response.json()
                self.logger.info("Author search succeeded", extra={"author_name": name, "result_count": len(authors)})
                return authors[:num_results] if num_results else authors
            elif response.status_code == 400:
                self.logger.warning("Bad request for author search", extra={"author_name": name, "region": region})
                return []
            else:
                body_preview = response.text[:200] if response.text else ''
                self.logger.error(
                    "Author search failed",
                    extra={
                        'status_code': response.status_code,
                        'author_name': name,
                        'body': body_preview
                    }
                )
                return []
                
        except Exception as exc:
            self.logger.error(
                "Error searching authors",
                extra={"author_name": name, "exc": exc}, exc_info=True
            )
            return []
    
    def get_author_details(self, asin: str, region: str = "us", update: bool = False) -> Optional[Dict]:
        """Get detailed author information by ASIN"""
        try:
            self.logger.info("Getting author details", extra={"author_asin": asin, "region": region, "force_update": update})
            
            params = {
                "region": region,
                "update": "1" if update else "0"
            }
            
            response = self.session.get(
                f"{self.base_url}/authors/{asin}",
                params=params,
                timeout=self.request_timeout
            )
            
            if response.status_code == 200:
                author = response.json()
                self.logger.info("Author details retrieved", extra={"author_asin": asin, "author_name": author.get("name", asin)})
                return author
            elif response.status_code == 404:
                self.logger.warning("Author not found", extra={"author_asin": asin})
                return None
            else:
                body_preview = response.text[:200] if response.text else ''
                self.logger.error(
                    "Author details request failed",
                    extra={
                        'status_code': response.status_code,
                        'author_asin': asin,
                        'body': body_preview
                    }
                )
                return None
                
        except Exception as exc:
            self.logger.error(
                "Error getting author details",
                extra={"author_asin": asin, "exc": exc}, exc_info=True
            )
            return None
    
    def get_book_details(self, asin: str, region: str = "us", seed_authors: bool = True, update: bool = False) -> Optional[Dict]:
        """Get detailed book information by ASIN"""
        try:
            self.logger.info("Getting book details", extra={"book_asin": asin, "region": region, "seed_authors": seed_authors, "force_update": update})
            
            params = {
                "region": region,
                "seedAuthors": "1" if seed_authors else "0",
                "update": "1" if update else "0"
            }
            
            response = self.session.get(
                f"{self.base_url}/books/{asin}",
                params=params,
                timeout=self.request_timeout
            )
            
            if response.status_code == 200:
                book = response.json()
                self.logger.info("Book details retrieved", extra={"book_asin": asin, "title": book.get("title", asin)})
                return book
            elif response.status_code == 404:
                self.logger.warning("Book not found", extra={"book_asin": asin})
                return None
            else:
                body_preview = response.text[:200] if response.text else ''
                self.logger.error(
                    "Book details request failed",
                    extra={
                        'status_code': response.status_code,
                        'book_asin': asin,
                        'body': body_preview
                    }
                )
                return None
        except Exception as exc:
            self.logger.error(
                "Error getting book details",
                extra={"book_asin": asin, "exc": exc}, exc_info=True
            )
            return None
    
    def find_author_by_name(self, author_name: str, region: str = "us") -> Optional[Dict]:
        """Find the best matching author by name and return full details"""
        try:
            authors = self.search_authors(author_name, region)
            
            if not authors:
                return None
            
            best_match = None
            exact_match = None
            
            for author in authors:
                if author.get('name', '').lower() == author_name.lower():
                    exact_match = author
                    break
                elif author_name.lower() in author.get('name', '').lower():
                    if not best_match:
                        best_match = author
            
            target_author = exact_match or best_match or authors[0]
            
            if target_author and target_author.get('asin'):
                return self.get_author_details(target_author['asin'], region)
            
            return target_author
            
        except Exception as exc:
            self.logger.error(
                "Error finding author",
                extra={"author_name": author_name, "exc": exc}, exc_info=True
            )
            return None
    
    def format_author_for_compatibility(self, author_data: Dict) -> Dict:
        """Format Audnexus author data to match existing application expectations"""
        try:
            formatted = {
                'asin': author_data.get('asin', ''),
                'name': author_data.get('name', ''),
                'author_image': author_data.get('image', ''),
                'author_bio': author_data.get('description', ''),
                'author_page_url': f"https://www.audible.com/author/{author_data.get('name', '').replace(' ', '-')}/{author_data.get('asin', '')}",
                'genres': author_data.get('genres', []),
                'similar_authors': author_data.get('similar', []),
                'region': author_data.get('region', 'us'),
                'last_updated': datetime.now().isoformat()
            }
            
            return formatted
            
        except Exception as exc:
            self.logger.error(
                "Error formatting author data",
                extra={"exc": exc}, exc_info=True
            )
            return author_data
    
    def format_book_for_compatibility(self, book_data: Dict) -> Dict:
        """Format Audnexus book data to match existing application expectations"""
        try:
            authors = book_data.get('authors', [])
            author_name = authors[0].get('name', '') if authors else ''
            
            narrators = book_data.get('narrators', [])
            narrator_name = narrators[0].get('name', '') if narrators else ''
            
            runtime_min = book_data.get('runtimeLengthMin', 0)
            hours = runtime_min // 60
            minutes = runtime_min % 60
            runtime_str = f"{hours} hrs {minutes} mins" if hours > 0 else f"{minutes} mins"
            
            release_date = book_data.get('releaseDate') or ''
            release_year = str(release_date)[:4]
            
            formatted = {
                'ASIN': book_data.get('asin', ''),
                'Title': book_data.get('title', ''),
                'Author': author_name,
                'Authors': [author.get('name', '') for author in authors],
                'Narrator': narrator_name,
                'Narrators': [narrator.get('name', '') for narrator in narrators],
                'Publisher': book_data.get('publisherName', ''),
                'Release Date': release_year,
                'Runtime': runtime_str,
                'RuntimeMin': runtime_min,
                'Cover Image': book_data.get('image', ''),
                'Overall Rating': book_data.get('rating', ''),
                'Summary': book_data.get('summary', ''),
                'Description': book_data.get('description', ''),
                'ISBN': book_data.get('isbn', ''),
                'Language': book_data.get('language', ''),
                'Format': book_data.get('formatType', ''),
                'Genres': book_data.get('genres', []),
                'Copyright': book_data.get('copyright', ''),
                'IsAdult': book_data.get('isAdult', False),
                'LiteratureType': book_data.get('literatureType', ''),
                'Series': 'N/A',
                'Sequence': 'N/A',
                'Status': 'Available',
                'Region': book_data.get('region', 'us')
            }
            
            return formatted
            
        except Exception as exc:
            self.logger.error(
                "Error formatting book data",
                extra={"exc": exc}, exc_info=True
            )
            return book_data
    
    def test_connection(self) -> tuple[bool, str]:
        """Test connection to Audnexus API"""
        try:
            self.logger.info("Testing Audnexus API connection")
            
            response = self.session.get(
                f"{self.base_url}/authors",
                params={"name": "Andy Weir", "region": "us"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                self.logger.info(
                    "Audnexus API connection test successful",
                    extra={"author_count": len(data)}
                )
                return True, f"API accessible - found {len(data)} authors"
            else:
                self.logger.error(
                    "Audnexus API test failed",
                    extra={"status_code": response.status_code}
                )
                return False, f"API returned status {response.status_code}"
                
        except Exception as exc:
            self.logger.error(
                "Audnexus API connection test error",
                extra={"exc": exc}, exc_info=True
            )
            return False, f"Connection error: {exc}"
