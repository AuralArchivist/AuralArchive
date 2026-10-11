"""
Module Name: client_selector.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Chooses the appropriate download client by type, capability, and priority.
    Currently selects qBittorrent for torrent workloads while caching configs
    and instances for reuse.

Location:
    /services/acquisition/client_selector.py
"""

import re
import time
from typing import Any, Dict, Mapping, Optional

from core.logging import get_module_logger
from core.paths import get_path_resolver
from core.convert import to_bool
from services.container import container



QB_AUTH_MODES = ("api_key", "credentials")


def qbittorrent_config_from_section(section: Mapping[str, Any]) -> Dict[str, Any]:
    """The qBittorrent client config built from a [qbittorrent] section.

    The only translation from stored settings to the client: the download
    pipeline, the dashboard and the Settings test all use it, so they sign in
    the same way. Both sign-in methods are passed through; the client uses
    only the selected one.
    """
    def _get(option: str, fallback: str = "") -> str:
        value = section.get(option, fallback)
        return fallback if value is None else str(value)

    def _clean(value: Optional[str]) -> str:
        return str(value or "").strip()

    password = _get("qb_password").strip()
    if len(password) >= 2 and password[0] == password[-1] == '"':
        password = password[1:-1]

    buckets: Dict[int, Dict[str, str]] = {}
    for option, value in section.items():
        match = re.match(r"path_mapping_(\d+)_(qb_path|host_path|remote|local)", option.lower())
        if not match:
            continue
        bucket = buckets.setdefault(int(match.group(1)), {"remote": "", "local": ""})
        bucket["remote" if match.group(2) in {"qb_path", "remote"} else "local"] = _clean(value)
    path_mappings = [m for _, m in sorted(buckets.items()) if m["remote"] or m["local"]]

    if not path_mappings:
        for entry in _get("path_mappings").split(";"):
            if "|" in entry:
                remote, local = (part.strip() for part in entry.split("|", 1))
                if remote or local:
                    path_mappings.append({"remote": remote, "local": local})

    remote_root = path_mappings[0]["remote"] if path_mappings else ""
    local_root = path_mappings[0]["local"] if path_mappings else ""
    if not remote_root:
        remote_root = _clean(_get("download_path") or _get("download_path_remote")
                             or _get("save_path_root") or _get("save_path"))
    if not local_root:
        local_root = _clean(_get("download_path_local") or _get("local_save_path")) or remote_root

    try:
        port = int(_get("qb_port", "8080") or 8080)
    except ValueError:
        port = 8080

    config = {
        "enabled": to_bool(section.get("enabled"), False, empty_as_default=True),
        "host": _clean(_get("qb_host")) or "localhost",
        "port": port,
        "qb_auth_mode": _clean(_get("qb_auth_mode")) or "credentials",
        "qb_api_key": _clean(_get("qb_api_key")),
        "username": _clean(_get("qb_username")),
        "password": password,
        "use_ssl": to_bool(section.get("use_ssl"), False, empty_as_default=True),
        "category": _clean(_get("category")) or "auralarchive",
        "download_path_remote": remote_root,
        "download_path_local": local_root,
        "path_mappings": path_mappings,
    }
    if section.get("verify_cert") not in (None, ""):
        config["verify_cert"] = to_bool(section.get("verify_cert"), True, empty_as_default=True)
    return config


