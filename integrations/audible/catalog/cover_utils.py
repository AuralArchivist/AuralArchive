"""
Module Name: cover_utils.py
Author: AuralArchivist
Created: August 17, 2025
Last Modified: October 9, 2026
Description:
    Extract and generate cover image URLs for Audible catalog items with fallbacks.

Location:
    /integrations/audible/catalog/cover_utils.py
"""

from typing import List, Optional, Dict, Any

from core.logging import get_module_logger


class CoverImageUtils:
    """Utilities for extracting and managing book cover images"""
    
    def __init__(self):
        self.logger = get_module_logger("Service.Audible.CatalogCover")
    
    def extract_cover_image(self, book_data: Dict[str, Any], asin: str) -> str:
        """Extract cover image from Audible API book data with multiple fallback strategies"""
        try:
            cover_url = self._extract_from_product_images(book_data)
            if cover_url:
                self.logger.debug("Found cover image from product_images", extra={"asin": asin})
                return cover_url
            
            cover_url = self._extract_from_alternative_fields(book_data)
            if cover_url:
                self.logger.debug("Found cover image from alternative field", extra={"asin": asin})
                return cover_url
            
            if asin:
                cover_url = self._generate_asin_based_url(asin)
                self.logger.debug("Using generated cover URL", extra={"asin": asin})
                return cover_url
            
            self.logger.warning("No cover image found, using placeholder", extra={"asin": asin})
            return self._get_placeholder_url()
            
        except Exception as e:
            self.logger.exception("Error extracting cover image", extra={"asin": asin, "error": str(e)})
            return self._get_placeholder_url()
    
    def _extract_from_product_images(self, book_data: Dict[str, Any]) -> Optional[str]:
        """Extract cover from product_images field"""
        product_images = book_data.get("product_images")
        if not isinstance(product_images, dict):
            return None
        
        size_priorities = ["500", "large", "300", "medium", "small"]
        
        for size_key in size_priorities:
            if size_key in product_images:
                image_url = product_images[size_key]
                if self._is_valid_image_url(image_url):
                    self.logger.debug(f"Found cover from product_images[{size_key}]")
                    return image_url
        
        for key, value in product_images.items():
            if self._is_valid_image_url(value):
                self.logger.debug(f"Found cover from product_images[{key}]")
                return value
        
        return None
    
    def _extract_from_alternative_fields(self, book_data: Dict[str, Any]) -> Optional[str]:
        """Try alternative image fields in the API response"""
        alternative_fields = [
            "image",
            "cover_url", 
            "thumbnail",
            "cover_image",
            "image_url",
            "artwork_url"
        ]
        
        for field in alternative_fields:
            if field in book_data:
                image_url = book_data[field]
                if self._is_valid_image_url(image_url):
                    self.logger.debug(f"Found cover from field '{field}'")
                    return image_url
        
        return None
    
    def _is_valid_image_url(self, url: Any) -> bool:
        """Check if URL is a valid image URL"""
        if not isinstance(url, str):
            return False
        
        if not url.strip():
            return False
        
        if not url.startswith(('http://', 'https://')):
            return False
        
        url_lower = url.lower()
        if any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
            return True
        
        if 'images-amazon.com' in url_lower or 'ssl-images-amazon.com' in url_lower:
            return True
        
        return False
    
    def _generate_asin_based_url(self, asin: str) -> str:
        """Generate cover URL from ASIN using Amazon's image service"""
        if not asin:
            return self._get_placeholder_url()
        
        primary_url = f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01.500x500.jpg"
        return primary_url
    
    def generate_cover_urls_fallback(self, asin: str) -> List[str]:
        """Generate multiple cover image URL patterns for better success rate"""
        if not asin:
            return [self._get_placeholder_url()]
        
        patterns = [
            f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01.500x500.jpg",
            f"https://m.media-amazon.com/images/P/{asin}.01.L.jpg", 
            f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01.LZZZZZZZ.jpg",
            f"https://images.amazon.com/images/P/{asin}.01.L.jpg",
            f"https://covers.audible.com/im/I/{asin}.jpg",
            f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01._SCLZZZZZZZ_.jpg"
        ]
        
        self.logger.debug("Generated fallback cover URLs", extra={"asin": asin, "count": len(patterns)})
        return patterns
    
    def _get_placeholder_url(self) -> str:
        """Get placeholder image URL"""
        return "/static/images/no-cover.svg"
    
    def get_cover_info(self, book_data: Dict[str, Any], asin: str) -> Dict[str, Any]:
        """Get cover image information"""
        cover_url = self.extract_cover_image(book_data, asin)
        fallback_urls = self.generate_cover_urls_fallback(asin) if asin else []
        
        info = {
            'primary_url': cover_url,
            'fallback_urls': fallback_urls,
            'is_placeholder': cover_url == self._get_placeholder_url(),
            'source': self._determine_source(cover_url),
            'asin': asin
        }
        
        return info
    
    def _determine_source(self, url: str) -> str:
        """Determine the source of the cover image"""
        if not url:
            return 'none'
        
        if 'placeholder' in url:
            return 'placeholder'
        elif 'images-amazon.com' in url or 'ssl-images-amazon.com' in url:
            return 'amazon'
        elif 'covers.audible.com' in url:
            return 'audible'
        elif 'media-amazon.com' in url:
            return 'amazon_media'
        else:
            return 'external'

cover_utils = CoverImageUtils()
