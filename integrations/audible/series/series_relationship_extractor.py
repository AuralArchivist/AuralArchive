"""
Module Name: series_relationship_extractor.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Extract series ASIN and metadata from Audible relationships response groups.

Location:
    /integrations/audible/series/series_relationship_extractor.py
"""

from core.logging import get_module_logger


class SeriesRelationshipExtractor:
    """Extracts series information from Audible API book relationships."""

    def __init__(self, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.Series.RelationshipExtractor")
    
    def extract_series_metadata(self, book_metadata):
        """Extract series metadata (title, sequence, etc.) from book relationships"""
        try:
            product = book_metadata.get('product', book_metadata)
            
            series_array = product.get('series', [])
            if series_array and len(series_array) > 0:
                first_series = series_array[0]
                series_data = {
                    'series_asin': first_series.get('asin'),
                    'series_title': first_series.get('title'),
                    'series_url': first_series.get('url'),
                    'sku': None,
                    'sequence': first_series.get('sequence')
                }
                self.logger.info(
                    "Extracted series metadata from series array",
                    extra={"series_asin": series_data.get('series_asin'), "series_title": series_data.get('series_title')},
                )
                return series_data
            
            relationships = product.get('relationships', [])
            
            for relationship in relationships:
                if (
                    relationship.get('relationship_type') == 'series'
                    and relationship.get('relationship_to_product') == 'parent'
                ):
                    series_data = {
                        'series_asin': relationship.get('asin'),
                        'series_title': relationship.get('title'),
                        'series_url': relationship.get('url'),
                        'sku': relationship.get('sku'),
                        'sequence': relationship.get('sequence'),
                    }

                    self.logger.info(
                        "Extracted series metadata from relationships",
                        extra={"series_asin": series_data.get('series_asin'), "series_title": series_data.get('series_title')},
                    )
                    return series_data

            self.logger.warning("No series information found")
            return None

        except Exception as e:
            self.logger.error("Error extracting series metadata", extra={"error": str(e)}, exc_info=True)
            return None
