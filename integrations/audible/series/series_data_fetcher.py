"""
Module Name: series_data_fetcher.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Fetch series metadata and books from the Audible catalog, with series
    members fetched in batches.

Location:
    /integrations/audible/series/series_data_fetcher.py
"""

from core.logging import get_module_logger


class SeriesDataFetcher:
    """Fetches series data from the Audible catalog (signed-in or public client)."""

    SERIES_GROUPS = "product_desc,product_extended_attrs,media,relationships,customer_rights"
    BOOK_GROUPS = ("product_desc,product_attrs,product_extended_attrs,contributors,media,"
                   "rating,series,relationships,customer_rights")
    BATCH_SIZE = 50

    def __init__(self, audible_client, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.Series.DataFetcher")
        self.client = audible_client

    def get_series_product(self, series_asin):
        """One catalog call for the series product (metadata and child list)."""
        try:
            response = self.client.get(f"1.0/catalog/products/{series_asin}",
                                       params={"response_groups": self.SERIES_GROUPS})
        except Exception as e:
            self.logger.error("Error fetching series", extra={"series_asin": series_asin, "error": str(e)})
            return None
        if not response:
            self.logger.warning("No response for series ASIN", extra={"series_asin": series_asin})
            return None
        return response.get('product', response)

    def fetch_series_metadata(self, series_asin, product=None):
        """Series metadata; pass the product from get_series_product to avoid a second call."""
        product = product if product is not None else self.get_series_product(series_asin)
        if not product:
            return None
        series_data = {
            'series_asin': series_asin,
            'series_title': product.get('title'),
            'series_url': product.get('url'),
            'sku': product.get('sku'),
            'description': product.get('publisher_summary', ''),
            'cover_url': self._extract_cover_url(product),
            'total_books': self._extract_total_books(product)
        }
        self.logger.info(
            "Fetched series metadata",
            extra={"series_asin": series_asin, "series_title": series_data.get('series_title')},
        )
        return series_data

    def fetch_series_books(self, series_asin, product=None):
        """All books in a series, fetched in batches of BATCH_SIZE."""
        product = product if product is not None else self.get_series_product(series_asin)
        if not product:
            return []

        children = [
            {
                'asin': r.get('asin'),
                'sequence': r.get('sequence', ''),
                'sort_order': int(r.get('sort', 0) or 0),
            }
            for r in product.get('relationships') or []
            if r.get('relationship_type') == 'series' and r.get('relationship_to_product') == 'child' and r.get('asin')
        ]
        products = self.fetch_products([c['asin'] for c in children])

        books = []
        for child in children:
            found = products.get(child['asin'])
            metadata = self._product_metadata(found, child['asin']) if found else None
            if metadata is None:
                self.logger.warning("Could not fetch metadata for book; using minimal data",
                                    extra={"book_asin": child['asin']})
                metadata = {'asin': child['asin'], 'title': 'Unknown'}
            metadata['sequence'] = child['sequence']
            metadata['sort_order'] = child['sort_order']
            books.append(metadata)

        self.logger.info("Fetched metadata for series books",
                         extra={"series_asin": series_asin, "count": len(books)})
        return books

    def fetch_products(self, asins, response_groups=None):
        """Catalog products for many ASINs, BATCH_SIZE per call. Returns {asin: product}."""
        found = {}
        asins = [a for a in dict.fromkeys(asins) if a]
        for i in range(0, len(asins), self.BATCH_SIZE):
            chunk = asins[i:i + self.BATCH_SIZE]
            try:
                response = self.client.get("1.0/catalog/products", params={
                    "asins": ",".join(chunk),
                    "response_groups": response_groups or self.BOOK_GROUPS,
                    "image_sizes": "500",
                })
            except Exception as e:
                self.logger.error("Error fetching catalog products",
                                  extra={"count": len(chunk), "error": str(e)})
                continue
            for item in (response or {}).get('products') or []:
                if item.get('asin'):
                    found[item['asin']] = item
        return found

    def fetch_book_metadata(self, book_asin):
        """Complete metadata for a single book."""
        try:
            response = self.client.get(f"1.0/catalog/products/{book_asin}",
                                       params={"response_groups": self.BOOK_GROUPS, "image_sizes": "500"})
        except Exception as e:
            self.logger.error("Error fetching book metadata", extra={"book_asin": book_asin, "error": str(e)})
            return None
        if not response or 'product' not in response:
            self.logger.warning("No product in response for book", extra={"book_asin": book_asin})
            return None
        return self._product_metadata(response['product'], book_asin)

    def _product_metadata(self, product, book_asin):
        customer_rights = product.get('customer_rights') or {}
        return {
            'asin': book_asin,
            'title': product.get('title', 'Unknown'),
            'author': self._extract_authors(product),
            'narrator': self._extract_narrators(product),
            'publisher': product.get('publisher_name', 'Unknown'),
            'release_date': product.get('release_date', ''),
            'runtime': self._extract_runtime(product),
            'rating': self._extract_rating(product),
            'num_ratings': self._extract_num_ratings(product),
            'summary': product.get('publisher_summary', 'No summary available'),
            'cover_image': self._extract_cover_url(product),
            'language': product.get('language', 'en'),
            'customer_rights': customer_rights,
            'is_buyable': product.get('is_buyable', customer_rights.get('is_buyable')),
            'product_state': product.get('product_state', customer_rights.get('product_state')),
            'product': {
                'series': product.get('series', []),
                'relationships': product.get('relationships', []),
                'title': product.get('title'),
                'asin': product.get('asin')
            }
        }

    def _names(self, product, key, fallback):
        names = [c['name'] for c in product.get(key) or [] if isinstance(c, dict) and c.get('name')]
        return ', '.join(names) if names else fallback

    def _extract_authors(self, product):
        return self._names(product, 'authors', 'Unknown Author')

    def _extract_narrators(self, product):
        return self._names(product, 'narrators', 'Unknown Narrator')

    def _extract_runtime(self, product):
        return product.get('runtime_length_min') or 0

    def _rating_distribution(self, product):
        return (product.get('rating') or {}).get('overall_distribution') or {}

    def _extract_rating(self, product):
        return self._rating_distribution(product).get('display_average_rating', '')

    def _extract_num_ratings(self, product):
        return self._rating_distribution(product).get('num_ratings', 0)

    def _extract_cover_url(self, product):
        """Largest available cover image URL."""
        images = product.get('product_images') or {}
        for size in ['2400', '1215', '500', '252']:
            if size in images:
                return images[size]
        return None

    def _extract_total_books(self, product):
        """Number of child books listed in the series relationships."""
        child_count = sum(
            1 for r in product.get('relationships') or []
            if r.get('relationship_type') == 'series' and r.get('relationship_to_product') == 'child'
        )
        return child_count or None
