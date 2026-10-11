"""
Module Name: nzbget_client.py
Author: AuralArchivist
Created: February 18, 2026
Last Modified: October 9, 2026
Description:
    NZBGet download client implementation using the NZBGet JSON-RPC API.

Location:
    /integrations/download_clients/nzbget_client.py
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from requests.exceptions import RequestException

from .base_nzb_client import BaseNzbClient, NzbState
from core.logging import get_module_logger
from core import http

_LOGGER = get_module_logger("Service.DownloadClients.NZBGet")


# --- NZBGet status → NzbState ------------------------------------------------

_GROUP_STATE_MAP: Dict[str, NzbState] = {
    "QUEUED":      NzbState.QUEUED,
    "PAUSED":      NzbState.PAUSED,
    "DOWNLOADING": NzbState.DOWNLOADING,
    "FETCHING":    NzbState.DOWNLOADING,
    "PP_QUEUED":   NzbState.EXTRACTING,
    "PP_EXECUTING":NzbState.EXTRACTING,
}

_HISTORY_STATE_MAP: Dict[str, NzbState] = {
    "SUCCESS": NzbState.COMPLETE,
    "FAILURE": NzbState.FAILED,
    "DELETED": NzbState.FAILED,
    "WARNING": NzbState.FAILED,
}


class NZBGetError(RuntimeError):
    """Base NZBGet client error."""


class NZBGetAuthError(NZBGetError):
    """Invalid username/password."""


class NZBGetRequestError(NZBGetError):
    """HTTP or JSON-RPC communication failure."""


_RPC_ID = 0


def _next_id() -> int:
    global _RPC_ID
    _RPC_ID = (_RPC_ID + 1) % 10_000
    return _RPC_ID


class NZBGetClient(BaseNzbClient):
    """NZBGet JSON-RPC client."""

    RPC_PATH = "/jsonrpc"

    def __init__(self, config: Dict[str, Any], *, logger=None):
        super().__init__(config, logger=logger)
        self.logger = logger or _LOGGER
        self.username = config.get("username", "nzbget")
        self.password = config.get("password", "tegbzn6789")
        self.rpc_url = self._build_rpc_url()
        self._server_version: Optional[tuple] = None

    # --- URL / session helpers ---

    def _build_rpc_url(self) -> str:
        scheme = "https" if self.config.get("use_ssl") else "http"
        host   = self.config.get("host", "localhost")
        port   = int(self.config.get("port", 6789))
        return f"{scheme}://{host}:{port}{self.RPC_PATH}"

    def _session_auth(self):
        return (self.username, self.password)

    def _call(self, method: str, *params: Any) -> Any:
        """Execute a JSON-RPC call against NZBGet.

        Raises:
            NZBGetAuthError, NZBGetRequestError on failure.
        """
        payload = {
            "version": "1.1",
            "method": method,
            "params": list(params),
            "id": _next_id(),
        }

        try:
            session = self._session_get()
            resp = session.post(
                self.rpc_url,
                json=payload,
                timeout=self.timeout,
                verify=self.verify_cert,
            )
        except RequestException as exc:
            raise NZBGetRequestError(f"HTTP error calling NZBGet ({method}): {exc}") from exc

        if resp.status_code in (401, 403):
            raise NZBGetAuthError(f"NZBGet rejected credentials (HTTP {resp.status_code})")

        if not resp.ok:
            raise NZBGetRequestError(
                f"NZBGet returned HTTP {resp.status_code} for method={method}"
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise NZBGetRequestError(f"NZBGet returned non-JSON for method={method}: {exc}") from exc

        if data.get("error"):
            err = data["error"]
            raise NZBGetRequestError(
                f"NZBGet RPC error for {method}: {err.get('message', err)}"
            )

        return data.get("result")

    # --- BaseNzbClient abstract methods ---

    def test_connection(self) -> Dict[str, Any]:
        try:
            version_info = self._call("version")
            version = str(version_info) if version_info else "unknown"
            self._server_version = self._parse_version(version)
            self.logger.debug("NZBGet connection OK", extra={"version": version})
            return {"success": True, "version": version, "api_version": None, "error": None}
        except NZBGetAuthError as exc:
            return {"success": False, "version": None, "api_version": None, "error": str(exc)}
        except NZBGetRequestError as exc:
            return {"success": False, "version": None, "api_version": None, "error": str(exc)}

    def add_nzb(
        self,
        nzb_url: str,
        *,
        save_path: Optional[str] = None,
        category: Optional[str] = None,
        paused: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        cat  = category or self.default_category or ""
        name = (kwargs.get("nzb_name") or kwargs.get("name") or nzb_url.rsplit("/", 1)[-1])

        if self._server_version is None:
            try:
                version_raw = self._call("version")
                self._server_version = self._parse_version(str(version_raw or ""))
            except Exception:
                self._server_version = (0, 0)

        try:
            nzb_id = self._append_nzb(name, cat, nzb_url, paused)
        except NZBGetRequestError as exc:
            return {"success": False, "id": None, "name": None, "error": str(exc)}

        if not nzb_id or int(nzb_id) <= 0:
            return {"success": False, "id": None, "name": None, "error": "NZBGet returned empty or error NZB ID"}

        nzb_id_str = str(nzb_id)
        self.logger.info("NZB added to NZBGet", extra={"nzb_id": nzb_id_str, "url": nzb_url})
        return {"success": True, "id": nzb_id_str, "name": name, "error": None}

    def get_status(self, nzb_id: str) -> Dict[str, Any]:
        nzb_int = int(nzb_id)

        slot = self._find_in_groups(nzb_int)
        if slot is not None:
            return slot

        slot = self._find_in_history(nzb_int)
        if slot is not None:
            return slot

        raise ValueError(f"NZB ID not found in NZBGet: {nzb_id}")

    def get_all_downloads(self, filter_state: Optional[str] = None) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        try:
            groups = self._call("listgroups", 0) or []
            for group in groups:
                results.append(self._shape_group(group))
        except NZBGetRequestError as exc:
            self.logger.warning("Failed to fetch NZBGet groups", extra={"error": str(exc)})

        try:
            history = self._call("history", False) or []
            for item in history:
                results.append(self._shape_history(item))
        except NZBGetRequestError as exc:
            self.logger.warning("Failed to fetch NZBGet history", extra={"error": str(exc)})

        if filter_state:
            results = [r for r in results if r["state"].value == filter_state]

        return results

    def pause(self, nzb_id: str) -> bool:
        try:
            return bool(self._call("editqueue", "GroupPause", 0, "", [int(nzb_id)]))
        except NZBGetRequestError as exc:
            self._set_error(str(exc))
            return False

    def resume(self, nzb_id: str) -> bool:
        try:
            return bool(self._call("editqueue", "GroupResume", 0, "", [int(nzb_id)]))
        except NZBGetRequestError as exc:
            self._set_error(str(exc))
            return False

    def remove(self, nzb_id: str, *, delete_files: bool = False) -> bool:
        nzb_int = int(nzb_id)
        command = "GroupDeleteOutput" if delete_files else "GroupDelete"
        try:
            self._call("editqueue", command, 0, "", [nzb_int])
        except NZBGetRequestError:
            pass

        try:
            self._call("editqueue", "HistoryDelete", 0, "", [nzb_int])
        except NZBGetRequestError:
            pass

        return True

    def get_client_info(self) -> Dict[str, Any]:
        try:
            status = self._call("status") or {}
            groups = self._call("listgroups", 0) or []

            downloading = sum(1 for g in groups if g.get("Status") == "DOWNLOADING")
            paused      = sum(1 for g in groups if g.get("Status") == "PAUSED")
            queued      = sum(1 for g in groups if g.get("Status") == "QUEUED")

            version_info = self._call("version")
            version = str(version_info) if version_info else "unknown"

            free_disk = int(status.get("FreeDiskSpaceMB", -1) or -1)
            free_bytes = free_disk * 1024 * 1024 if free_disk >= 0 else -1

            speed = int(status.get("DownloadRate", -1) or -1)

            return {
                "name": "NZBGet",
                "version": version,
                "free_space": free_bytes,
                "download_speed": speed,
                "total_downloads": len(groups),
                "downloading": downloading,
                "paused": paused,
                "queued": queued,
            }
        except NZBGetRequestError as exc:
            self._set_error(str(exc))
            return {
                "name": "NZBGet",
                "version": "unknown",
                "free_space": -1,
                "download_speed": -1,
                "total_downloads": -1,
                "downloading": -1,
                "paused": -1,
                "queued": -1,
            }

    # --- Internal helpers ---

    def _find_in_groups(self, nzb_int: int) -> Optional[Dict[str, Any]]:
        try:
            groups = self._call("listgroups", 0) or []
            for group in groups:
                if group.get("NZBID") == nzb_int:
                    return self._shape_group(group)
        except NZBGetRequestError as exc:
            self.logger.debug("Group lookup failed", extra={"nzb_id": nzb_int, "error": str(exc)})
        return None

    def _find_in_history(self, nzb_int: int) -> Optional[Dict[str, Any]]:
        try:
            history = self._call("history", False) or []
            for item in history:
                if item.get("NZBID") == nzb_int:
                    return self._shape_history(item)
        except NZBGetRequestError as exc:
            self.logger.debug("History lookup failed", extra={"nzb_id": nzb_int, "error": str(exc)})
        return None

    def _shape_group(self, group: Dict[str, Any]) -> Dict[str, Any]:
        raw_status = group.get("Status", "")
        state = _GROUP_STATE_MAP.get(raw_status, NzbState.UNKNOWN)

        size_mb      = float(group.get("FileSizeMB", 0) or 0)
        remaining_mb = float(group.get("RemainingSizeMB", 0) or 0)
        total_bytes  = int(size_mb * 1024 * 1024)
        downloaded   = int((size_mb - remaining_mb) * 1024 * 1024)
        progress     = round((downloaded / total_bytes * 100), 2) if total_bytes > 0 else 0.0

        speed = int(group.get("DownloadRate", -1) or -1)
        eta   = int(group.get("EstimatedRemainingTime", -1) or -1)

        return {
            "id":             str(group.get("NZBID", "")),
            "name":           group.get("NZBName", ""),
            "state":          state,
            "progress":       progress,
            "download_speed": speed,
            "eta":            eta,
            "total_size":     total_bytes if total_bytes > 0 else -1,
            "downloaded":     downloaded,
            "category":       group.get("Category"),
            "error":          None,
        }

    # --- Version-aware submission helpers ---

    @staticmethod
    def _parse_version(version_str: str) -> tuple:
        """Return a (major, minor) tuple from a version string like '21.0'."""
        import re as _re
        m = _re.search(r"(\d+)\.(\d+)", version_str or "")
        if m:
            return (int(m.group(1)), int(m.group(2)))
        m2 = _re.search(r"(\d+)", version_str or "")
        if m2:
            return (int(m2.group(1)), 0)
        return (0, 0)

    def _append_nzb(self, name: str, category: str, nzb_url: str, paused: bool) -> int:
        """
        Submit an NZB URL to NZBGet using the correct API signature for the
        server version.

        NZBGet API history:
          v14+  appendurl(NZBFilename, Category, Priority, AddToTop, AddPaused, URL)
                → returns int NZBID (positive = success, 0/negative = error)
          v13   append(NZBFilename, NZBContent[base64], Category, Priority,
                       AddToTop, AddPaused, PPParameters)
                → same integer return convention
          <v13  append(NZBFilename, Category, Priority, AddToTop, NZBContent[base64])
                → returns True/False
        """
        major, _ = self._server_version or (0, 0)

        if major >= 14:
            result = self._call("appendurl", name, category, 0, False, paused, nzb_url)
            return int(result) if result is not None else 0

        import base64
        try:
            resp = http.get(nzb_url, timeout=30, verify=self.verify_cert)
            resp.raise_for_status()
            nzb_b64 = base64.b64encode(resp.content).decode("ascii")
        except Exception as exc:
            raise NZBGetRequestError(f"Failed to fetch NZB for v13-style append: {exc}") from exc

        if major >= 13:
            result = self._call("append", name, nzb_b64, category, 0, False, paused, "")
            return int(result) if result is not None else 0
        else:
            result = self._call("append", name, category, 0, False, nzb_b64)
            return 1 if result else 0

    def _shape_history(self, item: Dict[str, Any]) -> Dict[str, Any]:
        raw_status = item.get("Status", "")
        state = _HISTORY_STATE_MAP.get(raw_status, NzbState.UNKNOWN)
        size_mb     = float(item.get("FileSizeMB", -1) or -1)
        total_bytes = int(size_mb * 1024 * 1024) if size_mb >= 0 else -1

        fail_msg: Optional[str] = None
        if state == NzbState.FAILED:
            messages = item.get("MessageList") or []
            for msg in reversed(messages):
                if msg.get("Kind") == "ERROR":
                    fail_msg = msg.get("Text")
                    break

        return {
            "id":             str(item.get("NZBID", "")),
            "name":           item.get("NZBName", ""),
            "state":          state,
            "progress":       100.0 if state == NzbState.COMPLETE else 0.0,
            "download_speed": -1,
            "eta":            -1,
            "total_size":     total_bytes,
            "downloaded":     total_bytes if state == NzbState.COMPLETE else -1,
            "category":       item.get("Category"),
            "error":          fail_msg,
        }
