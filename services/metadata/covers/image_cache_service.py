"""
Module Name: image_cache_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Local image caching service to reduce external requests for author images
    and book covers. Supports preload from DB and cache size management.

    Files live in <data>/cache/images/<cache_type>/ and are served only to
    logged-in users by routes/cache_files.py at CACHE_URL_PREFIX.

Location:
    /services/metadata/covers/image_cache_service.py
"""

import os
import hashlib
import requests
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urljoin, urlparse
import time

from sqlalchemy import text

from core.logging import get_module_logger
from core.net_guard import OutboundURLRejected, validate_outbound_url
from core.paths import APP_ROOT, get_path_resolver
from core.http import HttpSession

_LOGGER = get_module_logger("Service.ImageCache.Service")

CACHE_URL_PREFIX = '/cache/images/'
CACHE_TYPES = ('local', 'audible')
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')


def image_cache_root() -> Path:
    """<data>/cache/images - parent of the per-type cache folders."""
    return Path(get_path_resolver().get_cache_dir()) / 'images'

class ImageCacheService:
    """Local image caching service to reduce external web requests for author images and book covers."""
    
    def __init__(self, cache_dir: str = None, cache_type: str = "local", max_cache_size_mb: int = 500, max_age_days: Optional[int] = None, *, logger=None):
        self.logger = logger or _LOGGER
        self.cache_type = cache_type
        
        if cache_type not in CACHE_TYPES:
            raise ValueError(f"Unknown image cache type: {cache_type}")
        if cache_dir is None:
            cache_dir = image_cache_root() / cache_type

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.max_cache_size_bytes = max_cache_size_mb * 1024 * 1024
        self.max_age_seconds: Optional[float] = max_age_days * 86400 if max_age_days is not None else None
        self.session = HttpSession(headers={'User-Agent': 'AuralArchive/1.0 (Image Cache Service)'})
        
        self.logger.success(
            "Image cache service started successfully",
            extra={"cache_dir": str(self.cache_dir), "cache_type": self.cache_type},
        )
        
    def _generate_cache_key(self, url: str) -> str:
        """Generate a unique cache key from URL."""
        return hashlib.md5(url.encode()).hexdigest()
    
    def _normalize_url(self, url: str) -> str:
        """Convert relative URLs to absolute URLs for downloading."""
        if url.startswith('http://') or url.startswith('https://'):
            return url

        if url.startswith('/static/') or url.startswith(CACHE_URL_PREFIX):
            raise ValueError(f"Refusing to HTTP-fetch local path: {url}")

        base = None
        from flask import has_request_context, request as flask_request
        if has_request_context():
            base = flask_request.host_url.rstrip('/')

        if base is None:
            host = os.environ.get('AURALARCHIVE_HOST', 'http://localhost:5000').rstrip('/')
            base = host

        if url.startswith('/'):
            return f"{base}{url}"
        return f"{base}/{url}"
    
    def _get_cache_path(self, cache_key: str, extension: str = None) -> Path:
        """Get the full path for a cached file."""
        if extension and not extension.startswith('.'):
            extension = f'.{extension}'
        elif not extension:
            extension = '.jpg'
            
        return self.cache_dir / f"{cache_key}{extension}"
    
    def _detect_extension_from_url(self, url: str) -> str:
        """Detect file extension from URL."""
        parsed = urlparse(url)
        path = parsed.path.lower()
        
        for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
            if path.endswith(ext):
                return ext
                
        return '.jpg'
    
    def _url_for(self, cache_path: Path) -> str:
        return f"{CACHE_URL_PREFIX}{self.cache_type}/{cache_path.name}"

    def _resolve_static_path(self, url: str) -> Optional[Path]:
        """Return an on-disk Path if the URL points to a local /static/ file
        or an already-cached image, else None."""
        if url.startswith(CACHE_URL_PREFIX):
            return resolve_cached_image(url)
        if not url.startswith('/static/'):
            return None
        static_root = (Path(APP_ROOT) / 'frontend' / 'static').resolve()
        candidate = (static_root.parent / url.lstrip('/')).resolve()
        # Must stay inside frontend/static and be an image: a '../' in a stored
        # cover URL must never copy config/secret files into the public cache.
        if os.path.commonpath([str(candidate), str(static_root)]) != str(static_root):
            self.logger.warning(f"Refusing static path outside frontend/static: {url}")
            return None
        if candidate.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'}:
            return None
        return candidate if candidate.is_file() else None

    def _guarded_get(self, url: str, max_redirects: int = 3):
        """GET an image URL, refusing link-local/metadata targets on every hop.

        Redirects are followed manually (cover CDNs redirect legitimately) so
        each Location is checked by core.net_guard before it is requested.
        """
        for _ in range(max_redirects + 1):
            try:
                validate_outbound_url(url, default_scheme=None)
            except OutboundURLRejected as exc:
                self.logger.warning(f"Refusing image URL {url}: {exc}")
                return None
            response = self.session.get(url, timeout=30, stream=True, allow_redirects=False)
            if response.is_redirect and response.headers.get('Location'):
                url = urljoin(url, response.headers['Location'])
                response.close()
                continue
            return response
        self.logger.warning(f"Too many redirects fetching image: {url}")
        return None

    def _download_image(self, url: str, cache_path: Path) -> bool:
        """Download an image from URL to cache path."""
        try:
            local_source = self._resolve_static_path(url)
            if local_source:
                if local_source == cache_path:
                    return True
                import shutil
                shutil.copy2(local_source, cache_path)
                self.logger.debug(f"Image copied from local path: {local_source} -> {cache_path}")
                return True

            full_url = self._normalize_url(url)
            self.logger.debug(f"Downloading image: {url} -> {full_url}")
            
            response = self._guarded_get(full_url)
            if response is None:
                return False
            response.raise_for_status()
            
            content_type = response.headers.get('content-type', '')
            if not content_type.startswith('image/'):
                self.logger.warning(f"URL does not return an image: {url} (content-type: {content_type})")
                return False
            
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode='wb',
                    delete=False,
                    dir=str(cache_path.parent),
                    prefix=cache_path.stem + '.',
                    suffix=cache_path.suffix + '.tmp',
                ) as temp_file:
                    temp_path = Path(temp_file.name)
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            temp_file.write(chunk)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, cache_path)
            finally:
                if temp_path and temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
            
            self.logger.debug(f"Image cached successfully: {cache_path}")
            return True
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to download image {url}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Error caching image {url}: {e}", exc_info=True)
            return False
    
    def _is_expired(self, file_path: Path) -> bool:
        """Return True if the cached file is older than max_age_seconds."""
        if self.max_age_seconds is None:
            return False
        try:
            age = time.time() - file_path.stat().st_mtime
            return age > self.max_age_seconds
        except OSError:
            return True

    def _cleanup_cache(self):
        """Remove old cache files if cache size exceeds limit."""
        try:
            cache_files = []
            total_size = 0
            
            for file_path in self.cache_dir.glob('*'):
                if file_path.is_file() and not file_path.name.endswith('.tmp'):
                    stat = file_path.stat()
                    if self.max_age_seconds is not None and (time.time() - stat.st_mtime) > self.max_age_seconds:
                        try:
                            file_path.unlink()
                            self.logger.debug("Pruned expired cache file", extra={"path": str(file_path)})
                        except OSError:
                            self.logger.debug("Could not prune %s", file_path, exc_info=True)
                        continue
                    cache_files.append({
                        'path': file_path,
                        'size': stat.st_size,
                        'mtime': stat.st_mtime
                    })
                    total_size += stat.st_size
            
            if total_size <= self.max_cache_size_bytes:
                return
            
            self.logger.info(
                "Cache size exceeds limit; cleaning up",
                extra={
                    "cache_size_mb": round(total_size / 1024 / 1024, 1),
                    "limit_mb": round(self.max_cache_size_bytes / 1024 / 1024, 1),
                },
            )
            
            cache_files.sort(key=lambda x: x['mtime'])
            
            target_size = self.max_cache_size_bytes * 0.8
            
            for file_info in cache_files:
                if total_size <= target_size:
                    break
                    
                try:
                    file_info['path'].unlink()
                    total_size -= file_info['size']
                    self.logger.debug(f"Removed cached file: {file_info['path']}")
                except Exception as e:
                    self.logger.error(f"Error removing cache file {file_info['path']}: {e}", exc_info=True)
            
            self.logger.info(
                "Cache cleanup completed",
                extra={"cache_size_mb": round(total_size / 1024 / 1024, 1)},
            )
            
        except Exception as e:
            self.logger.error(f"Error during cache cleanup: {e}", exc_info=True)
    
    def get_cached_image_url(self, original_url: str) -> Optional[str]:
        """Get a local cached version of an image URL."""
        if not original_url:
            return None
            
        try:
            if original_url.startswith('/static/') or original_url.startswith(CACHE_URL_PREFIX):
                self.logger.debug(f"Skipping cache for local URL: {original_url}")
                return original_url
            
            if original_url.startswith('/metadata/items/'):
                self.logger.warning(f"Invalid metadata URL detected: {original_url} - returning None")
                return None
            
            cache_key = self._generate_cache_key(original_url)
            extension = self._detect_extension_from_url(original_url)
            cache_path = self._get_cache_path(cache_key, extension)
            
            if cache_path.exists() and not self._is_expired(cache_path):
                cache_path.touch()
                return self._url_for(cache_path)
            
            if original_url.startswith('http://') or original_url.startswith('https://'):
                if self._download_image(original_url, cache_path):
                    self._cleanup_cache()
                    return self._url_for(cache_path)
            else:
                self.logger.debug(f"Skipping download for non-HTTP URL: {original_url}")
            
            return None
            
        except Exception:
            self.logger.debug("Error getting cached image for %s", original_url, exc_info=True)
            return None
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get statistics about the image cache."""
        try:
            cache_files = list(self.cache_dir.glob('*'))
            cache_files = [f for f in cache_files if f.is_file() and not f.name.endswith('.tmp')]
            
            total_size = sum(f.stat().st_size for f in cache_files)
            
            return {
                'cache_dir': str(self.cache_dir),
                'total_files': len(cache_files),
                'total_size_mb': round(total_size / 1024 / 1024, 2),
                'max_size_mb': round(self.max_cache_size_bytes / 1024 / 1024, 2),
                'usage_percent': round((total_size / self.max_cache_size_bytes) * 100, 1) if self.max_cache_size_bytes > 0 else 0
            }
            
        except Exception as e:
            self.logger.error(f"Error getting cache stats: {e}", exc_info=True)
            return {'error': str(e)}
    
    def clear_cache(self) -> bool:
        """Clear all cached images."""
        try:
            removed_count = 0
            for file_path in self.cache_dir.glob('*'):
                if file_path.is_file():
                    file_path.unlink()
                    removed_count += 1
            
            self.logger.info(f"Cache cleared: {removed_count} files removed")
            return True
            
        except Exception as e:
            self.logger.error(f"Error clearing cache: {e}", exc_info=True)
            return False
    
    def preload_images_from_database(self) -> Dict[str, bool]:
        """Preload all images (author images and book covers) from the database into the cache."""
        try:
            from services.container import container
            
            db_service = container.get_database_service()
            engine = db_service.connection_manager.get_engine()

            image_urls = []

            with engine.connect() as conn:
                author_rows = conn.execute(text('''
                    SELECT DISTINCT author_image_url
                    FROM authors
                    WHERE author_image_url IS NOT NULL
                    AND author_image_url != ''
                    AND author_image_url LIKE 'http%'
                ''')).fetchall()
                author_images = [row[0] for row in author_rows]
                image_urls.extend(author_images)

                cover_rows = conn.execute(text('''
                    SELECT DISTINCT cover_image
                    FROM books
                    WHERE cover_image IS NOT NULL
                    AND cover_image != ''
                    AND cover_image LIKE 'http%'
                ''')).fetchall()
                cover_images = [row[0] for row in cover_rows]
                image_urls.extend(cover_images)

            if image_urls:
                self.logger.info(
                    f"Preloading {len(image_urls)} images into cache ({len(author_images)} author images, {len(cover_images)} book covers)..."
                )

                results: Dict[str, bool] = {}
                for url in image_urls:
                    try:
                        cache_key = self._generate_cache_key(url)
                        extension = self._detect_extension_from_url(url)
                        cache_path = self._get_cache_path(cache_key, extension)

                        if cache_path.exists():
                            results[url] = True
                        else:
                            results[url] = self._download_image(url, cache_path)

                    except Exception as e:
                        self.logger.error(f"Error preloading image {url}: {e}", exc_info=True)
                        results[url] = False

                self._cleanup_cache()

                success_count = sum(1 for success in results.values() if success)
                self.logger.info(
                    f"Preloaded {success_count}/{len(image_urls)} images successfully"
                )

                return results
            else:
                self.logger.info("No images found in database to preload")
                return {}

        except Exception as e:
            self.logger.error(f"Error preloading images: {e}", exc_info=True)
            return {}


def resolve_cached_image(url_or_path: str) -> Optional[Path]:
    """Map '/cache/images/<type>/<md5>.<ext>' (or '<type>/<md5>.<ext>') to the
    file on disk, or None. Only exact cache-key filenames are accepted, so a
    crafted path can never reach outside the cache."""
    rel = url_or_path[len(CACHE_URL_PREFIX):] if url_or_path.startswith(CACHE_URL_PREFIX) else url_or_path
    parts = rel.split('/')
    if len(parts) != 2 or parts[0] not in CACHE_TYPES:
        return None
    stem, dot, ext = parts[1].rpartition('.')
    if (not dot or len(stem) != 32 or any(c not in '0123456789abcdef' for c in stem)
            or f'.{ext}' not in IMAGE_EXTENSIONS):
        return None
    path = image_cache_root() / parts[0] / parts[1]
    return path if path.is_file() else None