class ClientSelector:
    """
    Selects best download client for each download: qBittorrent for
    torrents, SABnzbd or NZBGet for usenet.

    Selection criteria:
    1. Capability matching (torrent/magnet vs NZB)
    2. User-configured priority
    3. Client health and availability
    """

    CLIENT_RECONNECT_INTERVAL = 30.0

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.DownloadManagement.ClientSelector")
        self._client_cache: Dict[str, Any] = {}
        self._client_configs: Dict[str, Dict[str, Any]] = {}
        self._client_failure_times: Dict[str, float] = {}

    def invalidate_cache(self, client_name: Optional[str] = None):
        """Invalidate cached client instances/config and close stale connections.

        Also clears the circuit-breaker failure time so the client can
        reconnect immediately on the next request (e.g. after settings change).
        """
        if client_name:
            targets = [client_name]
        else:
            targets = list(set(self._client_cache) | set(self._client_configs) | set(self._client_failure_times))

        for target in targets:
            client = self._client_cache.pop(target, None)
            self._client_configs.pop(target, None)
            self._client_failure_times.pop(target, None)

            if not client:
                continue

            disconnect = getattr(client, "disconnect", None)
            if callable(disconnect):
                try:
                    disconnect()
                except Exception as exc:
                    self.logger.debug(
                        "Ignoring client disconnect failure during cache invalidation",
                        extra={"client": target, "error": str(exc)},
                    )

    def select_client(self, download_type: str) -> Optional[str]:
        """Select best client for the provided download type."""

        if download_type in ("torrent", "magnet"):
            return self._select_torrent_client()

        if download_type == "nzb":
            return self._select_nzb_client()

        self.logger.error("Unknown download type", extra={
            "download_type": download_type
        })
        return None

    def _select_torrent_client(self) -> Optional[str]:
        """Select best available torrent client (currently qBittorrent)."""

        if not self._is_qbittorrent_enabled():
            self.logger.debug("No torrent client enabled", extra={
                "client": "qbittorrent"
            })
            return None

        self.logger.debug("Selecting torrent client", extra={
            "client": "qbittorrent"
        })
        return "qbittorrent"

    def _select_nzb_client(self) -> Optional[str]:
        """Select best available NZB client (SABnzbd preferred, then NZBGet)."""

        if self._is_sabnzbd_enabled():
            self.logger.debug("Selecting NZB client", extra={"client": "sabnzbd"})
            return "sabnzbd"

        if self._is_nzbget_enabled():
            self.logger.debug("Selecting NZB client", extra={"client": "nzbget"})
            return "nzbget"

        self.logger.debug("No NZB client enabled")
        return None

    def get_cached_client(self, client_name: str):
        """Return a client instance only if it is already connected and cached.

        Makes zero network calls. Use this in page-render paths where a TCP
        timeout against an unavailable client would block the response.
        """
        cached = self._client_cache.get(client_name)
        if cached and getattr(cached, "connected", False):
            return cached
        return None

    def get_client(self, client_name: str):
        """
        Get client instance by name.

        Returns None immediately (without a network attempt) when the client
        is in circuit-breaker cooldown after a recent connection failure.
        Only caches successfully connected clients so a disconnected instance
        is never returned from cache.
        """

        if client_name in self._client_cache:
            cached = self._client_cache[client_name]
            if getattr(cached, "connected", False):
                return cached
            self._client_cache.pop(client_name, None)
            self._client_configs.pop(client_name, None)

        last_failure = self._client_failure_times.get(client_name)
        if last_failure is not None:
            if time.monotonic() - last_failure < self.CLIENT_RECONNECT_INTERVAL:
                return None
            self._client_failure_times.pop(client_name, None)

        try:
            if client_name == "qbittorrent":
                from integrations.download_clients.qbittorrent_client import QBittorrentClient

                config = self._load_client_config("qbittorrent")
                if not config.get("enabled"):
                    self.logger.debug("qBittorrent is disabled; skipping client init")
                    return None
                client = QBittorrentClient(config)
                if not client.connect():
                    self._client_failure_times[client_name] = time.monotonic()
                    return None
                self._client_cache[client_name] = client
                self._client_configs[client_name] = config
                return client

            if client_name == "sabnzbd":
                from integrations.download_clients.sabnzbd_client import SABnzbdClient

                config = self._load_client_config("sabnzbd")
                if not config.get("enabled"):
                    self.logger.debug("SABnzbd is disabled; skipping client init")
                    return None
                client = SABnzbdClient(config)
                if not client.connect():
                    self._client_failure_times[client_name] = time.monotonic()
                    return None
                self._client_cache[client_name] = client
                self._client_configs[client_name] = config
                return client

            if client_name == "nzbget":
                from integrations.download_clients.nzbget_client import NZBGetClient

                config = self._load_client_config("nzbget")
                if not config.get("enabled"):
                    self.logger.debug("NZBGet is disabled; skipping client init")
                    return None
                client = NZBGetClient(config)
                if not client.connect():
                    self._client_failure_times[client_name] = time.monotonic()
                    return None
                self._client_cache[client_name] = client
                self._client_configs[client_name] = config
                return client

            self.logger.error("Unknown client", extra={
                "client": client_name
            })
            return None

        except Exception as exc:
            self._client_failure_times[client_name] = time.monotonic()
            self.logger.exception("Error loading client", extra={
                "client": client_name,
                "error": str(exc)
            })
            return None

    def get_client_config(self, client_name: str, *, refresh: bool = False) -> Dict[str, Any]:
        """Return cached configuration for a download client."""

        if refresh:
            self._client_configs.pop(client_name, None)

        if client_name in self._client_configs:
            return self._client_configs[client_name]

        config = self._load_client_config(client_name)
        if config:
            self._client_configs[client_name] = config
        return config

    def _load_client_config(self, client_name: str) -> Dict[str, Any]:
        """Get configuration for a specific download client."""

        config_service = container.get_config_service()

        if client_name == "qbittorrent":
            config_parser = config_service.load_config()
            if config_parser.has_section("qbittorrent"):
                return qbittorrent_config_from_section(config_parser["qbittorrent"])
            self.logger.warning("qBittorrent config not found, using defaults")
            return qbittorrent_config_from_section({})

        if client_name == "sabnzbd":
            config_parser = config_service.load_config()
            section_name = "sabnzbd"
            dm_downloads_path = get_path_resolver().get_downloads_dir()
            default_local_downloads = dm_downloads_path

            if config_parser.has_section(section_name):
                section = config_parser[section_name]

                def _get(option: str, fallback: str = "") -> str:
                    return section.get(option, fallback)

                def _get_bool(option: str, fallback: bool = False) -> bool:
                    try:
                        return section.getboolean(option, fallback=fallback)
                    except ValueError:
                        return fallback

                def _clean_path(value: Optional[str]) -> str:
                    if not value:
                        return ""
                    return str(value).strip()

                remote_path = _clean_path(
                    _get("remote_path")
                    or _get("download_path_remote")
                    or "/downloads"
                )
                local_path = _clean_path(
                    _get("local_path")
                    or _get("download_path_local")
                    or dm_downloads_path
                )

                return {
                    "enabled":     _get_bool("enabled", False),
                    "host":        _get("host", "localhost"),
                    "port":        int(_get("port", "8080") or 8080),
                    "api_key":     _get("api_key", ""),
                    "use_ssl":     _get_bool("use_ssl", False),
                    "verify_cert": _get_bool("verify_cert", True),
                    "category":    _get("category", ""),
                    "url_base":    _get("url_base", "/sabnzbd/api"),
                    "remote_path": remote_path,
                    "local_path":  local_path,
                }

            self.logger.warning("SABnzbd config not found, using defaults")
            return {
                "enabled": False,
                "host": "localhost",
                "port": 8080,
                "api_key": "",
                "use_ssl": False,
                "verify_cert": True,
                "category": "",
                "url_base": "/sabnzbd/api",
                "remote_path": "/downloads",
                "local_path": default_local_downloads,
            }

        if client_name == "nzbget":
            config_parser = config_service.load_config()
            section_name = "nzbget"

            if config_parser.has_section(section_name):
                section = config_parser[section_name]

                def _get(option: str, fallback: str = "") -> str:
                    return section.get(option, fallback)

                def _get_bool(option: str, fallback: bool = False) -> bool:
                    try:
                        return section.getboolean(option, fallback=fallback)
                    except ValueError:
                        return fallback

                return {
                    "enabled":     _get_bool("enabled", False),
                    "host":        _get("host", "localhost"),
                    "port":        int(_get("port", "6789") or 6789),
                    "username":    _get("username", "nzbget"),
                    "password":    _get("password", "tegbzn6789"),
                    "use_ssl":     _get_bool("use_ssl", False),
                    "verify_cert": _get_bool("verify_cert", True),
                    "category":    _get("category", ""),
                }

            self.logger.warning("NZBGet config not found, using defaults")
            return {
                "enabled": False,
                "host": "localhost",
                "port": 6789,
                "username": "nzbget",
                "password": "tegbzn6789",
                "use_ssl": False,
                "verify_cert": True,
                "category": "",
            }

        return {}

    def _is_qbittorrent_enabled(self) -> bool:
        config = self._load_client_config("qbittorrent")
        return bool(config.get("enabled"))

    def _is_sabnzbd_enabled(self) -> bool:
        config = self._load_client_config("sabnzbd")
        return bool(config.get("enabled"))

    def _is_nzbget_enabled(self) -> bool:
        config = self._load_client_config("nzbget")
        return bool(config.get("enabled"))
