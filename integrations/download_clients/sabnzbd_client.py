"""
Module Name: sabnzbd_client.py
Author: AuralArchivist
Created: February 18, 2026
Last Modified: October 9, 2026
Description:
    SABnzbd download client implementation using the SABnzbd JSON API.

Location:
    /integrations/download_clients/sabnzbd_client.py
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from requests.exceptions import RequestException

from .base_nzb_client import BaseNzbClient, NzbState
from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.DownloadClients.SABnzbd")


# --- SABnzbd queue-slot status → NzbState ------------------------------------

_QUEUE_STATE_MAP: Dict[str, NzbState] = {
    "Downloading": NzbState.DOWNLOADING,
    "Queued":      NzbState.QUEUED,
    "Paused":      NzbState.PAUSED,
    "Grabbing":    NzbState.DOWNLOADING,
    "Fetching":    NzbState.DOWNLOADING,
    "Fetch NZB":   NzbState.QUEUED,
    "Fetch NZB from URL": NzbState.QUEUED,
    "Verifying":   NzbState.VERIFYING,
    "Repairing":   NzbState.VERIFYING,
    "Extracting":  NzbState.EXTRACTING,
    "Moving":      NzbState.EXTRACTING,
    "Running":     NzbState.EXTRACTING,
}

_HISTORY_STATE_MAP: Dict[str, NzbState] = {
    "Completed": NzbState.COMPLETE,
    "Failed":    NzbState.FAILED,
}


class SABnzbdError(RuntimeError):
    """Base SABnzbd client error."""


class SABnzbdAuthError(SABnzbdError):
    """API key rejected or insufficient permissions."""


class SABnzbdRequestError(SABnzbdError):
    """HTTP-level communication failure."""


class SABnzbdClient(BaseNzbClient):
    """SABnzbd JSON API client."""

    URL_BASE = "/api"

    def __init__(self, config: Dict[str, Any], *, logger=None):
        super().__init__(config, logger=logger)
        self.logger = logger or _LOGGER
        self.api_key = config.get("api_key", "")
        self.base_url = self._build_base_url()

    # --- URL / session helpers ---

    def _build_base_url(self) -> str:
        scheme = "https" if self.config.get("use_ssl") else "http"
        host   = self.config.get("host", "localhost")
        port   = int(self.config.get("port", 8080))
        url_base = (self.config.get("url_base") or self.URL_BASE).rstrip("/")
        return f"{scheme}://{host}:{port}{url_base}"

    def _api(self, mode: str, **params: Any) -> Dict[str, Any]:
        """Make a JSON API call to SABnzbd.

        Raises:
            SABnzbdAuthError: On API key rejection.
            SABnzbdRequestError: On HTTP or JSON errors.
        """
        payload: Dict[str, Any] = {
            "output": "json",
            "apikey": self.api_key,
            "mode": mode,
        }
        payload.update({k: v for k, v in params.items() if v is not None})

        try:
            session = self._session_get()
            resp = session.get(
                self.base_url,
                params=payload,
                timeout=self.timeout,
                verify=self.verify_cert,
            )
        except RequestException as exc:
            raise SABnzbdRequestError(f"HTTP error calling SABnzbd ({mode}): {exc}") from exc

        if resp.status_code in (401, 403):
            raise SABnzbdAuthError(f"SABnzbd rejected API key (HTTP {resp.status_code})")

        if not resp.ok:
            raise SABnzbdRequestError(
                f"SABnzbd returned HTTP {resp.status_code} for mode={mode}"
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise SABnzbdRequestError(f"SABnzbd returned non-JSON for mode={mode}: {exc}") from exc

        if isinstance(data, dict) and data.get("status") is False:
            raise SABnzbdRequestError(
                f"SABnzbd reported error for mode={mode}: {data.get('error', 'unknown')}"
            )

        return data

    # --- BaseNzbClient abstract methods ---

    def test_connection(self) -> Dict[str, Any]:
        try:
            data = self._api("version")
            version = data.get("version") or data.get("Version", "unknown")
            self.logger.debug("SABnzbd connection OK", extra={"version": version})
            return {"success": True, "version": version, "api_version": None, "error": None}
        except SABnzbdAuthError as exc:
            return {"success": False, "version": None, "api_version": None, "error": str(exc)}
        except SABnzbdRequestError as exc:
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
        cat = category or self.default_category
        nzb_name = kwargs.get("nzb_name") or kwargs.get("name")

        try:
            resp = self._session_get().get(nzb_url, timeout=30, verify=self.verify_cert)
            resp.raise_for_status()
            nzb_bytes = resp.content
            self.logger.debug(
                "Fetched NZB bytes for upload",
                extra={
                    "url": nzb_url,
                    "content_type": resp.headers.get("Content-Type", ""),
                    "size_bytes": len(nzb_bytes),
                    "preview": nzb_bytes[:120].decode("utf-8", errors="replace"),
                },
            )
        except RequestException as exc:
            return {"success": False, "id": None, "name": None, "error": f"Failed to fetch NZB: {exc}"}

        filename = (nzb_name or "download") + ".nzb"
        payload: Dict[str, Any] = {
            "output": "json",
            "apikey": self.api_key,
            "mode": "addfile",
        }
        if cat:
            payload["cat"] = cat
        if nzb_name:
            payload["nzbname"] = nzb_name
        if paused:
            payload["priority"] = -2

        try:
            session = self._session_get()
            resp = session.post(
                self.base_url,
                params=payload,
                files={"name": (filename, nzb_bytes, "application/x-nzb")},
                timeout=self.timeout,
                verify=self.verify_cert,
            )
        except RequestException as exc:
            return {"success": False, "id": None, "name": None, "error": f"HTTP error calling SABnzbd (addfile): {exc}"}

        if resp.status_code in (401, 403):
            return {"success": False, "id": None, "name": None, "error": "SABnzbd rejected API key"}
        if not resp.ok:
            return {"success": False, "id": None, "name": None, "error": f"SABnzbd returned HTTP {resp.status_code}"}

        try:
            data = resp.json()
        except Exception as exc:
            return {"success": False, "id": None, "name": None, "error": f"SABnzbd returned non-JSON: {exc}"}

        if isinstance(data, dict) and data.get("status") is False:
            return {"success": False, "id": None, "name": None, "error": data.get("error", "SABnzbd rejected the NZB")}

        nzo_ids: List[str] = data.get("nzo_ids") or []
        if not nzo_ids:
            return {"success": False, "id": None, "name": None, "error": "SABnzbd did not return an NZO ID"}

        nzo_id = nzo_ids[0]
        self.logger.info("NZB added to SABnzbd", extra={"nzo_id": nzo_id, "url": nzb_url})
        return {"success": True, "id": nzo_id, "name": nzb_name, "error": None}

    def get_status(self, nzb_id: str) -> Dict[str, Any]:
        slot = self._find_in_queue(nzb_id) or self._find_in_history(nzb_id)
        if slot is None:
            raise ValueError(f"NZO ID not found in SABnzbd: {nzb_id}")
        return slot

    def get_all_downloads(self, filter_state: Optional[str] = None) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        try:
            q_data = self._api("queue")
            for slot in q_data.get("queue", {}).get("slots", []):
                results.append(self._shape_queue_slot(slot))
        except SABnzbdRequestError as exc:
            self.logger.warning("Failed to fetch SABnzbd queue", extra={"error": str(exc)})

        try:
            h_data = self._api("history")
            for slot in h_data.get("history", {}).get("slots", []):
                results.append(self._shape_history_slot(slot))
        except SABnzbdRequestError as exc:
            self.logger.warning("Failed to fetch SABnzbd history", extra={"error": str(exc)})

        if filter_state:
            results = [r for r in results if r["state"].value == filter_state]

        return results

    def pause(self, nzb_id: str) -> bool:
        try:
            self._api("pause_item", value=nzb_id)
            return True
        except SABnzbdRequestError as exc:
            self._set_error(str(exc))
            return False

    def resume(self, nzb_id: str) -> bool:
        try:
            self._api("resume_item", value=nzb_id)
            return True
        except SABnzbdRequestError as exc:
            self._set_error(str(exc))
            return False

    def remove(self, nzb_id: str, *, delete_files: bool = False) -> bool:
        del_flag = "1" if delete_files else "0"
        removed = False

        try:
            self._api("delete", name=nzb_id, del_files=del_flag)
            removed = True
        except SABnzbdRequestError:
            pass

        try:
            self._api("history", name="delete", value=nzb_id, del_files=del_flag)
            removed = True
        except SABnzbdRequestError:
            pass

        if not removed:
            self.logger.warning("Could not remove NZO from SABnzbd", extra={"nzo_id": nzb_id})
        return removed

    def get_client_info(self) -> Dict[str, Any]:
        try:
            q_data = self._api("queue")
            q = q_data.get("queue", {})

            speed_raw = q.get("speed", "0")
            speed_bytes = self._parse_speed(speed_raw)

            free_space_raw = q.get("diskspace1", "0")
            free_gb = float(free_space_raw or 0)
            free_bytes = int(free_gb * 1024 ** 3)

            slots = q.get("slots", [])
            downloading = sum(1 for s in slots if s.get("status") == "Downloading")
            paused_count = sum(1 for s in slots if s.get("status") == "Paused")
            queued_count = sum(1 for s in slots if s.get("status") == "Queued")

            version_data = self._api("version")
            version = version_data.get("version", "unknown")

            return {
                "name": "SABnzbd",
                "version": version,
                "free_space": free_bytes,
                "download_speed": speed_bytes,
                "total_downloads": len(slots),
                "downloading": downloading,
                "paused": paused_count,
                "queued": queued_count,
            }
        except SABnzbdRequestError as exc:
            self._set_error(str(exc))
            return {
                "name": "SABnzbd",
                "version": "unknown",
                "free_space": -1,
                "download_speed": -1,
                "total_downloads": -1,
                "downloading": -1,
                "paused": -1,
                "queued": -1,
            }

    # --- Internal helpers ---

    def _find_in_queue(self, nzb_id: str) -> Optional[Dict[str, Any]]:
        try:
            data = self._api("queue")
            for slot in data.get("queue", {}).get("slots", []):
                if slot.get("nzo_id") == nzb_id:
                    return self._shape_queue_slot(slot)
        except SABnzbdRequestError as exc:
            self.logger.debug("Queue lookup failed", extra={"nzo_id": nzb_id, "error": str(exc)})
        return None

    def _find_in_history(self, nzb_id: str) -> Optional[Dict[str, Any]]:
        try:
            data = self._api("history", value=nzb_id)
            slots = data.get("history", {}).get("slots", [])
            if slots:
                return self._shape_history_slot(slots[0])
        except SABnzbdRequestError as exc:
            self.logger.debug("History lookup failed", extra={"nzo_id": nzb_id, "error": str(exc)})
        return None

    def _shape_queue_slot(self, slot: Dict[str, Any]) -> Dict[str, Any]:
        raw_status = slot.get("status", "")
        state = _QUEUE_STATE_MAP.get(raw_status, NzbState.UNKNOWN)

        mb_total = float(slot.get("mb", 0) or 0)
        mb_left  = float(slot.get("mbleft", 0) or 0)
        total_bytes = int(mb_total * 1024 * 1024)
        downloaded  = int((mb_total - mb_left) * 1024 * 1024)
        progress    = round((downloaded / total_bytes * 100), 2) if total_bytes > 0 else 0.0

        timeleft = slot.get("timeleft", "") or ""
        eta = self._parse_timeleft(timeleft)

        speed_raw = slot.get("speed", "")
        speed_bytes = self._parse_speed(speed_raw) if speed_raw else -1

        return {
            "id":             slot.get("nzo_id", ""),
            "name":           slot.get("filename") or slot.get("name", ""),
            "state":          state,
            "progress":       progress,
            "download_speed": speed_bytes,
            "eta":            eta,
            "total_size":     total_bytes if total_bytes > 0 else -1,
            "downloaded":     downloaded,
            "category":       slot.get("cat"),
            "error":          None,
        }

    def _shape_history_slot(self, slot: Dict[str, Any]) -> Dict[str, Any]:
        raw_status = slot.get("status", "")
        state = _HISTORY_STATE_MAP.get(raw_status, NzbState.UNKNOWN)
        total_bytes = int(slot.get("bytes", -1) or -1)
        fail_msg    = slot.get("fail_message") or None
        storage_path = slot.get("storage") or None

        return {
            "id":             slot.get("nzo_id", ""),
            "name":           slot.get("name", ""),
            "state":          state,
            "progress":       100.0 if state == NzbState.COMPLETE else 0.0,
            "download_speed": -1,
            "eta":            -1,
            "total_size":     total_bytes,
            "downloaded":     total_bytes if state == NzbState.COMPLETE else -1,
            "category":       slot.get("category"),
            "storage_path":   storage_path,
            "error":          fail_msg if state == NzbState.FAILED else None,
        }

    @staticmethod
    def _parse_timeleft(timeleft: str) -> int:
        """Parse SABnzbd HH:MM:SS timeleft string → seconds (-1 on failure)."""
        if not timeleft:
            return -1
        parts = timeleft.split(":")
        try:
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + int(s)
            if len(parts) == 2:
                m, s = parts
                return int(m) * 60 + int(s)
        except (ValueError, TypeError):
            pass
        return -1

    @staticmethod
    def _parse_speed(speed: str) -> int:
        """Parse SABnzbd speed string like '1.2 MB/s' → bytes/sec (-1 on failure)."""
        if not speed:
            return -1
        speed = speed.strip()
        multipliers = {
            "GB/s": 1024 ** 3,
            "MB/s": 1024 ** 2,
            "KB/s": 1024,
            "B/s":  1,
        }
        for suffix, mult in multipliers.items():
            if speed.endswith(suffix):
                try:
                    return int(float(speed.replace(suffix, "").strip()) * mult)
                except ValueError:
                    return -1
        try:
            return int(float(speed))
        except ValueError:
            return -1
