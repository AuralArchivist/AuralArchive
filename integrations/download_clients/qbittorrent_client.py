"""
Module Name: qbittorrent_client.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    qBittorrent client implementation for the download subsystem.

Location:
    /integrations/download_clients/qbittorrent_client.py
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import string
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from requests import Response, Session
from requests.exceptions import RequestException

from .base_torrent_client import BaseTorrentClient, TorrentState

AUTH_MODES = ("api_key", "credentials")
from core.logging import get_module_logger
from core.http import HttpSession

_QB_LOGGER = get_module_logger("Service.DownloadClients.QBittorrent")


class QBittorrentError(RuntimeError):
    """Base qBittorrent client error."""


class QBittorrentAuthError(QBittorrentError):
    """Authentication error raised when login fails."""


class QBittorrentRequestError(QBittorrentError):
    """Raised when an HTTP interaction with qBittorrent fails."""


class QBittorrentClient(BaseTorrentClient):
    """Thin wrapper around the qBittorrent Web API v2."""

    DEFAULT_TIMEOUT = 15
    LOGIN_CACHE_SECONDS = 30
    NEW_TORRENT_POLL_ATTEMPTS = 15
    NEW_TORRENT_POLL_INTERVAL = 1.0

    STATE_MAP: Dict[str, TorrentState] = {
        "pausedDL": TorrentState.PAUSED,
        "pausedUP": TorrentState.SEEDING,
        "stoppedUP": TorrentState.SEEDING,
        "queuedDL": TorrentState.QUEUED,
        "queuedUP": TorrentState.QUEUED,
        "stalledDL": TorrentState.DOWNLOADING,
        "stalledUP": TorrentState.SEEDING,
        "checkingDL": TorrentState.DOWNLOADING,
        "checkingUP": TorrentState.SEEDING,
        "downloading": TorrentState.DOWNLOADING,
        "forcedDL": TorrentState.DOWNLOADING,
        "uploading": TorrentState.SEEDING,
        "forcedUP": TorrentState.SEEDING,
        "metaDL": TorrentState.DOWNLOADING,
        "allocating": TorrentState.DOWNLOADING,
        "moving": TorrentState.DOWNLOADING,
        "missingFiles": TorrentState.ERROR,
        "error": TorrentState.ERROR,
        "checkingResumeData": TorrentState.DOWNLOADING,
        "completed": TorrentState.COMPLETE,
    }

    FILTER_MAP: Dict[str, str] = {
        "all": "all",
        "downloading": "downloading",
        "seeding": "seeding",
        "completed": "completed",
        "paused": "paused",
        "active": "active",
        "inactive": "inactive",
        "stalled": "stalled",
    }

    def __init__(self, config: Dict[str, Any], *, logger=None):
        super().__init__(config, logger=logger)
        self.logger = logger or _QB_LOGGER
        self._session: Optional[Session] = None
        self.timeout = float(config.get("timeout", self.DEFAULT_TIMEOUT))
        self.verify_cert = bool(config.get("verify_cert", True))
        self.default_category = (config.get("category") or "").strip() or None
        self.auth_mode = (config.get("qb_auth_mode") or "credentials").strip()
        self.api_key = (config.get("qb_api_key") or "").strip()
        self.version: Optional[str] = None
        self.api_version: Optional[str] = None
        self.base_url = self._build_base_url()
        self.api_url = f"{self.base_url}/api/v2/"
        self._last_login = 0.0
        self._preferences: Dict[str, Any] = {}
        self._seed_ratio_limit: Optional[float] = None
        self._seed_time_limit_seconds: Optional[int] = None

        self.logger.debug("Initialized QBittorrentClient for %s", self.base_url)

    # --- Connection lifecycle ---

    def connect(self) -> bool:
        if self.connected and self._session:
            return True

        problem = self._auth_config_problem()
        if problem:
            self._set_error(problem)
            return False

        try:
            self._session = self._create_session()
            self._login(force=True)
            self.connected = True
            # Confirm the server accepts this sign-in before reporting success
            self.api_version = self._request_text("app/webapiVersion").strip()
            self.version = self._request_text("app/version").strip()
            self._refresh_preferences()
            self._clear_error()
            self.logger.debug("Authenticated with qBittorrent %s at %s (%s)", self.version, self.base_url, self.auth_mode)
            if self.auth_mode == "credentials":
                self._warn_credentials_deprecated()
            return True
        except QBittorrentError as exc:
            self._teardown_session()
            self._set_error(f"Failed to connect to qBittorrent: {exc}")
            return False
        except Exception as exc:
            self._teardown_session()
            self._set_error(f"Failed to connect to qBittorrent: {exc}")
            return False

    def _auth_config_problem(self) -> Optional[str]:
        """Why the selected sign-in method can't be used, or None."""
        if self.auth_mode not in AUTH_MODES:
            return f"Unknown qBittorrent sign-in method '{self.auth_mode}'. Choose API key or username and password in Settings."
        if self.auth_mode == "api_key" and not self.api_key:
            return "API key sign-in is selected but no API key is saved. Add it in Settings › Download Clients."
        if self.auth_mode == "credentials" and not (self.config.get("username") or "").strip():
            return "Username and password sign-in is selected but no username is saved."
        return None

    _deprecation_logged = False

    def _warn_credentials_deprecated(self) -> None:
        if QBittorrentClient._deprecation_logged:
            return
        QBittorrentClient._deprecation_logged = True
        self.logger.warning(
            "qBittorrent username and password sign-in is deprecated and will be removed in AuralArchive 1.1. "
            "Create an API key in qBittorrent (5.2 or newer) and switch to it in Settings › Download Clients."
        )

    def disconnect(self) -> None:
        self._teardown_session()
        super().disconnect()

    def test_connection(self) -> Dict[str, Any]:
        result = {"success": False, "version": None, "api_version": None, "error": None}
        if not self.connected and not self.connect():
            result["error"] = self.get_last_error()
            return result

        try:
            version = self.version or self._request_text("app/version").strip()
            api_version = self.api_version or self._request_text("app/webapiVersion").strip()
            result.update({"success": True, "version": version, "api_version": api_version,
                           "auth_mode": self.auth_mode})
        except QBittorrentError as exc:
            result["error"] = str(exc)
            self._set_error(f"Connection test failed: {exc}")
        return result

    # --- Public API surface ---

    def add_torrent(
        self,
        torrent_data: Any,
        save_path: Optional[str] = None,
        category: Optional[str] = None,
        paused: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not self.connected and not self.connect():
            return {"success": False, "error": self.get_last_error()}

        explicit_hash = kwargs.pop("expected_hash", None)
        expected_hash = self._normalize_info_hash(explicit_hash) or self._derive_info_hash(torrent_data)
        known_hashes = self._get_existing_hashes()
        if expected_hash and expected_hash in known_hashes:
            self.logger.info("Torrent %s already present in qBittorrent", expected_hash)
            return {
                "success": True,
                "hash": expected_hash,
                "duplicate": True,
                "message": "Torrent already existed in qBittorrent",
            }

        payload = self._build_add_payload(save_path, category, paused, kwargs)

        try:
            response = self._submit_torrent(torrent_data, payload)
            self._validate_add_response(response)
            new_hash = self._wait_for_new_torrent(known_hashes, expected_hash)
            if new_hash:
                return {"success": True, "hash": new_hash}

            duplicate_hash = self._resolve_existing_torrent(expected_hash, save_path)
            if duplicate_hash:
                return {
                    "success": True,
                    "hash": duplicate_hash,
                    "duplicate": True,
                    "message": "Torrent already existed in qBittorrent",
                }

            return {
                "success": True,
                "hash": None,
                "message": "Torrent accepted by qBittorrent; hash pending",
                "pending_hash": True,
            }
        except QBittorrentRequestError as exc:
            duplicate_hash = None
            if "duplicate" in str(exc).lower():
                duplicate_hash = expected_hash or self._find_torrent_by_save_path(save_path)
            elif expected_hash and self._hash_exists(expected_hash):
                duplicate_hash = expected_hash

            if duplicate_hash:
                self.logger.info("qBittorrent reported duplicate torrent %s", duplicate_hash)
                return {
                    "success": True,
                    "hash": duplicate_hash,
                    "duplicate": True,
                    "message": "Torrent already existed in qBittorrent",
                }

            self._set_error(f"Failed to add torrent: {exc}")
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            self._set_error(f"Failed to add torrent: {exc}")
            return {"success": False, "error": str(exc)}

    def get_status(self, torrent_hash: str) -> Dict[str, Any]:
        if not torrent_hash:
            raise ValueError("torrent_hash is required")

        resolved = self._resolve_torrent_by_identifier(torrent_hash)
        if not resolved:
            raise ValueError(f"Torrent {torrent_hash} not found")
        return self._build_torrent_record(resolved)

    def get_all_torrents(self, filter_state: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Optional[Dict[str, Any]] = None
        if filter_state:
            mapped = self.FILTER_MAP.get(filter_state.lower(), filter_state)
            params = {"filter": mapped}

        torrents = self._request_json("torrents/info", params=params)
        return [self._build_torrent_record(item) for item in torrents]

    def pause(self, torrent_hash: str) -> bool:
        return self._torrent_action(("torrents/pause", "torrents/stop"), torrent_hash)

    def resume(self, torrent_hash: str) -> bool:
        return self._torrent_action(("torrents/resume", "torrents/start"), torrent_hash)

    def remove(self, torrent_hash: str, delete_files: bool = False) -> bool:
        resolved_hash = self._resolve_action_hash(torrent_hash)
        if not resolved_hash:
            self._set_error(f"Torrent {torrent_hash} not found")
            return False

        data = {"hashes": resolved_hash, "deleteFiles": "true" if delete_files else "false"}
        try:
            self._request("POST", "torrents/delete", data=data)
            return True
        except QBittorrentError as exc:
            self._set_error(f"Failed to remove torrent {torrent_hash}: {exc}")
            return False

    def set_location(self, torrent_hash: str, location: str) -> bool:
        """Relocate an existing torrent to the requested save path."""

        if not torrent_hash or not location:
            return False

        resolved_hash = self._resolve_action_hash(torrent_hash)
        if not resolved_hash:
            self.logger.warning("Cannot set location because torrent %s was not found", torrent_hash)
            return False

        data = {"hashes": resolved_hash, "location": location}
        try:
            self._request("POST", "torrents/setLocation", data=data)
            return True
        except QBittorrentError as exc:
            self.logger.warning(
                "Failed to set qBittorrent save path for %s to %s: %s",
                torrent_hash,
                location,
                exc,
            )
            return False

    def get_client_info(self) -> Dict[str, Any]:
        if not self.connected and not self.connect():
            return {}

        info: Dict[str, Any] = {}
        try:
            version = self._request_text("app/version").strip()
            api_version = self._request_text("app/webapiVersion").strip()
            transfer = self._request_json("transfer/info") or {}
            torrents = self.get_all_torrents()
            info = {
                "name": "qBittorrent",
                "version": version,
                "api_version": api_version,
                "download_speed": transfer.get("dlspeed", 0),
                "upload_speed": transfer.get("upspeed", 0),
                "free_space": transfer.get("free_space_on_disk", 0),
                "total_torrents": len(torrents),
                "downloading": sum(1 for t in torrents if t.get("state_enum") == TorrentState.DOWNLOADING),
                "seeding": sum(1 for t in torrents if t.get("state_enum") == TorrentState.SEEDING),
                "paused": sum(1 for t in torrents if t.get("state_enum") == TorrentState.PAUSED),
            }
        except QBittorrentError as exc:
            self._set_error(f"Failed to fetch client info: {exc}")
        return info

    # --- Internal helpers ---

    def _create_session(self) -> HttpSession:
        headers: Dict[str, str] = {
            "User-Agent": "AuralArchive-QBittorrentClient/1.0",
            "Accept": "application/json, text/plain, */*",
        }
        if self.auth_mode == "api_key" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        session = HttpSession(headers=headers)
        session.verify = self.verify_cert
        return session

    def _teardown_session(self) -> None:
        if self._session is None:
            return

        if self.auth_mode != "api_key":
            try:
                self._session.get(f"{self.api_url}auth/logout", timeout=self.timeout)
            except RequestException:
                pass

        self._session.close()
        self._session = None
        self.connected = False

    def _login(self, force: bool = False) -> None:
        if self.auth_mode == "api_key":
            return

        if not self._session:
            raise QBittorrentError("Session not initialised")

        now = time.time()
        if not force and now - self._last_login < self.LOGIN_CACHE_SECONDS:
            return

        payload = {
            "username": self.config.get("username", ""),
            "password": self.config.get("password", ""),
        }

        response = self._session.post(
            f"{self.api_url}auth/login",
            data=payload,
            timeout=self.timeout,
            allow_redirects=False,
        )

        body = response.text.strip().lower()
        if response.status_code == 204 or (response.status_code == 200 and body in {"ok", "ok."}):
            self._last_login = now
            return

        raise QBittorrentAuthError(
            f"Login failed: {response.status_code} {response.text.strip()}"
        )

    def _ensure_connected(self) -> None:
        if self.connected and self._session:
            return
        if not self.connect():
            raise QBittorrentError(self.get_last_error() or "Unable to connect to qBittorrent")

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> Response:
        self._ensure_connected()
        assert self._session

        url = f"{self.api_url}{endpoint}"
        try:
            response = self._session.request(method, url, timeout=self.timeout, **kwargs)
        except RequestException as exc:
            raise QBittorrentRequestError(f"HTTP {method} {endpoint} failed: {exc}") from exc

        if response.status_code in (401, 403):
            if self.auth_mode == "api_key":
                raise QBittorrentAuthError(
                    f"qBittorrent rejected the API key ({response.status_code}). Check the key, and that qBittorrent "
                    "is 5.2.0 or newer (older versions have no API keys)."
                )
            self.logger.debug("Session cookie expired, re-authenticating")
            self._login(force=True)
            try:
                response = self._session.request(method, url, timeout=self.timeout, **kwargs)
            except RequestException as exc:
                raise QBittorrentRequestError(f"HTTP {method} {endpoint} failed: {exc}") from exc

        try:
            response.raise_for_status()
        except RequestException as exc:
            response_body = (response.text or "").strip()
            if len(response_body) > 300:
                response_body = response_body[:300] + "..."
            if response_body:
                raise QBittorrentRequestError(
                    f"HTTP {method} {endpoint} failed: {exc} (response: {response_body})"
                ) from exc
            raise QBittorrentRequestError(f"HTTP {method} {endpoint} failed: {exc}") from exc

        return response

    def _request_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = self._request("GET", endpoint, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise QBittorrentRequestError(f"Invalid JSON response from {endpoint}: {exc}") from exc

    def _request_text(self, endpoint: str) -> str:
        response = self._request("GET", endpoint)
        return response.text

    def _refresh_preferences(self) -> None:
        if not self._session:
            return
        try:
            prefs = self._request_json("app/preferences") or {}
            self._preferences = prefs
            self._seed_ratio_limit = self._extract_ratio_limit(prefs)
            self._seed_time_limit_seconds = self._extract_time_limit_seconds(prefs)
            self.logger.debug(
                "Loaded qBittorrent preferences: ratio_limit=%s, time_limit=%s",
                self._seed_ratio_limit,
                self._seed_time_limit_seconds,
            )
        except QBittorrentError as exc:
            self.logger.warning("Unable to load qBittorrent preferences: %s", exc)
            if self._seed_ratio_limit is None and self._seed_time_limit_seconds is None:
                self._seed_ratio_limit = 2.0
                self._seed_time_limit_seconds = 168 * 3600

    def _extract_ratio_limit(self, prefs: Dict[str, Any]) -> Optional[float]:
        if not isinstance(prefs, dict) or not prefs.get("max_ratio_enabled"):
            return None
        try:
            value = float(prefs.get("max_ratio", 0))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _extract_time_limit_seconds(self, prefs: Dict[str, Any]) -> Optional[int]:
        if not isinstance(prefs, dict) or not prefs.get("max_seeding_time_enabled"):
            return None
        try:
            minutes = int(prefs.get("max_seeding_time", 0))
        except (TypeError, ValueError):
            return None
        seconds = minutes * 60
        return seconds if seconds > 0 else None

    def _get_torrent_ratio_limit(self, torrent_data: Dict[str, Any]) -> Optional[float]:
        for raw_value in (torrent_data.get("ratio_limit"), torrent_data.get("max_ratio")):
            limit = self._interpret_ratio_limit(raw_value)
            if limit is not None:
                return limit
        return self._seed_ratio_limit

    def _get_torrent_time_limit(self, torrent_data: Dict[str, Any]) -> Optional[int]:
        primary_limit = self._interpret_time_limit_seconds(
            self._minutes_to_seconds(torrent_data.get("max_seeding_time"))
        )
        if primary_limit is not None:
            return primary_limit

        secondary_raw = torrent_data.get("seeding_time_limit")
        seconds_value = self._minutes_to_seconds(secondary_raw)
        secondary_limit = self._interpret_time_limit_seconds(seconds_value)
        if secondary_limit is not None:
            return secondary_limit
        return self._seed_time_limit_seconds

    def _interpret_ratio_limit(self, raw_value: Any) -> Optional[float]:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        if value == -1:
            return None
        if value == -2:
            return self._seed_ratio_limit
        if value <= 0:
            return None
        return value

    def _interpret_time_limit_seconds(self, raw_value: Any) -> Optional[int]:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None
        if value == -1:
            return None
        if value == -2:
            return self._seed_time_limit_seconds
        if value <= 0:
            return None
        return value

    def _extract_seeding_time(self, data: Dict[str, Any]) -> int:
        """Prefer client-reported seeding_time; fall back to time_active."""
        candidates = [data.get("seeding_time"), data.get("time_active"), 0]
        for candidate in candidates:
            try:
                seconds = int(candidate)
            except (TypeError, ValueError):
                continue
            if seconds < 0:
                continue
            return seconds
        return 0

    @staticmethod
    def _minutes_to_seconds(raw_value: Any) -> Any:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return raw_value
        if value > 0:
            return value * 60
        return value

    def _build_base_url(self) -> str:
        host = str(self.config.get("host", "localhost")).strip()
        port = self.config.get("port")
        scheme = "https" if self.config.get("use_ssl", False) else "http"

        if host.startswith(("http://", "https://")):
            parsed = urlparse(host)
            base = f"{parsed.scheme}://{parsed.netloc or parsed.path}"
            if parsed.path and parsed.path not in {"", "/"}:
                base = f"{base}{parsed.path.rstrip('/')}"
        else:
            if port and ":" not in host:
                base = f"{scheme}://{host}:{port}"
            else:
                base = f"{scheme}://{host}"

        extra = (
            self.config.get("webui_path")
            or self.config.get("base_path")
            or self.config.get("path")
            or ""
        )
        if extra:
            base = f"{base}/{str(extra).strip('/')}"
        return base.rstrip("/")

    def _build_add_payload(
        self,
        save_path: Optional[str],
        category: Optional[str],
        paused: bool,
        extra: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"paused": "true" if paused else "false"}

        final_category = category or self.default_category
        if final_category:
            payload["category"] = final_category
        if save_path:
            payload["savepath"] = save_path

        if extra.get("tags"):
            payload["tags"] = extra["tags"]
        if extra.get("skip_hash_check") is True:
            payload["skip_checking"] = "true"
        if extra.get("sequential") is True:
            payload["sequentialDownload"] = "true"
        if extra.get("first_last_priority") is True:
            payload["firstLastPiecePrio"] = "true"
        if extra.get("auto_tmm") is not None:
            payload["autoTMM"] = "true" if extra["auto_tmm"] else "false"
        if extra.get("seed_ratio") is not None:
            payload["ratioLimit"] = extra["seed_ratio"]
        if extra.get("seed_time") is not None:
            payload["seedingTimeLimit"] = extra["seed_time"]
        if extra.get("dl_limit") is not None:
            payload["downloadLimit"] = extra["dl_limit"]
        if extra.get("up_limit") is not None:
            payload["uploadLimit"] = extra["up_limit"]

        return payload

    @staticmethod
    def _as_multipart(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a flat dict into a requests-compatible multipart fields dict.

        Passing ``files=`` with ``(None, value)`` tuples forces requests to use
        ``multipart/form-data`` encoding for all fields, which is what the
        qBittorrent ``torrents/add`` endpoint requires even for URL submissions.
        Plain ``data=`` sends ``application/x-www-form-urlencoded``, which
        qBittorrent rejects with HTTP 415.
        """
        return {k: (None, str(v)) for k, v in payload.items()}

    def _submit_torrent(self, torrent_data: Any, payload: Dict[str, Any]) -> Response:
        endpoint = "torrents/add"

        if isinstance(torrent_data, str):
            trimmed = torrent_data.strip()
            if trimmed.startswith("magnet:") or trimmed.lower().startswith("http"):
                payload["urls"] = trimmed
                return self._request("POST", endpoint, files=self._as_multipart(payload))

            path = Path(trimmed)
            if path.exists():
                with path.open("rb") as handle:
                    files = {**self._as_multipart(payload), "torrents": (path.name, handle, "application/x-bittorrent")}
                    return self._request("POST", endpoint, files=files)

            raise ValueError("Invalid torrent data string provided")

        if isinstance(torrent_data, (bytes, bytearray)):
            files = {**self._as_multipart(payload), "torrents": ("upload.torrent", torrent_data, "application/x-bittorrent")}
            return self._request("POST", endpoint, files=files)

        raise ValueError("Unsupported torrent_data type")

    @staticmethod
    def _validate_add_response(response: Response) -> None:
        text = (response.text or "").strip().lower()
        if response.status_code != 200 or text not in {"ok", "ok."}:
            raise QBittorrentRequestError(
                f"qBittorrent returned {response.status_code}: {response.text.strip()}"
            )

    def _get_existing_hashes(self) -> set[str]:
        try:
            torrents = self.get_all_torrents()
        except Exception:
            self.logger.debug("Could not list torrents before adding", exc_info=True)
            return set()
        return {t.get("hash") for t in torrents if t.get("hash")}

    def _wait_for_new_torrent(
        self,
        existing_hashes: Sequence[str],
        expected_hash: Optional[str] = None,
    ) -> Optional[str]:
        known = {str(value).lower() for value in existing_hashes if value}
        normalized_expected = self._normalize_info_hash(expected_hash)
        for _ in range(self.NEW_TORRENT_POLL_ATTEMPTS):
            time.sleep(self.NEW_TORRENT_POLL_INTERVAL)
            try:
                torrents = self.get_all_torrents()
            except Exception:
                self.logger.debug("Torrent list poll failed", exc_info=True)
                continue
            for torrent in torrents:
                torrent_hash = torrent.get("hash")
                if not torrent_hash:
                    continue
                normalized = str(torrent_hash).lower()
                if normalized_expected and normalized == normalized_expected:
                    return torrent_hash
                if normalized not in known:
                    return torrent_hash
        return None

    def _torrent_action(self, endpoints: Sequence[str], torrent_hash: str) -> bool:
        resolved_hash = self._resolve_action_hash(torrent_hash)
        if not resolved_hash:
            self._set_error(f"Torrent {torrent_hash} not found")
            return False

        last_error: Optional[Exception] = None
        for endpoint in endpoints:
            try:
                self._request("POST", endpoint, data={"hashes": resolved_hash})
                return True
            except QBittorrentError as exc:
                last_error = exc
                continue

        if last_error:
            self._set_error(str(last_error))
        return False

    def _resolve_action_hash(self, torrent_hash: str) -> Optional[str]:
        """Resolve user-provided torrent ID to qBittorrent's primary hash field."""
        if not torrent_hash:
            return None

        candidate = str(torrent_hash).strip()
        if not candidate:
            return None

        try:
            torrents = self._request_json("torrents/info", params={"hashes": candidate}) or []
            if torrents:
                primary = (torrents[0].get("hash") or "").strip()
                if primary:
                    return primary
        except QBittorrentError:
            pass

        resolved = self._resolve_torrent_by_identifier(candidate)
        if not resolved:
            return None

        primary = (resolved.get("hash") or "").strip()
        return primary or None

    def _resolve_torrent_by_identifier(self, torrent_hash: str) -> Optional[Dict[str, Any]]:
        """Find a torrent by primary hash or alternate v1/v2 info-hash fields."""
        identifier = str(torrent_hash or "").strip().lower()
        if not identifier:
            return None

        try:
            torrents = self._request_json("torrents/info", params={"hashes": torrent_hash}) or []
            if torrents:
                return torrents[0]
        except QBittorrentError:
            pass

        try:
            torrents = self._request_json("torrents/info") or []
        except QBittorrentError:
            return None

        for torrent in torrents:
            candidates = {
                str(torrent.get("hash") or "").strip().lower(),
                str(torrent.get("infohash_v1") or "").strip().lower(),
                str(torrent.get("infohash_v2") or "").strip().lower(),
            }
            candidates.discard("")
            if identifier in candidates:
                return torrent

        return None

    def _resolve_existing_torrent(
        self, expected_hash: Optional[str], save_path: Optional[str]
    ) -> Optional[str]:
        if expected_hash and self._hash_exists(expected_hash):
            return expected_hash
        return self._find_torrent_by_save_path(save_path)

    def _hash_exists(self, info_hash: Optional[str]) -> bool:
        if not info_hash:
            return False
        normalized = self._normalize_info_hash(info_hash)
        if not normalized:
            return False
        return normalized in {value.lower() for value in self._get_existing_hashes() if value}

    def _find_torrent_by_save_path(self, save_path: Optional[str]) -> Optional[str]:
        if not save_path:
            return None
        try:
            torrents = self.get_all_torrents()
        except Exception:
            self.logger.debug("Could not list torrents to match save path", exc_info=True)
            return None
        target = self._normalize_filesystem_path(save_path)
        for torrent in torrents:
            candidate = torrent.get("save_path")
            candidate_hash = torrent.get("hash")
            if not candidate or not candidate_hash:
                continue
            if self._normalize_filesystem_path(candidate) == target:
                return candidate_hash
        return None

    @staticmethod
    def _normalize_filesystem_path(path_value: Optional[str]) -> str:
        if not path_value:
            return ""
        normalized = os.path.normpath(str(path_value).strip())
        return normalized.rstrip("/\\")

    def _derive_info_hash(self, torrent_data: Any) -> Optional[str]:
        if isinstance(torrent_data, str):
            trimmed = torrent_data.strip()
            if trimmed.lower().startswith("magnet:"):
                return self._extract_info_hash_from_string(trimmed)
                
            normalized = self._normalize_info_hash(trimmed)
            if normalized:
                return normalized

            path = Path(trimmed)
            if path.exists() and path.is_file():
                try:
                    return self._extract_info_hash_from_bytes(path.read_bytes())
                except OSError:
                    return None

        if isinstance(torrent_data, (bytes, bytearray)):
            return self._extract_info_hash_from_bytes(bytes(torrent_data))

        return None

    def _extract_info_hash_from_string(self, value: str) -> Optional[str]:
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        if parsed.scheme != "magnet":
            return None
        params = parse_qs(parsed.query)
        for qualifier in params.get("xt", []):
            if not qualifier:
                continue
            lowered = qualifier.lower()
            if lowered.startswith("urn:btih:"):
                raw_hash = qualifier.split(":")[-1]
                return self._normalize_info_hash(raw_hash)
        return None

    def _extract_info_hash_from_bytes(self, data: bytes) -> Optional[str]:
        if not data:
            return None
        info_section = self._extract_info_section_bytes(data)
        if not info_section:
            return None
        return hashlib.sha1(info_section).hexdigest()

    @staticmethod
    def _extract_info_section_bytes(data: bytes) -> Optional[bytes]:
        def parse(index: int) -> tuple[int, Optional[bytes]]:
            if index >= len(data):
                raise ValueError("Unexpected end of bencoded data")
            token = data[index:index + 1]
            if token == b"i":
                end = data.index(b"e", index)
                return end + 1, None
            if token == b"l":
                index += 1
                while data[index:index + 1] != b"e":
                    index, info_bytes = parse(index)
                    if info_bytes is not None:
                        return index, info_bytes
                return index + 1, None
            if token == b"d":
                index += 1
                while data[index:index + 1] != b"e":
                    colon = data.index(b":", index)
                    length = int(data[index:colon])
                    key_start = colon + 1
                    key_end = key_start + length
                    key = data[key_start:key_end]
                    value_start = key_end
                    index, info_bytes = parse(value_start)
                    if key == b"info":
                        return index, data[value_start:index]
                    if info_bytes is not None:
                        return index, info_bytes
                return index + 1, None
            colon = data.index(b":", index)
            length = int(data[index:colon])
            start = colon + 1
            return start + length, None

        try:
            _, info_section = parse(0)
        except (ValueError, RecursionError):
            return None
        return info_section

    @staticmethod
    def _normalize_info_hash(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        trimmed = str(value).strip()
        if not trimmed:
            return None
        candidate = trimmed.lower()
        if len(candidate) == 40 and all(ch in string.hexdigits for ch in candidate):
            return candidate
        try:
            decoded = base64.b32decode(trimmed.upper())
            return decoded.hex()
        except (binascii.Error, ValueError):
            return None

    def _build_torrent_record(self, data: Dict[str, Any]) -> Dict[str, Any]:
        state = str(data.get("state", "unknown"))
        mapped_state = self.STATE_MAP.get(state, TorrentState.UNKNOWN)
        try:
            progress = float(data.get("progress", 0.0)) * 100.0
        except (TypeError, ValueError):
            progress = 0.0

        record = {
            "hash": data.get("hash"),
            "infohash_v1": data.get("infohash_v1"),
            "infohash_v2": data.get("infohash_v2"),
            "name": data.get("name"),
            "state": state,
            "state_enum": mapped_state,
            "progress": progress,
            "download_speed": data.get("dlspeed", 0),
            "upload_speed": data.get("upspeed", 0),
            "eta": data.get("eta", -1),
            "total_size": data.get("size") or data.get("total_size") or 0,
            "downloaded": data.get("downloaded") or data.get("completed") or 0,
            "uploaded": data.get("uploaded", 0),
            "ratio": float(data.get("ratio", 0.0) or 0.0),
            "category": data.get("category"),
            "save_path": data.get("save_path"),
            "added_on": data.get("added_on"),
            "completed_on": data.get("completion_on") or data.get("completed_on"),
            "seeding_time": self._extract_seeding_time(data),
            "num_seeds": data.get("num_seeds"),
            "num_leechs": data.get("num_leechs"),
            "availability": data.get("availability"),
            "message": data.get("msg") or data.get("error"),
            "seed_ratio_limit": self._get_torrent_ratio_limit(data),
            "seed_time_limit_seconds": self._get_torrent_time_limit(data),
        }
        return record

    @staticmethod
    def is_seeding_complete(status: Dict[str, Any]) -> bool:
        """Determine if seeding goals are satisfied for the torrent."""
        state_enum = status.get("state_enum", TorrentState.UNKNOWN)
        state_str = str(status.get("state") or "").strip().lower()
        progress = float(status.get("progress", 0.0) or 0.0)
        ratio = float(status.get("ratio", 0.0) or 0.0)
        seeding_time = int(status.get("seeding_time", 0) or 0)
        seed_ratio_limit = status.get("seed_ratio_limit")
        seed_time_limit = status.get("seed_time_limit_seconds")
        torrent_hash = status.get("hash") or status.get("download_client_id") or "unknown"

        active_seeding_states = {"uploading", "stalledup", "forcedup", "checkingup"}
        queued_states = {"queuedup"}
        paused_states = {"pausedup", "stoppedup"}
        completed_states = {"completed", "complete"}

        def _limit_enabled(value: Any) -> bool:
            return isinstance(value, (int, float)) and value > 0

        if state_enum == TorrentState.ERROR:
            _QB_LOGGER.warning(
                "Torrent %s entered error state '%s' while seeding; treating as complete",
                torrent_hash,
                state_str,
            )
            return True
        if state_str in active_seeding_states or state_str in queued_states:
            return False
        if progress < 99.9 and state_enum not in {TorrentState.COMPLETE}:
            return False
        if state_enum == TorrentState.UNKNOWN:
            return False

        ratio_reached = _limit_enabled(seed_ratio_limit) and ratio >= float(seed_ratio_limit)
        time_reached = _limit_enabled(seed_time_limit) and seeding_time >= int(seed_time_limit)
        specified_goals = _limit_enabled(seed_ratio_limit) or _limit_enabled(seed_time_limit)

        if ratio_reached or time_reached:
            _QB_LOGGER.info(
                "Torrent %s met qBittorrent share goals (ratio %.2f/%s, time %ss/%s)",
                torrent_hash,
                ratio,
                seed_ratio_limit,
                seeding_time,
                seed_time_limit,
            )
            return True

        if state_str in paused_states | completed_states:
            if not specified_goals:
                _QB_LOGGER.info(
                    "Torrent %s reported qBittorrent state '%s' with no seeding goals configured; treating as complete",
                    torrent_hash,
                    state_str,
                )
                return True
            _QB_LOGGER.debug(
                "Torrent %s reported qBittorrent state '%s' but share goals unmet (ratio %.2f/%s, time %ss/%s); continuing to seed",
                torrent_hash,
                state_str,
                ratio,
                seed_ratio_limit,
                seeding_time,
                seed_time_limit,
            )
            return False

        if state_enum == TorrentState.COMPLETE:
            if not specified_goals:
                _QB_LOGGER.info(
                    "Torrent %s in COMPLETE state with no seeding goals; assuming qBittorrent finalized seeding",
                    torrent_hash,
                )
                return True
            _QB_LOGGER.debug(
                "Torrent %s in COMPLETE state but share goals unmet (ratio %.2f/%s, time %ss/%s); waiting",
                torrent_hash,
                ratio,
                seed_ratio_limit,
                seeding_time,
                seed_time_limit,
            )
            return False

        return False

