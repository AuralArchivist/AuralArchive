"""
Module Name: storage_status.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 11, 2026
Description:
    Health report for every storage location.

Location:
    /services/library/storage_status.py
"""

import os
import re
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from core.paths import get_path_resolver
from services.container import container

_LOGGER = get_module_logger("Service.Library.StorageStatus")

LOW_SPACE_BYTES = 2 * 1024 ** 3
_BANNER_TTL_SECONDS = 60

_banner_lock = threading.Lock()
_banner_cache: Dict[str, Any] = {'at': 0.0, 'alerts': []}


def _mount_points() -> List[str]:
    """Mount points visible to this process (Linux); [] elsewhere."""
    points = []
    try:
        with open('/proc/self/mountinfo', 'r', encoding='utf-8') as fh:
            for line in fh:
                fields = line.split()
                if len(fields) > 4:
                    points.append(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), fields[4]))
    except OSError:
        pass
    return points


def mount_point_for(path: str, points: Optional[List[str]] = None) -> Optional[str]:
    """Closest mount point containing path, ignoring '/' (the container's own
    filesystem). None means the path lives on the root filesystem."""
    real = os.path.realpath(path)
    best = None
    for point in (points if points is not None else _mount_points()):
        if point == '/':
            continue
        if real == point or real.startswith(point.rstrip('/') + '/'):
            if best is None or len(point) > len(best):
                best = point
    return best


def folder_status(path: str) -> Dict[str, Any]:
    status: Dict[str, Any] = {
        'path': path,
        'exists': os.path.isdir(path),
        'writable': False,
        'free_bytes': None,
        'total_bytes': None,
        'device': None,
    }
    if status['exists']:
        status['writable'] = os.access(path, os.W_OK | os.X_OK)
        try:
            usage = shutil.disk_usage(path)
            status['free_bytes'], status['total_bytes'] = usage.free, usage.total
            status['device'] = os.stat(path).st_dev
        except OSError:
            pass
    return status


