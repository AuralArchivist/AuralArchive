"""
Module Name: path_resolver.py
Author: AuralArchivist
Created: January 19, 2026
Last Modified: October 11, 2026
Description:
    Resolves every filesystem location: app data in /config (Docker) or
    app/config, built-in media folders in /library etc. (Docker) or app/media.

Location:
    /core/paths.py
"""

import configparser
import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

from core.convert import to_bool

_LOGGER = logging.getLogger("Utils.PathResolver")

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(APP_ROOT, 'config')
MEDIA_DIR = os.path.join(APP_ROOT, 'media')
DOCKER_DATA_DIR = '/config'

PATHS_SECTION = 'paths'
MEDIA_FOLDERS = ('library', 'downloads', 'import', 'work')

_MEDIA_ENV_VARS: Dict[str, Tuple[str, ...]] = {
    'library': ('AURALARCHIVE_LIBRARY_DIR', 'AURALARCHIVE_AUDIOBOOKS_DIR'),
    'downloads': ('AURALARCHIVE_DOWNLOADS_DIR',),
    'import': ('AURALARCHIVE_IMPORT_DIR',),
    'work': ('AURALARCHIVE_WORK_DIR',),
}

# Fixed media folders inside the Docker image; users pick host folders with bind mounts.
DOCKER_MEDIA_PATHS = {name: f'/{name}' for name in MEDIA_FOLDERS}

DATABASE_FILENAME = 'auralarchive.db'


def _absolute(path: str) -> str:
    """Expand ~ and make relative paths absolute against APP_ROOT."""
    path = os.path.expanduser(path.strip())
    if not os.path.isabs(path):
        path = os.path.join(APP_ROOT, path)
    return os.path.normpath(path)


def _first_env(names: Tuple[str, ...]) -> Tuple[Optional[str], Optional[str]]:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return name, value
    return None, None


def _resolve_data_dir(docker_image: bool) -> Tuple[str, str]:
    """Return (data_dir, source). AURALARCHIVE_DATA_DIR is for tests and development."""
    if docker_image:
        return DOCKER_DATA_DIR, 'docker'
    _, value = _first_env(('AURALARCHIVE_DATA_DIR',))
    if value:
        return _absolute(value), 'AURALARCHIVE_DATA_DIR'
    return DATA_DIR, 'default'


