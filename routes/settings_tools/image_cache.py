"""
Module Name: image_cache.py
Author: AuralArchivist
Created: August 3, 2025
Last Modified: October 9, 2026
Description:
    Image cache management routes for stats and clearing.

Location:
    /routes/settings_tools/image_cache.py
"""

from flask import Blueprint, jsonify

from services.container import container
from core.logging import get_module_logger

cache_management_bp = Blueprint('cache_management', __name__)
logger = get_module_logger("Routes.Settings.ImageCache")

@cache_management_bp.route('/image-cache/stats')
def get_image_cache_stats():
    """Get image cache statistics for the settings page."""
    try:
        cache_service = container.get_image_cache_service()
        stats = cache_service.get_cache_stats()
        
        return jsonify({
            'success': True,
            'stats': stats
        })
        
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@cache_management_bp.route('/image-cache/clear', methods=['POST'])
def clear_image_cache():
    """Clear all cached images."""
    try:
        cache_service = container.get_image_cache_service()
        success = cache_service.clear_cache()
        
        if success:
            logger.info("Image cache cleared successfully")
            return jsonify({
                'success': True,
                'message': 'Image cache cleared successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to clear image cache'
            }), 500
            
    except Exception as e:
        logger.error(f"Error clearing image cache: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
