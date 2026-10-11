"""
Module Name: base_nzb_client.py
Author: AuralArchivist
Created: February 18, 2026
Last Modified: October 9, 2026
Description:
    Abstract base class for NZB/Usenet download client implementations.
    Mirrors BaseTorrentClient but with NZB-specific semantics - no seeding
    phase, completion is detected via state rather than progress percentage.

Location:
    /integrations/download_clients/base_nzb_client.py
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from core.http import HttpSession


class NzbState(Enum):
    """Normalised NZB download states across all clients."""
    QUEUED      = "queued"
    DOWNLOADING = "downloading"
    PAUSED      = "paused"
    VERIFYING   = "verifying"
    EXTRACTING  = "extracting"
    COMPLETE    = "complete"
    FAILED      = "failed"
    UNKNOWN     = "unknown"


class BaseNzbClient(ABC):
    """
    Abstract base class for Usenet/NZB download clients.

    All NZB client implementations (SABnzbd, NZBGet) must inherit from this
    class and implement all abstract methods.

    Compared to BaseTorrentClient:
    - Downloads are identified by a string NZO/group ID, not a hash.
    - There is no seeding phase - COMPLETE is the terminal success state.
    - Progress is expressed as a float 0-100 but COMPLETE state is the
      authoritative signal, not progress >= 100.
    """

    client_protocol: str = "nzb"
    DEFAULT_TIMEOUT = 15

    def __init__(self, config: Dict[str, Any], *, logger=None):
        self.config = config
        self.client_type = self.__class__.__name__
        self.connected = False
        self.last_error: Optional[str] = None
        self.logger = logger or get_module_logger("Service.DownloadClients.BaseNzbClient")
        self._session: Optional[HttpSession] = None
        self.timeout = float(config.get("timeout", self.DEFAULT_TIMEOUT))
        self.verify_cert = bool(config.get("verify_cert", True))
        self.default_category = (config.get("category") or "").strip() or None

        self.logger.debug("Initialising NZB client", extra={
            "client_type": self.client_type,
            "host": config.get("host"),
            "port": config.get("port"),
        })

    def _session_get(self) -> HttpSession:
        """The client's HTTP session, created on first use."""
        if self._session is None:
            self._session = HttpSession()
            self._session.auth = self._session_auth()
        return self._session

    def _session_auth(self):
        """HTTP auth for the session; None unless the client needs it."""
        return None

    # --- Abstract interface - every subclass must implement these ---

    def connect(self) -> bool:
        """Test the connection and record the result on the client."""
        try:
            result = self.test_connection()
            self.connected = result["success"]
            if not self.connected:
                self._set_error(result.get("error", "Connection failed"))
            else:
                self._clear_error()
            return self.connected
        except Exception as exc:
            self._set_error(str(exc))
            self.connected = False
            return False

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the connection and return diagnostic info.

        Returns:
            {
                "success": bool,
                "version": str,
                "api_version": str | None,
                "error": str | None,
            }
        """

    @abstractmethod
    def add_nzb(
        self,
        nzb_url: str,
        *,
        save_path: Optional[str] = None,
        category: Optional[str] = None,
        paused: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Submit an NZB URL (or local file path) to the download client.

        Args:
            nzb_url:   URL of the NZB file, or an absolute local path.
            save_path: Optional override for the download destination.
            category:  Optional category / post-processing preset.
            paused:    Add in a paused state.

        Returns:
            {
                "success": bool,
                "id": str,          # client-assigned NZO/group ID
                "name": str | None, # display name if immediately available
                "error": str | None,
            }
        """

    @abstractmethod
    def get_status(self, nzb_id: str) -> Dict[str, Any]:
        """
        Fetch the current status of a single NZB item.

        Args:
            nzb_id: Client-assigned NZO/group ID.

        Returns:
            {
                "id": str,
                "name": str,
                "state": NzbState,
                "progress": float,       # 0-100
                "download_speed": int,   # bytes/sec  (-1 if unknown)
                "eta": int,              # seconds    (-1 if unknown)
                "total_size": int,       # bytes      (-1 if unknown)
                "downloaded": int,       # bytes      (-1 if unknown)
                "category": str | None,
                "error": str | None,     # set when state is FAILED
            }

        Raises:
            ValueError: If nzb_id is not found.
        """

    @abstractmethod
    def get_all_downloads(
        self, filter_state: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch status for all NZB items currently known to the client.

        Args:
            filter_state: Optional NzbState value string to filter by.

        Returns:
            List of status dicts in the same shape as get_status().
        """

    @abstractmethod
    def pause(self, nzb_id: str) -> bool:
        """Pause a queued or downloading NZB item."""

    @abstractmethod
    def resume(self, nzb_id: str) -> bool:
        """Resume a paused NZB item."""

    @abstractmethod
    def remove(self, nzb_id: str, *, delete_files: bool = False) -> bool:
        """
        Remove an NZB item from the client.

        Args:
            nzb_id:       Client-assigned NZO/group ID.
            delete_files: Also delete any downloaded files from disk.
        """

    @abstractmethod
    def get_client_info(self) -> Dict[str, Any]:
        """
        Return aggregate stats about the client itself.

        Returns:
            {
                "name": str,
                "version": str,
                "free_space": int,        # bytes
                "download_speed": int,    # bytes/sec  (global)
                "total_downloads": int,
                "downloading": int,
                "paused": int,
                "queued": int,
            }
        """

    # --- Concrete helpers ---

    def is_connected(self) -> bool:
        return self.connected

    def get_last_error(self) -> Optional[str]:
        return self.last_error

    def _set_error(self, error: str) -> None:
        self.last_error = error
        self.logger.error("NZB client error", extra={
            "client_type": self.client_type,
            "error": error,
        })

    def _clear_error(self) -> None:
        self.last_error = None

    def disconnect(self) -> None:
        self.connected = False
        self.logger.debug("NZB client disconnected", extra={
            "client_type": self.client_type,
        })

    def __repr__(self) -> str:
        return (
            f"{self.client_type}("
            f"host={self.config.get('host')}, "
            f"port={self.config.get('port')})"
        )
