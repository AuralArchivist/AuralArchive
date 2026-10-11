"""
Module Name: categories.py
Author: AuralArchivist
Created: July 7, 2026
Last Modified: October 9, 2026
Description:
    Recursively crawls Audible's public category tree, caches it in the
    audible_categories table, and queries live books for a given category.

Location:
    /services/metadata/categories.py
"""

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from core.language_matching import language_allowed
from core.logging import get_module_logger
from integrations.audible.catalog.catalog_search import audible_session
from integrations.audible.catalog.error_handling import error_handler
from services.settings.languages import get_preferred_languages

VALID_PRODUCTS_SORT_BY = {
    "-ReleaseDate", "ReleaseDate", "Heuristic", "-Heuristic", "ContentLevel",
    "-ContentLevel", "Title", "-Title", "AmazonEnglish", "AvgRating",
    "BestSellers", "RuntimeLength", "-RuntimeLength", "ProductSiteLaunchDate",
    "Relevance",
}
DEFAULT_SORT_BY = "-ReleaseDate"

PRODUCTS_RESPONSE_GROUPS = (
    "contributors,product_attrs,product_desc,product_extended_attrs,"
    "series,rating,media,relationships,customer_rights"
)


class AudibleCategorySyncService:
    """Syncs the Audible category tree and fetches books for a category."""

    CATEGORIES_URL = "https://api.audible.com/1.0/catalog/categories"
    PRODUCTS_URL = "https://api.audible.com/1.0/catalog/products"

    def __init__(self, db_service, *, logger=None) -> None:
        self.db_service = db_service
        self.logger = logger or get_module_logger("Service.Audible.CategorySync")
        self.session = audible_session()

    def sync_categories(self) -> Dict[str, Any]:
        """Crawl the full category tree and upsert it into audible_categories."""
        self.logger.info("Starting Audible category synchronization")

        seen_audible_ids: List[str] = []
        errors: List[str] = []
        imported = 0

        try:
            root = self._get_root_categories()
        except Exception as exc:
            self.logger.error("Unable to retrieve Audible categories", extra={"error": str(exc)}, exc_info=True)
            return {"success": False, "imported": 0, "errors": [str(exc)]}

        for top in root.get("categories", []):
            top_id = top.get("id")
            top_name = top.get("name")
            if not top_id or not top_name:
                continue

            top_db_id = self.db_service.categories.upsert_category(top_id, top_name, None, 1, top_name)
            seen_audible_ids.append(top_id)
            imported += 1

            for child in top.get("children", []) or []:
                child_id = child.get("id")
                child_name = child.get("name")
                if not child_id or not child_name:
                    continue

                child_path = f"{top_name} > {child_name}"
                child_db_id = self.db_service.categories.upsert_category(child_id, child_name, top_db_id, 2, child_path)
                seen_audible_ids.append(child_id)
                imported += 1

                crawled, crawl_result = self._crawl_children(child_id, child_db_id, child_path, level=3)
                imported += crawled
                seen_audible_ids.extend(crawl_result.pop("seen", []))
                errors.extend(crawl_result.get("errors", []))

        removed = self.db_service.categories.mark_missing_as_inactive(seen_audible_ids)

        self.logger.info(
            "Imported Audible categories",
            extra={"count": imported, "removed_or_stale": removed, "errors": len(errors)},
        )

        return {"success": True, "imported": imported, "removed_or_stale": removed, "errors": errors}

    def _crawl_children(self, audible_category_id: str, parent_db_id: Optional[int], parent_path: str, level: int):
        """Recursively fetch and upsert deeper levels of one category branch."""
        imported = 0
        seen: List[str] = []
        errors: List[str] = []

        try:
            detail = self._get_category_detail(audible_category_id)
        except Exception as exc:
            self.logger.error(
                "Failed to fetch category subtree",
                extra={"audible_category_id": audible_category_id, "error": str(exc)}, exc_info=True,
            )
            return 0, {"seen": [], "errors": [f"{audible_category_id}: {exc}"]}

        children = (detail.get("category") or {}).get("children") or []
        for child in children:
            child_id = child.get("id")
            child_name = child.get("name")
            if not child_id or not child_name:
                continue

            child_path = f"{parent_path} > {child_name}"
            child_db_id = self.db_service.categories.upsert_category(child_id, child_name, parent_db_id, level, child_path)
            seen.append(child_id)
            imported += 1

            deeper_imported, deeper_result = self._crawl_children(child_id, child_db_id, child_path, level=level + 1)
            imported += deeper_imported
            seen.extend(deeper_result.pop("seen", []))
            errors.extend(deeper_result.get("errors", []))

        return imported, {"seen": seen, "errors": errors}

    def _get_root_categories(self) -> Dict[str, Any]:
        response = self.session.get(self.CATEGORIES_URL, timeout=30)
        response.raise_for_status()
        return response.json()

    def _get_category_detail(self, audible_category_id: str) -> Dict[str, Any]:
        safe_id = quote(str(audible_category_id), safe='')
        response = self.session.get(f"{self.CATEGORIES_URL}/{safe_id}", timeout=30)
        response.raise_for_status()
        return response.json()

    def _get_country_code(self) -> str:
        """User's selected Audible marketplace (persisted from the Profile login form), default 'us'."""
        try:
            from services.container import container
            config_service = container.get_config_service()
            return (config_service.get_config_value('audible', 'country_code', 'us') or 'us').strip().lower()
        except Exception as exc:
            self.logger.debug("Unable to load country_code preference; defaulting to 'us'", extra={"error": str(exc)})
            return 'us'

    def _passes_country_filter(self, product: Dict[str, Any], country_code: str) -> bool:
        """Check the product's distribution_rights_region list for the user's marketplace.

        Products missing the field pass through rather than being excluded -
        the field isn't guaranteed on every response_groups combination, and
        failing open avoids silently emptying results over missing metadata.
        """
        regions = product.get("distribution_rights_region")
        if not regions:
            return True
        return country_code.upper() in regions

    def get_books_by_category(
        self,
        audible_category_id: str,
        sort_by: str = DEFAULT_SORT_BY,
        num_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """Live-query Audible for books in a category (not cached, per spec)."""
        sort_value = sort_by if sort_by in VALID_PRODUCTS_SORT_BY else DEFAULT_SORT_BY
        country_code = self._get_country_code()
        params = {
            "category_id": audible_category_id,
            "response_groups": PRODUCTS_RESPONSE_GROUPS,
            "products_sort_by": sort_value,
            "num_results": max(1, min(num_results, 50)),
            "marketplace": country_code,
        }

        self.logger.info(
            "Querying Audible catalog for category",
            extra={"audible_category_id": audible_category_id, "sort_by": sort_value, "country_code": country_code},
        )

        response = self.session.get(self.PRODUCTS_URL, params=params, timeout=30)
        if not error_handler.validate_response(response, f"Books for category {audible_category_id}"):
            return []

        error_handler.handle_api_quota(response)

        products = response.json().get("products", [])

        preferred_languages = get_preferred_languages()
        filtered_products = [
            product for product in products
            if self._passes_country_filter(product, country_code)
            and language_allowed(product.get("language"), preferred_languages)
        ]

        if len(filtered_products) != len(products):
            self.logger.info(
                "Filtered category results by language/country preference",
                extra={
                    "audible_category_id": audible_category_id,
                    "country_code": country_code,
                    "before": len(products),
                    "after": len(filtered_products),
                },
            )

        from services.container import container
        formatter = container.get_audible_account()
        books = [formatter.format_book_data(product) for product in filtered_products]

        for book in books:
            asin = book.get("asin") or book.get("ASIN")
            local_book = self.db_service.books.get_book_by_asin(asin) if asin else None
            if local_book:
                book["library_status"] = local_book.get("library_status")

        return books
