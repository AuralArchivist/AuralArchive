"""
Module Name: catalog_search.py
Author: AuralArchivist
Created: August 16, 2025
Last Modified: October 9, 2026
Description:
    Perform Audible catalog queries with retry/error handling and shared sessions.

Location:
    /integrations/audible/catalog/catalog_search.py
"""

from typing import Any, Dict, List, Optional

import requests

from core.logging import get_module_logger
from core.http import BROWSER_USER_AGENT, HttpSession, RequestPacer
from .error_handling import error_handler


CATALOG_PACER = RequestPacer(min_interval=0.5)


def audible_session(*, paced: bool = True) -> HttpSession:
    """Session the Audible catalog API accepts. Paced sessions share one pacer
    and retry network errors (3 attempts)."""
    headers = {"User-Agent": BROWSER_USER_AGENT}
    if not paced:
        return HttpSession(headers=headers)
    return HttpSession(headers=headers, pacer=CATALOG_PACER, attempts=3, retry_delay=1.0)


class PublicCatalogClient:
    """Unauthenticated stand-in for audible.Client.get, limited to catalog paths.

    Catalog data (books, series, their relationships) doesn't depend on an
    account, so series lookups work without an Audible sign-in.
    """

    BASE_URL = "https://api.audible.com/"

    def __init__(self, region: str = "us") -> None:
        self.logger = get_module_logger("Service.Audible.PublicCatalog")
        self.region = region
        self.session = audible_session(paced=False)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        if not path.startswith("1.0/catalog/"):
            raise ValueError(f"Not a catalog path: {path}")
        query: Dict[str, Any] = {"marketplace": self.region}
        query.update(params or {})
        query.update(kwargs)
        response = self.session.get(self.BASE_URL + path, params=query, timeout=30)
        error_handler.handle_api_quota(response)
        response.raise_for_status()
        return response.json()


class AudibleSearch:
    """Handles Audible API search operations and requests."""

    def __init__(self) -> None:
        self.logger = get_module_logger("Service.Audible.CatalogSearch")
        self.base_url = "https://api.audible.com/1.0/catalog/products"
        self.session = audible_session()


    def search_books(self, query: str, region: str = "us", num_results: int = 25) -> List[Dict[str, Any]]:
        """Search for books on Audible by keyword."""

        self.logger.info("Searching Audible", extra={"query": query, "region": region, "requested": num_results})

        try:
            params = self._build_search_params(query=query, num_results=num_results, region=region)
            error_handler.log_request_info(self.base_url, params, f"Search books: {query}")

            response = self.session.get(self.base_url, params=params, timeout=30)
            if not error_handler.validate_response(response, f"Search books: {query}"):
                return []

            error_handler.handle_api_quota(response)

            api_data = response.json()
            products = api_data.get("products", [])

            self.logger.info("Search results received", extra={"query": query, "region": region, "count": len(products)})
            return products

        except requests.exceptions.RequestException as exc:
            self.logger.error("Request error while searching", extra={"query": query, "region": region, "error": str(exc)})
            raise
        except Exception as exc:
            self.logger.error("Unexpected error while searching", extra={"query": query, "region": region, "error": str(exc)})
            raise

    def _build_search_params(self, query: str, num_results: int, region: str) -> Dict[str, str]:
        """Build query parameters for keyword search requests."""

        return {
            "response_groups": "contributors,product_attrs,product_desc,product_extended_attrs,series,rating,media,relationships,customer_rights,category_ladders",
            "num_results": str(num_results),
            "products_sort_by": "Relevance",
            "keywords": query,
            "marketplace": region,
        }

    def search_by_author(
        self,
        author: str,
        region: str = "us",
        num_results: int = 25,
        response_groups: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search for books using Audible's author filter."""

        self.logger.info("Searching Audible for author", extra={"author": author, "region": region, "requested": num_results})

        try:
            effective_results = max(1, min(num_results, 50))
            params: Dict[str, str] = {
                "response_groups": response_groups
                or "contributors,product_attrs,product_desc,product_extended_attrs,series,rating,media,relationships,customer_rights",
                "num_results": str(effective_results),
                "products_sort_by": "Relevance",
                "author": author,
                "marketplace": region,
            }

            error_handler.log_request_info(self.base_url, params, f"Search by author: {author}")

            response = self.session.get(self.base_url, params=params, timeout=30)
            if not error_handler.validate_response(response, f"Search by author: {author}"):
                return []

            error_handler.handle_api_quota(response)

            api_data = response.json()
            products = api_data.get("products", [])
            self.logger.info("Author search results received", extra={"author": author, "region": region, "count": len(products)})
            return products

        except requests.exceptions.RequestException as exc:
            self.logger.error("Request error while searching author", extra={"author": author, "region": region, "error": str(exc)})
            raise
        except Exception as exc:
            self.logger.error("Unexpected error while searching author", extra={"author": author, "region": region, "error": str(exc)})
            raise

    def get_api_status(self) -> Dict[str, Any]:
        """Check whether the Audible API endpoint is reachable."""

        status: Dict[str, Any] = {
            "accessible": False,
            "base_url": self.base_url,
        }

        try:
            test_params = {
                "response_groups": "product_attrs",
                "num_results": "1",
                "keywords": "status",
                "marketplace": "us",
            }

            response = self.session.get(self.base_url, params=test_params, timeout=10)
            status["status_code"] = response.status_code

            if error_handler.validate_response(response, "API status check"):
                status["accessible"] = True

                remaining = error_handler.handle_api_quota(response)
                if remaining is not None:
                    status["quota_remaining"] = remaining

                try:
                    data = response.json()
                    status["api_version"] = data.get("version", "unknown")
                    status["products_available"] = len(data.get("products", []))
                except ValueError:
                    self.logger.debug("Audible status response was not JSON")

            return status

        except requests.exceptions.RequestException as exc:
            self.logger.error("Request error during API status check", extra={"error": str(exc)})
            status["error"] = str(exc)
            return status
        except Exception as exc:
            self.logger.error("Unexpected error during API status check", extra={"error": str(exc)}, exc_info=True)
            status["error"] = str(exc)
            return status
