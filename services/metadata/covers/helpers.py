"""
Module Name: helpers.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Convenience helpers for image caching and URL resolution across author
    images and book covers.

Location:
    /services/metadata/covers/helpers.py
"""

from typing import Optional, Dict, Any
from core.logging import get_module_logger
from services.container import container

_LOGGER = get_module_logger("Service.ImageCache.Helpers")

def cache_image(image_url: str, *, logger=None) -> Optional[str]:
    """Cache any image (author image or book cover) and return the local URL."""
    if not image_url:
        return None
        
    try:
        image_cache = container.get_image_cache_service()
        cached_url = image_cache.get_cached_image_url(image_url)
        
        if cached_url:
            (logger or _LOGGER).debug(f"Image cached successfully: {image_url} -> {cached_url}")
            return cached_url
        else:
            (logger or _LOGGER).warning(f"Failed to cache image: {image_url}")
            if image_url.startswith('/metadata/items/'):
                (logger or _LOGGER).warning(f"Invalid metadata URL, returning None: {image_url}")
                return None
            return image_url
            
    except Exception as e:
        (logger or _LOGGER).error(f"Error caching image {image_url}: {e}")
        if image_url.startswith('/metadata/items/'):
            (logger or _LOGGER).warning(f"Invalid metadata URL, returning None: {image_url}")
            return None
        return image_url

def cache_book_cover(cover_image_url: str) -> Optional[str]:
    """
    Cache a book cover image and return the local URL.
    """
    return cache_image(cover_image_url)

def get_cached_author_image_url(author_data: Dict[str, Any]) -> Optional[str]:
    """Get cached author image URL from author data, with fallback logic."""
    image_url = (
        author_data.get('author_image_url') or 
        author_data.get('author_image') or 
        author_data.get('image_url') or
        author_data.get('cover_image')
    )
    
    if image_url:
        return cache_image(image_url)
    
    return None

def get_cached_book_cover_url(book_data: Dict[str, Any]) -> Optional[str]:
    """Get cached book cover image URL from book data, with fallback logic."""
    cover_url = (
        book_data.get('cover_image') or 
        book_data.get('Cover Image') or
        book_data.get('cover_url') or
        book_data.get('image_url')
    )
    
    if cover_url:
        return cache_image(cover_url)
    
    return None

def preload_images_from_database():
    """
    Preload all images (author images and book covers) from the database into the cache.
    This runs automatically in the background.
    """
    try:
        image_cache = container.get_image_cache_service()
        results = image_cache.preload_images_from_database()
        
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)
        
        if total_count > 0:
            _LOGGER.debug(f"Preloaded {success_count}/{total_count} images successfully")
        
        return results
        
    except Exception as e:
        _LOGGER.error(f"Error preloading images: {e}", exc_info=True)
        return {}