class PathResolver:
    """Resolves every AuralArchive path. Use get_path_resolver()."""

    def __init__(self):
        _enforce_not_root()
        self._is_docker =bool(os.getenv('DOCKER_CONTAINER')) or os.path.exists('/.dockerenv')
        self._docker_image = to_bool(os.getenv('DOCKER_CONTAINER'))
        self._data_dir, self._data_dir_source = _resolve_data_dir(self._docker_image)
        # A test data folder keeps its built-in media folders inside it.
        self._media_dir = MEDIA_DIR if self._data_dir_source == 'default' else os.path.join(self._data_dir, 'media')

        self._paths_lock = threading.Lock()
        self._paths_cache: Dict[str, str] = {}
        self._paths_mtime: Optional[float] = None

        for directory in (
            self._data_dir,
            self.get_config_dir(),
            self.get_database_dir(),
            self.get_cache_dir(),
            self.get_logs_dir(),
            self.get_backups_dir(),
        ):
            os.makedirs(directory, exist_ok=True)
        self._ensure_private_dir(self.get_secrets_dir())

        for name in MEDIA_FOLDERS:
            if self.get_path_info(name)['source'] == 'default':
                os.makedirs(self.get_path_info(name)['path'], exist_ok=True)

        if self._docker_image:
            self._warn_ignored_env()

        _LOGGER.info(
            "Storage paths resolved",
            extra={
                "data_dir": self._data_dir,
                "data_dir_source": self._data_dir_source,
                "media_dir": self._media_dir,
                "docker": self._is_docker,
            },
        )

    # --- App data (always app-owned, always created) ---

    def get_data_dir(self) -> str:
        return self._data_dir

    def get_data_dir_source(self) -> str:
        """'docker', 'AURALARCHIVE_DATA_DIR' or 'default'."""
        return self._data_dir_source

    def get_config_dir(self) -> str:
        return os.path.join(self._data_dir, 'config')

    def get_config_file(self) -> str:
        return os.path.join(self.get_config_dir(), 'config.txt')

    def get_secrets_dir(self) -> str:
        return os.path.join(self._data_dir, 'secrets')

    def get_database_dir(self) -> str:
        return os.path.join(self._data_dir, 'database')

    def get_database_path(self) -> str:
        return os.path.join(self.get_database_dir(), DATABASE_FILENAME)

    def get_cache_dir(self) -> str:
        override = os.getenv('AURALARCHIVE_CACHE_DIR')
        return _absolute(override) if override else os.path.join(self._data_dir, 'cache')

    def get_logs_dir(self) -> str:
        override = os.getenv('AURALARCHIVE_LOGS_DIR')
        return _absolute(override) if override else os.path.join(self._data_dir, 'logs')

    def get_backups_dir(self, kind: Optional[str] = None) -> str:
        """Backups root, or a created subfolder ('config', 'database')."""
        root = os.path.join(self._data_dir, 'backups')
        if not kind:
            return root
        path = os.path.join(root, kind)
        os.makedirs(path, exist_ok=True)
        return path

    def get_secret_file(self, filename: str) -> str:
        return os.path.join(self.get_secrets_dir(), filename)

    def get_audible_auth_file(self) -> str:
        return self.get_secret_file('audible_auth.json')

    def get_users_file(self) -> str:
        return self.get_secret_file('users.json')

    # --- Media folders (relocatable) ---

    def get_media_dir(self) -> str:
        return self._media_dir

    def default_media_path(self, name: str) -> str:
        if self._docker_image:
            return DOCKER_MEDIA_PATHS[name]
        return os.path.join(self._media_dir, name)

    def _configured_paths(self) -> Dict[str, str]:
        """[paths] from config.txt, re-read only when the file changes."""
        config_file = self.get_config_file()
        try:
            mtime = os.path.getmtime(config_file)
        except OSError:
            return {}
        with self._paths_lock:
            if mtime != self._paths_mtime:
                parser = configparser.RawConfigParser(interpolation=None)
                try:
                    parser.read(config_file, encoding='utf-8')
                    section = parser[PATHS_SECTION] if parser.has_section(PATHS_SECTION) else {}
                    self._paths_cache = {
                        name: str(section.get(name, '') or '').strip() for name in MEDIA_FOLDERS
                    }
                except (configparser.Error, OSError) as exc:
                    _LOGGER.warning("Could not read [paths] from config", extra={"error": str(exc)})
                    self._paths_cache = {}
                self._paths_mtime = mtime
            return dict(self._paths_cache)

    def get_path_info(self, name: str) -> Dict[str, Any]:
        """Describe one media folder for code and the Settings UI.

        Returns path, source ('docker' | 'env' | 'config' | 'default'), env_var
        (when source is env), default (the built-in location), and locked (True
        when Docker or an env var pins it, so the UI must not offer to change it).
        """
        if name not in MEDIA_FOLDERS:
            raise ValueError(f"Unknown media folder: {name}")
        default = self.default_media_path(name)

        if self._docker_image:
            return {'path': default, 'source': 'docker', 'env_var': None,
                    'default': default, 'locked': True}

        env_var, env_value = _first_env(_MEDIA_ENV_VARS[name])
        if env_value:
            return {'path': _absolute(env_value), 'source': 'env', 'env_var': env_var,
                    'default': default, 'locked': True}

        configured = self._configured_paths().get(name)
        if configured:
            return {'path': _absolute(configured), 'source': 'config', 'env_var': None,
                    'default': default, 'locked': False}

        return {'path': default, 'source': 'default', 'env_var': None,
                'default': default, 'locked': False}

    def get_all_path_info(self) -> Dict[str, Dict[str, Any]]:
        return {name: self.get_path_info(name) for name in MEDIA_FOLDERS}

    def ensure_media_path(self, name: str) -> str:
        """Effective path of a media folder, creating it if it is built-in."""
        info = self.get_path_info(name)
        if info['source'] == 'default':
            os.makedirs(info['path'], exist_ok=True)
        return info['path']

    _media_path = ensure_media_path

    def get_library_dir(self) -> str:
        return self._media_path('library')

    def get_downloads_dir(self) -> str:
        return self._media_path('downloads')

    def get_import_dir(self) -> str:
        return self._media_path('import')

    def get_work_dir(self) -> str:
        return self._media_path('work')

    def _work_subdir(self, name: str) -> str:
        """work/<name>, created only when work/ itself exists."""
        work = self.get_work_dir()
        path = os.path.join(work, name)
        if os.path.isdir(work):
            os.makedirs(path, exist_ok=True)
        else:
            _LOGGER.error("Work folder does not exist", extra={"work_dir": work})
        return path

    def get_audible_work_dir(self) -> str:
        return self._work_subdir('audible')

    def get_conversion_dir(self) -> str:
        return self._work_subdir('conversion')

    def get_failed_dir(self) -> str:
        return self._work_subdir('failed')

    # --- Helpers ---

    def is_docker(self) -> bool:
        """Only for UI hints; media paths follow the image's DOCKER_CONTAINER flag."""
        return self._is_docker

    def has_fixed_media_paths(self) -> bool:
        return self._docker_image

    @staticmethod
    def _ensure_private_dir(path: str) -> None:
        os.makedirs(path, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError as exc:
            _LOGGER.warning("Could not restrict permissions", extra={"path": path, "error": str(exc)})

    def _warn_ignored_env(self) -> None:
        names = ('AURALARCHIVE_DATA_DIR',) + tuple(n for v in _MEDIA_ENV_VARS.values() for n in v)
        for name in names:
            if (os.getenv(name) or '').strip():
                _LOGGER.warning(
                    "Ignoring this variable: in Docker the folders are fixed; "
                    "use volume mounts to choose host folders",
                    extra={"variable": name},
                )


_path_resolver: Optional[PathResolver] = None
_path_resolver_lock = threading.Lock()


def get_path_resolver() -> PathResolver:
    """Get or create the global PathResolver instance."""
    global _path_resolver
    if _path_resolver is None:
        with _path_resolver_lock:
            if _path_resolver is None:
                _path_resolver = PathResolver()
    return _path_resolver


# --- Module-level helpers ----------------------------------------------------

def ensure_within_directory(path: str, allowed_root: str, *, what: str = 'path') -> str:
    """Resolve ``path`` against ``allowed_root`` and refuse anything that escapes it.

    Handles '..' traversal, absolute-path overrides, and symlinks (both sides
    are passed through realpath before the commonpath check). Relative input
    is interpreted relative to ``allowed_root``. Returns the resolved absolute
    path, or raises ValueError.
    """
    root = os.path.realpath(allowed_root)
    candidate = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([candidate, root]) != root:
        raise ValueError(f'{what} escapes the configured directory: {path}')
    return candidate


def validate_user_media_path(name: str, value: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate a media folder path entered in Settings.

    Returns (value_to_store, error). value_to_store is '' for "use the
    built-in folder", or None (with no error) when the folder is locked by an
    env var and config.txt should be left alone. A non-empty value must be
    absolute and an existing, writable directory, and must not sit inside the
    app data folder.
    """
    resolver = get_path_resolver()
    info = resolver.get_path_info(name)
    value = (value or '').strip()

    if info['locked']:
        if value and os.path.normpath(os.path.expanduser(value)) != os.path.normpath(info['path']):
            by = info['env_var'] or 'the Docker image (use a volume mount instead)'
            return None, f"{name} is set by {by} and can't be changed here"
        return None, None

    if not value:
        return '', None

    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        return None, f"{name} path must be absolute: {value}"
    normalized = os.path.normpath(expanded)
    if os.path.normpath(normalized) == os.path.normpath(info['default']):
        return '', None
    if not os.path.isdir(normalized):
        return None, f"{name} folder does not exist: {normalized}"
    if not os.access(normalized, os.W_OK | os.X_OK):
        return None, f"AuralArchive can't write to the {name} folder: {normalized}"

    if _is_within(normalized, resolver.get_data_dir()) and not _is_within(normalized, resolver.get_media_dir()):
        return None, f"{name} folder can't be inside the app data folder: {normalized}"
    return normalized, None


def _is_within(path: str, root: str) -> bool:
    real_path, real_root = os.path.realpath(path), os.path.realpath(root)
    try:
        return os.path.commonpath([real_path, real_root]) == real_root
    except ValueError:
        return False


def _enforce_not_root() -> None:
    """Refuse to run as root unless AURALARCHIVE_ALLOW_ROOT is set.

    Runs before any file is created so a root run can't leave root-owned
    files behind.
    """
    if to_bool(os.getenv('AURALARCHIVE_ALLOW_ROOT')):
        return
    if os.geteuid() == 0:
        raise SystemExit(
            "AuralArchive refuses to run as root. Start it as a regular user that owns "
            "its data folder and can write to your media folders. (Set "
            "AURALARCHIVE_ALLOW_ROOT=true to override - not recommended.)"
        )


def resolve_audible_auth_file() -> str:
    """Return the path to audible_auth.json in the secrets directory."""
    return get_path_resolver().get_audible_auth_file()


def resolve_users_file() -> str:
    """Return the path to users.json in the secrets directory."""
    return get_path_resolver().get_users_file()
