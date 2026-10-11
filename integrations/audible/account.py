"""
Module Name: account.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    The signed-in Audible account: authentication, the API client, and the
    user's library.

Location:
    /integrations/audible/account.py
"""

import audible
import os
from typing import Any, Dict, List, Optional, Tuple
import threading

from core.logging import get_module_logger
from core.paths import resolve_audible_auth_file
from core.config.secrets_crypto import get_audible_auth_password

from .library.library_client import AudibleLibraryClient

class AudibleAccount:
    """The signed-in Audible account: authentication, API calls and the library client."""
    
    def __init__(self, auth_file: str = None, config_service=None, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.Manager")
        self.config_service = config_service
        self.auth_file = auth_file or resolve_audible_auth_file()
        self.auth = None
        self._lock = threading.Lock()
        
        self.library_client = AudibleLibraryClient(config_service, self.logger)
        
        self._load_existing_auth()
        self.logger.debug("AudibleAccount initialized", extra={"auth_file": self.auth_file})
        
    def _load_existing_auth(self) -> bool:
        """Try to load existing authentication from file."""
        try:
            if os.path.exists(self.auth_file):
                self.logger.debug("Loading existing Audible authentication", extra={"auth_file": self.auth_file})
                self.auth = audible.Authenticator.from_file(self.auth_file, password=get_audible_auth_password())

                if not self.auth.encryption:
                    try:
                        self.auth.to_file(self.auth_file, password=get_audible_auth_password(), encryption='json')
                        os.chmod(self.auth_file, 0o600)
                        self.logger.info("Migrated Audible auth file to encrypted storage", extra={"auth_file": self.auth_file})
                    except Exception as migrate_exc:
                        self.logger.warning("Failed to migrate Audible auth file to encrypted storage", extra={"error": str(migrate_exc)})

                return True
        except Exception as e:
            self.logger.warning("Failed to load existing auth", extra={"error": str(e), "auth_file": self.auth_file})
        return False
    
    def is_configured(self) -> bool:
        """Check if Audible is properly configured (has auth token)."""
        try:
            return os.path.exists(self.auth_file)
        except Exception as e:
            self.logger.error("Error checking Audible configuration", extra={"error": str(e), "auth_file": self.auth_file}, exc_info=True)
            return False
    
    def load_saved_auth(self) -> Tuple[bool, str]:
        """Load the saved sign-in token; signing in itself happens in the web UI.

        Returns:
            Tuple of (success, message)
        """
        with self._lock:
            try:
                if self.auth is None and os.path.exists(self.auth_file):
                    try:
                        self.logger.debug(
                            "Loading existing Audible authentication token",
                            extra={"auth_file": self.auth_file},
                        )
                        self.auth = audible.Authenticator.from_file(self.auth_file, password=get_audible_auth_password())
                        return True, "Successfully loaded existing authentication"
                    except Exception as e:
                        self.logger.warning(
                            "Failed to load existing auth token",
                            extra={"error": str(e), "auth_file": self.auth_file},
                        )
                        return False, "Failed to load authentication token"
                
                if self.auth:
                    return True, "Authentication already loaded"
                
                self.logger.info(
                    "No authentication token found - user must authenticate via web UI",
                    extra={"auth_file": self.auth_file},
                )
                return False, "No authentication token found. Please authenticate using the Settings > Audible page."
                
            except Exception as e:
                error_msg = f"Error checking authentication: {e}"
                self.logger.error("Error checking authentication", extra={"error": str(e)}, exc_info=True)
                return False, error_msg
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test the Audible connection."""
        try:
            if not self.auth:
                success, message = self.load_saved_auth()
                if not success:
                    return False, f"Authentication failed: {message}"
            
            with audible.Client(auth=self.auth) as client:
                client.get("1.0/account/information")
                
            return True, "Connection successful"
            
        except Exception as e:
            error_msg = f"Connection test failed: {e}"
            self.logger.error("Connection test failed", extra={"error": str(e)}, exc_info=True)
            return False, error_msg
    
    def get_client(self) -> Optional[audible.Client]:
        """Get an authenticated Audible client."""
        try:
            if not self.auth:
                success, message = self.load_saved_auth()
                if not success:
                    self.logger.error("Cannot create Audible client", extra={"message": message})
                    return None
            
            return audible.Client(auth=self.auth)
            
        except Exception as e:
            self.logger.error("Error creating Audible client", extra={"error": str(e)}, exc_info=True)
            return None
    
    def make_api_call(self, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Make an API call to Audible with error handling."""
        try:
            client = self.get_client()
            if client is None:
                return None

            with client:
                self.logger.debug("Making API call", extra={"endpoint": endpoint, "kwargs": kwargs})
                response = client.get(endpoint, **kwargs)
                return response
                
        except Exception as e:
            self.logger.error("API call failed", extra={"endpoint": endpoint, "error": str(e)}, exc_info=True)
            return None
    
    def try_multiple_endpoints(self, endpoints: List[str], **kwargs) -> Optional[Dict[str, Any]]:
        """Try multiple API endpoints until one succeeds."""
        for endpoint in endpoints:
            try:
                self.logger.debug("Trying endpoint", extra={"endpoint": endpoint})
                response = self.make_api_call(endpoint, **kwargs)
                
                if response and isinstance(response, dict):
                    has_data = any(key in response for key in ['products', 'items', 'books', 'collections', 'lists'])
                    if has_data:
                        self.logger.debug("Successful response from endpoint", extra={"endpoint": endpoint})
                        return response
                    
            except Exception as e:
                self.logger.debug("Endpoint failed", extra={"endpoint": endpoint, "error": str(e)})
                continue
        
        self.logger.warning("All endpoints failed", extra={"endpoints": endpoints})
        return None
    
    def format_book_data(self, book: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format book data for consistent use across the application.
        Centralized formatting logic used by both recommendations and wishlist services.
        """
        authors = []
        
        authors_data = book.get("authors") or []
        if authors_data and isinstance(authors_data, list):
            authors = [author.get("name", "Unknown") for author in authors_data 
                      if author and isinstance(author, dict)]
        
        if not authors:
            contributors_data = book.get("contributors") or []
            if contributors_data and isinstance(contributors_data, list):
                author_contributors = [c for c in contributors_data 
                                     if c and isinstance(c, dict) and c.get("role") == "Author"]
                if author_contributors:
                    authors = [contrib.get("name", "Unknown") for contrib in author_contributors]
                else:
                    authors = [contrib.get("name", "Unknown") for contrib in contributors_data
                             if contrib and isinstance(contrib, dict)]
        
        if not authors:
            authors = ["Unknown Author"]
        
        narrators = []
        contributors_data = book.get("contributors") or []
        if contributors_data and isinstance(contributors_data, list):
            narrator_contributors = [c for c in contributors_data 
                                   if c and isinstance(c, dict) and c.get("role") == "Narrator"]
            narrators = [contrib.get("name", "Unknown") for contrib in narrator_contributors]
        
        if not narrators:
            narrators_data = book.get("narrators") or []
            if narrators_data and isinstance(narrators_data, list):
                narrators = [narrator.get("name", "Unknown") for narrator in narrators_data 
                           if narrator and isinstance(narrator, dict)]
        
        if not narrators:
            narrators = ["Unknown Narrator"]
        
        rating_info = book.get("rating") or {}
        runtime_min = book.get("runtime_length_min")
        
        runtime_display = "Unknown"
        if runtime_min is not None and runtime_min > 0:
            hours = runtime_min // 60
            minutes = runtime_min % 60
            if hours > 0:
                runtime_display = f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
            else:
                runtime_display = f"{minutes}m"
        
        series_info = book.get("series", [])
        series_name = "N/A"
        series_sequence = ""
        
        if series_info and isinstance(series_info, list) and len(series_info) > 0:
            first_series = series_info[0]
            if isinstance(first_series, dict):
                series_name = first_series.get("title", "N/A")
                series_sequence = str(first_series.get("sequence", ""))
        
        language_map = {
            "en": "English",
            "es": "Spanish", 
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "pt": "Portuguese",
            "ja": "Japanese",
            "zh": "Chinese"
        }
        language_code = book.get("language", "en")
        language_full = language_map.get(language_code, "English")
        
        full_summary = book.get("publisher_summary", "") or "No summary available"
        
        return {
            'asin': book.get("asin", ""),
            'title': book.get("title", "Unknown Title"),
            'authors': authors,
            'author': ", ".join(authors),
            'narrators': narrators,
            'narrator': ", ".join(narrators),
            'rating': rating_info.get("displayed_average_rating", "") or rating_info.get("overall_distribution", {}).get("display_average_rating", ""),
            'num_ratings': rating_info.get("overall_distribution", {}).get("num_ratings", 0),
            'runtime_minutes': runtime_min,
            'runtime': runtime_display,
            'release_date': book.get("release_date", ""),
            'publisher': book.get("publisher_name", ""),
            'summary': full_summary,
            'cover_image': self._extract_cover_image(book),
            'price_display': self._extract_price(book),
            'categories': book.get("category_ladders", []),
            'language': language_full,
            'series': series_name,
            'sequence': series_sequence,
            
            'ASIN': book.get("asin", ""),
            'Title': book.get("title", "Unknown Title"),
            'Author': ", ".join(authors),
            'Narrator': ", ".join(narrators),
            'Overall Rating': rating_info.get("displayed_average_rating", "") or rating_info.get("overall_distribution", {}).get("display_average_rating", ""),
            'Runtime': runtime_display,
            'Release Date': book.get("release_date", ""),
            'Publisher': book.get("publisher_name", ""),
            'Summary': full_summary,
            'Cover Image': self._extract_cover_image(book),
            'Language': language_full,
            'Series': series_name,
            'Sequence': series_sequence,
            'Num_Ratings': rating_info.get("overall_distribution", {}).get("num_ratings", 0)
        }
    
    def _extract_cover_image(self, book: Dict[str, Any]) -> str:
        """Cover URL, preferring 500px, else any size offered."""
        product_images = book.get("product_images") or {}
        for size in ["500", "300", "150", "90"]:
            if size in product_images:
                return product_images[size]
        return next(iter(product_images.values()), "")

    def _extract_price(self, book: Dict[str, Any]) -> str:
        return ((book.get("price") or {}).get("list_price") or {}).get("display_price", "")

    def get_service_status(self) -> Dict[str, Any]:
        """Get current manager status information."""
        try:
            is_configured = self.is_configured()
            connection_ok = False
            
            if is_configured:
                connection_ok, _ = self.test_connection()
            
            library_status = self.library_client.get_service_status()
            
            return {
                'configured': is_configured,
                'connected': connection_ok,
                'auth_file_exists': os.path.exists(self.auth_file),
                'auth_loaded': self.auth is not None,
                'services': {
                    'library_client': library_status
                }
            }
            
        except Exception as e:
            self.logger.error("Error getting manager status", extra={"error": str(e)}, exc_info=True)
            return {
                'configured': False,
                'connected': False,
                'error': str(e),
                'services': {}
            }
    
    def export_library(self, output_format='json', force_refresh=False) -> Dict[str, Any]:
        """Export user's Audible library via library service."""
        return self.library_client.export_library(output_format, force_refresh)
    
    def get_library_stats(self) -> Dict[str, Any]:
        """Get library statistics via library service."""
        return self.library_client.get_library_stats()
    
    def search_library(self, query: str, search_fields: List[str] = None) -> Dict[str, Any]:
        """Search within library via library service."""
        return self.library_client.search_library(query, search_fields)
    
    def refresh_library_cache(self) -> Dict[str, Any]:
        """Refresh library cache via library service."""
        return self.library_client.refresh_library_cache()
    
    def test_audible_cli_availability(self) -> Dict[str, Any]:
        """Test Python audible package availability via library service."""
        return self.library_client.test_audible_cli_availability()
    
    def check_authentication_status(self) -> Dict[str, Any]:
        """Check authentication status via library service."""
        return self.library_client.check_authentication_status()
