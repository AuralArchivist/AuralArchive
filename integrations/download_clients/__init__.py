"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Download client implementations for torrent workflows with priority-based
    selection and automatic failover.

Location:
    /integrations/download_clients/__init__.py
"""

from .base_torrent_client import BaseTorrentClient, TorrentState
from .qbittorrent_client import QBittorrentClient

__all__ = [
    'BaseTorrentClient',
    'TorrentState',
    'QBittorrentClient'
]
