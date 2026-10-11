"""
Module Name: base_torrent_client.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Abstract base for torrent client implementations and shared interface.

Location:
    /integrations/download_clients/base_torrent_client.py
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from enum import Enum

from core.logging import get_module_logger


class TorrentState(Enum):
    """Standard torrent states across all clients."""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    SEEDING = "seeding"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETE = "complete"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class BaseTorrentClient(ABC):
    """
    Abstract base class for torrent download clients.
    
    All torrent client implementations must inherit from this class
    and implement all abstract methods.
    """
    
    def __init__(self, config: Dict[str, Any], *, logger=None):
        """
        Initialize the torrent client.
        
        Args:
            config: Client configuration dictionary with keys:
                - host: Server hostname/IP
                - port: Server port
                - username: Authentication username
                - password: Authentication password
                - use_ssl: Whether to use HTTPS (optional, default False)
                - verify_cert: Whether to verify SSL certificate (optional, default True)
        """
        self.config = config
        self.client_type = self.__class__.__name__
        self.connected = False
        self.last_error = None
        self.logger = logger or get_module_logger("Service.DownloadClients.BaseTorrentClient")
        
        self.logger.debug("Initializing torrent client", extra={
            "client_type": self.client_type,
            "host": config.get('host'),
            "port": config.get('port')
        })
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to the torrent client.
        
        Returns:
            True if connection successful, False otherwise
            
        Raises:
            ConnectionError: If connection cannot be established
        """
        pass
    
    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """
        Test connection to the client and verify credentials.
        
        Returns:
            Dictionary with:
                - success: bool - Whether connection test passed
                - version: str - Client version if successful
                - api_version: str - API version if available
                - error: str - Error message if failed
        """
        pass
    
    @abstractmethod
    def add_torrent(
        self,
        torrent_data: str,
        save_path: Optional[str] = None,
        category: Optional[str] = None,
        paused: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Add a torrent to the download client.
        
        Args:
            torrent_data: Magnet link, torrent URL, or path to .torrent file
            save_path: Download destination path (optional)
            category: Category to assign (optional)
            paused: Whether to add in paused state (default: False)
            **kwargs: Additional client-specific parameters
            
        Returns:
            Dictionary with:
                - success: bool - Whether torrent was added
                - hash: str - Torrent hash if successful
                - error: str - Error message if failed
                
        Raises:
            ValueError: If torrent_data is invalid
        """
        pass
    
    @abstractmethod
    def get_status(self, torrent_hash: str) -> Dict[str, Any]:
        """
        Get detailed status of a specific torrent.
        
        Args:
            torrent_hash: Hash of the torrent
            
        Returns:
            Dictionary with:
                - hash: str - Torrent hash
                - name: str - Torrent name
                - state: TorrentState - Current state
                - progress: float - Progress percentage (0-100)
                - download_speed: int - Download speed in bytes/sec
                - upload_speed: int - Upload speed in bytes/sec
                - eta: int - Estimated time remaining in seconds (-1 if unknown)
                - total_size: int - Total size in bytes
                - downloaded: int - Downloaded bytes
                - uploaded: int - Uploaded bytes
                - ratio: float - Upload/download ratio
                - error: str - Error message if state is ERROR
                
        Raises:
            ValueError: If torrent not found
        """
        pass
    
    @abstractmethod
    def get_all_torrents(self, filter_state: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get status of all torrents.
        
        Args:
            filter_state: Optional state filter (downloading, seeding, paused, etc.)
            
        Returns:
            List of torrent status dictionaries (same format as get_status())
        """
        pass
    
    @abstractmethod
    def pause(self, torrent_hash: str) -> bool:
        """
        Pause a torrent.
        
        Args:
            torrent_hash: Hash of the torrent to pause
            
        Returns:
            True if paused successfully, False otherwise
            
        Raises:
            ValueError: If torrent not found
        """
        pass
    
    @abstractmethod
    def resume(self, torrent_hash: str) -> bool:
        """
        Resume a paused torrent.
        
        Args:
            torrent_hash: Hash of the torrent to resume
            
        Returns:
            True if resumed successfully, False otherwise
            
        Raises:
            ValueError: If torrent not found
        """
        pass
    
    @abstractmethod
    def remove(self, torrent_hash: str, delete_files: bool = False) -> bool:
        """
        Remove a torrent from the client.
        
        Args:
            torrent_hash: Hash of the torrent to remove
            delete_files: Whether to also delete downloaded files (default: False)
            
        Returns:
            True if removed successfully, False otherwise
            
        Raises:
            ValueError: If torrent not found
        """
        pass
    
    @abstractmethod
    def get_client_info(self) -> Dict[str, Any]:
        """
        Get information about the download client itself.
        
        Returns:
            Dictionary with:
                - name: str - Client name
                - version: str - Client version
                - api_version: str - API version
                - free_space: int - Free disk space in bytes
                - download_speed: int - Global download speed in bytes/sec
                - upload_speed: int - Global upload speed in bytes/sec
                - total_torrents: int - Total number of torrents
                - downloading: int - Number currently downloading
                - seeding: int - Number currently seeding
                - paused: int - Number paused
        """
        pass
    
    def is_connected(self) -> bool:
        """
        Check if client is currently connected.
        
        Returns:
            True if connected, False otherwise
        """
        return self.connected
    
    def get_last_error(self) -> Optional[str]:
        """
        Get the last error that occurred.
        
        Returns:
            Last error message or None
        """
        return self.last_error
    
    def _set_error(self, error: str) -> None:
        """
        Set the last error message.
        
        Args:
            error: Error message to store
        """
        self.last_error = error
        self.logger.error("Torrent client error", extra={
            "client_type": self.client_type,
            "error": error
        })
    
    def _clear_error(self) -> None:
        """Clear the last error message."""
        self.last_error = None
    
    def disconnect(self) -> None:
        """
        Disconnect from the client.
        Subclasses should override this if they need cleanup.
        """
        self.connected = False
        self.logger.debug("Torrent client disconnected", extra={
            "client_type": self.client_type
        })
    
    def __repr__(self) -> str:
        """String representation of the client."""
        return f"{self.client_type}(host={self.config.get('host')}, port={self.config.get('port')})"
