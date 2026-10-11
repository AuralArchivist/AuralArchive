"""
Module Name: client_paths.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Path translation between this app and the download clients, and
    matching a new torrent to its hash.

Location:
    /services/acquisition/client_paths.py
"""

import os
from typing import Any, Dict, List, Optional

from .path_mapping import join_remote_path, normalize_path, normalize_remote_path, remap_remote_to_local


class ClientPathsMixin:
    """Download client path mapping. Mixed into DownloadManagementService."""

    def _map_local_to_remote(self, local_path: Optional[str]) -> Optional[str]:
        """Translate a local filesystem path to the remote path expected by the client."""

        if not local_path:
            return None

        normalized_local = os.path.abspath(local_path)

        local_root = self.torrent_download_root_local
        remote_root = self.torrent_download_root_remote

        if local_root and remote_root:
            local_root_abs = os.path.abspath(local_root)
            if normalized_local == local_root_abs or normalized_local.startswith(local_root_abs + os.sep):
                relative = os.path.relpath(normalized_local, local_root_abs)
                if relative in {'.', os.curdir}:
                    relative = ''
                return join_remote_path(remote_root, relative)

        for mapping in self.qbittorrent_path_mappings:
            local_base = mapping.get('local')
            remote_base = mapping.get('remote')
            if not local_base or not remote_base:
                continue
            local_base_abs = os.path.abspath(local_base)
            if normalized_local == local_base_abs or normalized_local.startswith(local_base_abs + os.sep):
                relative = os.path.relpath(normalized_local, local_base_abs)
                if relative in {'.', os.curdir}:
                    relative = ''
                return join_remote_path(remote_base, relative)

        return None

    def _remap_sabnzbd_path(self, path: str) -> str:
        """Translate a SABnzbd-reported path to local filesystem path."""
        if not path:
            return path
        try:
            sab_config = self.client_selector.get_client_config('sabnzbd', refresh=True) or {}
            remote_base = sab_config.get('remote_path') or '/downloads'
            local_base = sab_config.get('local_path') or self.downloads_path
            remapped = remap_remote_to_local(path, remote_base, local_base)
            return remapped or path
        except Exception as exc:
            self.logger.debug("SABnzbd path remap skipped", extra={"path": path, "error": str(exc)})

        return path

    def _map_remote_to_local(self, remote_path: Optional[str]) -> Optional[str]:
        """Translate a remote client path back to the local filesystem when possible."""

        if not remote_path:
            return None

        normalized_remote = normalize_remote_path(remote_path)

        for mapping in self.qbittorrent_path_mappings:
            remote_base = mapping.get('remote')
            local_base = mapping.get('local')
            if not remote_base or not local_base:
                continue
            remote_base_norm = normalize_remote_path(remote_base)
            if normalized_remote == remote_base_norm or normalized_remote.startswith(remote_base_norm + '/'):
                suffix = normalized_remote[len(remote_base_norm):].lstrip('/')
                local_base_abs = os.path.abspath(local_base)
                if not suffix:
                    return local_base_abs
                return os.path.join(local_base_abs, suffix.replace('/', os.sep))

        if self.torrent_download_root_remote and self.torrent_download_root_local:
            base_remote_norm = normalize_remote_path(self.torrent_download_root_remote)
            if normalized_remote == base_remote_norm or normalized_remote.startswith(base_remote_norm + '/'):
                suffix = normalized_remote[len(base_remote_norm):].lstrip('/')
                local_base_abs = os.path.abspath(self.torrent_download_root_local)
                if not suffix:
                    return local_base_abs
                return os.path.join(local_base_abs, suffix.replace('/', os.sep))

        return None

    def _build_save_path_candidates(
        self,
        download_dir: Optional[str],
        remote_download_dir: Optional[str],
    ) -> Dict[str, str]:
        """Build deterministic path candidates used to map queue items to qB torrents."""
        candidates = {
            'local': normalize_path(download_dir),
            'remote': normalize_path(remote_download_dir),
            'basename': '',
        }

        local_candidate = candidates['local']
        if local_candidate:
            candidates['basename'] = os.path.basename(local_candidate)

        if not candidates['remote'] and local_candidate:
            mapped_remote = self._map_local_to_remote(download_dir)
            candidates['remote'] = normalize_path(mapped_remote)

        return candidates

    def _select_torrent_hash_candidate(
        self,
        torrents: List[Dict[str, Any]],
        download_dir: Optional[str],
        remote_download_dir: Optional[str],
        title_hint: str,
    ) -> Optional[str]:
        """Resolve torrent hash consistently using save-path first, title second."""
        if not torrents:
            return None

        candidates = self._build_save_path_candidates(download_dir, remote_download_dir)
        local_candidate = candidates['local']
        remote_candidate = candidates['remote']
        basename_candidate = candidates['basename']

        for torrent in torrents:
            candidate_hash = torrent.get('hash')
            if not candidate_hash:
                continue

            save_path = normalize_path(torrent.get('save_path'))
            if not save_path:
                continue

            if local_candidate and save_path == local_candidate:
                return candidate_hash
            if remote_candidate and save_path == remote_candidate:
                return candidate_hash

        if basename_candidate:
            basename_matches: List[str] = []
            suffix = f"/{basename_candidate}"
            for torrent in torrents:
                candidate_hash = torrent.get('hash')
                if not candidate_hash:
                    continue

                save_path = normalize_path(torrent.get('save_path'))
                if save_path and save_path.endswith(suffix):
                    basename_matches.append(candidate_hash)

            if len(basename_matches) == 1:
                return basename_matches[0]

        if title_hint:
            title_matches: List[str] = []
            for torrent in torrents:
                candidate_hash = torrent.get('hash')
                if not candidate_hash:
                    continue

                name = str(torrent.get('name') or '').lower()
                if name and title_hint in name:
                    title_matches.append(candidate_hash)

            if len(title_matches) == 1:
                return title_matches[0]

        return None

    def _detect_actual_save_path(self, client, torrent_hash: str) -> Optional[str]:
        """Ask the torrent client where it stored the download and map to local path."""

        if not torrent_hash or not client:
            return None

        try:
            status = client.get_status(torrent_hash)
        except Exception as exc:
            self.logger.debug("Unable to query qBittorrent save path for %s: %s", torrent_hash, exc)
            return None

        save_path = status.get('save_path') if isinstance(status, dict) else None
        if not save_path:
            return None

        mapped_local = self._map_remote_to_local(save_path)
        if mapped_local and os.path.isdir(mapped_local):
            return mapped_local

        normalized = os.path.abspath(save_path)
        if os.path.isdir(normalized):
            return normalized

        return None

    def _build_download_directory_path(self, download_id: int, download: Dict[str, Any]) -> str:
        """Construct a deterministic, sanitized directory for torrent downloads."""
        base_path = (
            self.torrent_download_root_local
            or self.torrent_download_root
            or self.downloads_path
        )
        os.makedirs(base_path, exist_ok=True)

        raw_title = (
            download.get('book_title')
            or download.get('title')
            or download.get('book_asin')
            or f'download_{download_id}'
        )

        sanitized = ''.join(
            ch if ch.isalnum() or ch in (' ', '-', '_') else '_'
            for ch in raw_title
        ).strip()

        if not sanitized:
            sanitized = f'download_{download_id}'

        normalized = '_'.join(sanitized.split())
        folder_name = f"{normalized[:64]}_{download_id}"

        return os.path.join(base_path, folder_name)