def _human(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return 'unknown'
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f"{size:.1f} {unit}" if unit != 'B' else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def get_storage_report() -> Dict[str, Any]:
    """Full storage state for the UI and APIs."""
    resolver = get_path_resolver()
    docker = resolver.is_docker()
    points = _mount_points() if docker else []
    notices: List[Dict[str, str]] = []

    folders: Dict[str, Dict[str, Any]] = {}
    for name, info in resolver.get_all_path_info().items():
        if info['source'] == 'default':
            resolver.ensure_media_path(name)
        status = folder_status(info['path'])
        status.update({
            'value': info['path'] if info['source'] != 'default' else '',
            'default': info['default'],
            'source': info['source'],
            'locked': info['locked'],
            'env_var': info['env_var'],
            'mounted': bool(mount_point_for(info['path'], points)) if docker else None,
            'free_human': _human(status['free_bytes']),
        })
        folders[name] = status

        if not status['exists']:
            notices.append({'level': 'error', 'folder': name, 'short': "Folder not found",
                            'message': f"The {name} folder does not exist: {info['path']}"})
        elif not status['writable']:
            notices.append({'level': 'error', 'folder': name, 'short': "AuralArchive can't write here",
                            'message': f"AuralArchive can't write to the {name} folder: {info['path']}"})
        elif status['free_bytes'] is not None and status['free_bytes'] < LOW_SPACE_BYTES:
            notices.append({'level': 'warning', 'folder': name, 'short': f"Only {status['free_human']} free",
                            'message': f"Only {status['free_human']} free in the {name} folder"})

        if docker and status['exists'] and not status['mounted'] and name in ('library', 'downloads'):
            notices.append({
                'level': 'error', 'folder': name,
                'short': "Not mounted - contents are lost when the container is recreated",
                'message': f"The {name} folder ({info['path']}) is not a bind mount - its contents "
                           f"will be lost when the container is recreated. Mount a host folder there.",
            })

    library = folders['library']
    # In Docker each folder is its own mount, so moves between them are always copies.
    disk_hints = () if resolver.has_fixed_media_paths() else (
        ('work', 'finished Audible downloads and conversions are copied into the library instead of moved'),
        ('downloads', 'imports copy files into the library instead of moving them'),
    )
    for name, effect in disk_hints:
        other = folders[name]
        if library['device'] is not None and other['device'] is not None and library['device'] != other['device']:
            notices.append({
                'level': 'info', 'folder': name,
                'short': "Different disk from Library, so books are copied rather than moved",
                'message': f"The {name} and library folders are on different filesystems, so {effect}. "
                           f"Putting them on the same disk makes this instant.",
            })

    libraries = _library_report(folders, points if docker else None, notices)

    data_dir = resolver.get_data_dir()
    app_data = {
        'data_dir': data_dir,
        'data_dir_source': resolver.get_data_dir_source(),
        'config_file': resolver.get_config_file(),
        'secrets': resolver.get_secrets_dir(),
        'database': resolver.get_database_path(),
        'cache': resolver.get_cache_dir(),
        'logs': resolver.get_logs_dir(),
        'backups': resolver.get_backups_dir(),
        'media_dir': resolver.get_media_dir(),
        'free_human': _human(folder_status(data_dir)['free_bytes']),
        'mounted': bool(mount_point_for(data_dir, points)) if docker else None,
    }
    if docker and not app_data['mounted']:
        notices.append({
            'level': 'error', 'folder': 'config',
            'short': "App data (/config) isn't mounted - settings are lost when the container is recreated",
            'message': f"The app data folder ({data_dir}) is not a bind mount - settings, the database "
                       f"and saved credentials will be lost when the container is recreated.",
        })

    return {'docker': docker, 'folders': folders, 'app_data': app_data, 'notices': notices, 'libraries': libraries}


def _library_report(folders: Dict[str, Dict[str, Any]], points: Optional[List[str]],
                    notices: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Health of every library. The Storage-path library mirrors the library folder above."""
    try:
        repo = container.get_library_repository()
        libraries = repo.all()
        counts = repo.book_counts()
    except Exception:
        _LOGGER.debug("Library list unavailable for the storage report", exc_info=True)
        return []
    downloads = folders.get('downloads') or {}
    report = []
    for lib in libraries:
        status = folders['library'] if lib['uses_storage_path'] else folder_status(lib['root'])
        entry = {
            'id': lib['id'], 'name': lib['name'], 'root': lib['root'], 'uses_storage_path': lib['uses_storage_path'],
            'naming_template': lib['naming_template'], 'is_default': lib['is_default'], 'enabled': lib['enabled'],
            'books': counts.get(lib['id'], 0), 'exists': status['exists'], 'writable': status['writable'],
            'free_human': _human(status.get('free_bytes')),
            'mounted': (bool(mount_point_for(lib['root'], points)) if points is not None else None),
            'same_disk_as_downloads': (status.get('device') is not None and downloads.get('device') is not None
                                       and status.get('device') == downloads.get('device')),
        }
        report.append(entry)
        if lib['uses_storage_path'] or not lib['enabled']:
            continue
        key = f"library:{lib['id']}"
        if not status['exists']:
            notices.append({'level': 'error', 'folder': key, 'short': f"{lib['name']} library folder not found",
                            'message': f"The {lib['name']} library folder does not exist: {lib['root']}"})
        elif not status['writable']:
            notices.append({'level': 'error', 'folder': key, 'short': f"Can't write to the {lib['name']} library",
                            'message': f"AuralArchive can't write to the {lib['name']} library folder: {lib['root']}"})
        elif status['free_bytes'] is not None and status['free_bytes'] < LOW_SPACE_BYTES:
            notices.append({'level': 'warning', 'folder': key, 'short': f"{lib['name']}: only {entry['free_human']} free",
                            'message': f"Only {entry['free_human']} free in the {lib['name']} library folder"})
        if points is not None and status['exists'] and not entry['mounted']:
            notices.append({'level': 'error', 'folder': key,
                            'short': f"{lib['name']} isn't mounted - its books are lost when the container is recreated",
                            'message': f"The {lib['name']} library folder ({lib['root']}) is not a bind mount."})
        if status['exists'] and downloads.get('device') is not None and not entry['same_disk_as_downloads']:
            notices.append({'level': 'info', 'folder': key,
                            'short': f"{lib['name']} is on a different disk from Downloads, so books are copied",
                            'message': f"The {lib['name']} library and the downloads folder are on different "
                                       f"filesystems, so imports into it copy files instead of moving them."})
    return report


def get_banner_alerts() -> List[Dict[str, str]]:
    """Error-level storage notices for the app-wide banner, cached briefly
    because the banner renders on every page."""
    now = time.monotonic()
    with _banner_lock:
        if now - _banner_cache['at'] < _BANNER_TTL_SECONDS:
            return _banner_cache['alerts']
    try:
        alerts = [n for n in get_storage_report()['notices'] if n['level'] == 'error']
    except Exception:
        _LOGGER.warning("Storage check for the banner failed", exc_info=True)
        alerts = []
    with _banner_lock:
        _banner_cache.update(at=now, alerts=alerts)
    return alerts


def invalidate_banner_cache() -> None:
    with _banner_lock:
        _banner_cache['at'] = 0.0
