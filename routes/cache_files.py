"""
Module Name: cache_files.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 9, 2026
Description:
    Serves cached cover and author images from <data>/cache/images to
    logged-in users only, so the cache isn't readable without signing in.

Location:
    /routes/cache_files.py
"""

from flask import Blueprint, abort, send_file
from flask_login import login_required

from services.metadata.covers.image_cache_service import CACHE_URL_PREFIX, resolve_cached_image

cache_files_bp = Blueprint('cache_files', __name__)


@cache_files_bp.route(f'{CACHE_URL_PREFIX}<cache_type>/<filename>')
@login_required
def cached_image(cache_type, filename):
    """Serve one cached image. resolve_cached_image() accepts only
    '<md5>.<image-ext>' names inside a known cache type, so traversal and
    arbitrary file reads are impossible."""
    path = resolve_cached_image(f'{cache_type}/{filename}')
    if path is None:
        abort(404)
    response = send_file(path, conditional=True, max_age=7 * 24 * 3600)
    response.headers['Cache-Control'] = 'private, max-age=604800'
    return response
