"""
Module Name: grab.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Grab stage: sends a found release to qBittorrent, SABnzbd or NZBGet, or
    hands Audible downloads to their own workers.

Location:
    /services/acquisition/grab.py
"""

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse
from services.container import container


def _apply_base_override(parsed_url, base_override: str) -> Optional[str]:
    """Rebuild the download URL with a new base host."""

    if not base_override:
        return None

    trimmed = base_override.strip()
    if not trimmed:
        return None

    if not trimmed.startswith(('http://', 'https://')):
        trimmed = f"{parsed_url.scheme or 'http'}://{trimmed}"

    override_parts = urlparse(trimmed)
    netloc = override_parts.netloc or override_parts.path
    if not netloc:
        return None

    base_path = override_parts.path.rstrip('/')
    rewritten_path = f"{base_path}{parsed_url.path}" if parsed_url.path else base_path
    if not rewritten_path.startswith('/'):
        rewritten_path = f"/{rewritten_path}" if rewritten_path else parsed_url.path or '/'

    return urlunparse(
        (
            override_parts.scheme or parsed_url.scheme or 'http',
            netloc,
            rewritten_path,
            parsed_url.params,
            parsed_url.query,
            parsed_url.fragment,
        )
    )


class GrabMixin:
    """Grab stage of the download pipeline. Mixed into DownloadManagementService."""

    def _start_download(self, download_id: int, source_info: Dict[str, Any]):
        """Start the actual download using appropriate client based on download_type."""
        try:
            download = self.queue_manager.get_download(download_id)
            if not download:
                self.logger.error(f"Download {download_id} not found")
                return

            if download.get('next_retry_at'):
                self.queue_manager.update_download(download_id, {'next_retry_at': None})
            
            download_url = source_info.get('download_url') or download.get('download_url')
            download_type = (source_info.get('download_type')
                             or download.get('download_type')
                             or 'torrent')
            download_type = (download_type or '').strip().lower()
            
            self.logger.info(f"Starting {download_type} download for {download_id}")
            
            if download_type == 'audible':
                current_status = (download.get('status') or '').upper()

                if current_status == 'AUDIBLE_DOWNLOADING':
                    self.logger.debug(
                        "Audible download %s already running via dedicated pipeline",
                        download_id
                    )
                    return

                if self.state_machine.transition(download_id, 'AUDIBLE_DOWNLOADING'):
                    self.event_emitter.emit_download_started(download_id)
                    refreshed = self.queue_manager.get_download(download_id) or download
                    self._schedule_audible_download(download_id, refreshed)
                else:
                    self.logger.debug(
                        "Audible download %s could not transition to AUDIBLE_DOWNLOADING (status=%s)",
                        download_id,
                        current_status or 'UNKNOWN'
                    )
                return
            elif download_type in ('torrent', 'nzb'):
                if not download_url:
                    error_msg = "Download source missing download_url; retrying search"
                    self.logger.error(error_msg)
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='SEARCH_FAILED',
                        error_message=error_msg
                    )
                    return

                try:
                    normalized_url = self._normalize_download_url(download_url, allow_localhost=True)
                except RuntimeError as rewrite_error:
                    error_msg = str(rewrite_error)
                    self.logger.error(error_msg)
                    self.queue_manager.update_download(download_id, {'last_error': error_msg})
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='DOWNLOAD_FAILED',
                        error_message=error_msg
                    )
                    return

                if normalized_url != download_url:
                    self.logger.debug(
                        "Rewriting download URL for download %s: %s -> %s",
                        download_id,
                        download_url,
                        normalized_url,
                    )
                    download_url = normalized_url
                    self.queue_manager.update_download(download_id, {'download_url': download_url})

                info_hash = source_info.get('info_hash') or download.get('info_hash')
                self._start_torrent_download(
                    download_id,
                    download,
                    download_url,
                    download_type,
                    info_hash,
                )
            else:
                error_msg = f"Unknown download_type: {download_type}"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=error_msg
                )
                
        except Exception as e:
            self.logger.exception("Error starting download %s", download_id)
            refreshed = self.queue_manager.get_download(download_id)
            already_sent = refreshed and refreshed.get('download_client_id')
            if not already_sent:
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=str(e)
                )
            else:
                self.logger.warning(
                    "Download %s already has a client job (%s); skipping retry after exception",
                    download_id, refreshed.get('download_client_id'),
                )

    def _start_torrent_download(
        self,
        download_id: int,
        download: Dict[str, Any],
        download_url: str,
        download_type: str,
        info_hash: Optional[str] = None,
    ):
        """Download from torrent/NZB indexers using download clients."""
        try:
            if download_type == 'nzb':
                self._start_nzb_download(download_id, download, download_url)
                return

            client_name = 'qbittorrent'
            client = self.client_selector.get_client(client_name)
            if not client:
                error_msg = f"No {download_type} client available"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=error_msg
                )
                return
            
            config_service = container.get_config_service()
            category = config_service.get_config_value('qbittorrent', 'category', fallback='audiobooks')
            
            download_dir = self._build_download_directory_path(download_id, download)
            os.makedirs(download_dir, exist_ok=True)

            remote_save_path = self._map_local_to_remote(download_dir)
            if remote_save_path:
                self.logger.debug(
                    "Mapped local download directory '%s' to remote save path '%s' for qBittorrent",
                    download_dir,
                    remote_save_path,
                )
            else:
                self.logger.debug(
                    "No remote mapping for download directory '%s'; using local path for qBittorrent",
                    download_dir,
                )

            save_path_for_client = remote_save_path or download_dir
            
            if not download_url or not isinstance(download_url, str):
                error_msg = "Torrent source missing download URL"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=error_msg,
                )
                return

            trimmed_url = download_url.strip()
            torrent_payload: Any
            lower_url = trimmed_url.lower()

            if lower_url.startswith("magnet:"):
                if self._is_direct_provider_download(download, trimmed_url):
                    torrent_payload = trimmed_url
                    self.logger.warning(
                        "Using direct-provider magnet fallback for download %s",
                        download_id,
                    )
                else:
                    error_msg = (
                        "Magnet sources are disabled in strict torrent-file mode; "
                        "provider must return a .torrent file"
                    )
                    self.logger.error(error_msg)
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='DOWNLOAD_FAILED',
                        error_message=error_msg,
                    )
                    return

            elif lower_url.startswith(("http://", "https://")):
                try:
                    fetched_payload, fetched_type = self._fetch_remote_torrent(trimmed_url, download)
                except PermissionError as exc:
                    error_msg = str(exc) or "Direct provider rejected download session"
                    self.logger.error(error_msg)
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='DOWNLOAD_FAILED',
                        error_message=error_msg,
                    )
                    return

                if fetched_type == "bytes" and fetched_payload:
                    torrent_payload = fetched_payload
                    self.logger.debug("Using server-fetched torrent payload for download %s", download_id)
                elif fetched_type == "magnet" and fetched_payload and self._is_direct_provider_download(download, trimmed_url):
                    torrent_payload = fetched_payload
                    self.logger.warning(
                        "Using direct-provider magnet fallback for download %s after HTML resolution",
                        download_id,
                    )
                else:
                    if fetched_type == "magnet":
                        error_msg = (
                            "Provider resolved to magnet instead of .torrent file in strict mode"
                        )
                    else:
                        error_msg = (
                            f"Failed to fetch a valid .torrent payload from {download_url}"
                        )
                    self.logger.error(error_msg)
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='DOWNLOAD_FAILED',
                        error_message=error_msg,
                    )
                    return
            else:
                parsed_candidate = urlparse(trimmed_url)
                has_uri_scheme = bool(parsed_candidate.scheme and parsed_candidate.scheme != 'file')

                if has_uri_scheme:
                    is_local_torrent = False
                else:
                    try:
                        local_path = Path(trimmed_url)
                        is_local_torrent = (
                            local_path.exists()
                            and local_path.is_file()
                            and local_path.suffix.lower() == ".torrent"
                        )
                    except OSError:
                        is_local_torrent = False

                if is_local_torrent:
                    torrent_payload = trimmed_url
                else:
                    error_msg = (
                        "Strict torrent-file mode requires an HTTP(S) URL that resolves to a .torrent "
                        "or a local .torrent file path"
                    )
                    self.logger.error(error_msg)
                    self.retry_handler.handle_failure(
                        download_id=download_id,
                        failure_status='DOWNLOAD_FAILED',
                        error_message=error_msg,
                    )
                    return

            add_result = client.add_torrent(
                torrent_data=torrent_payload,
                save_path=save_path_for_client,
                category=category,
                paused=False,
                auto_tmm=False,
                expected_hash=info_hash,
            )

            
            if not add_result.get('success'):
                error_msg = add_result.get('error', 'Failed to add download to client')
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=error_msg
                )
                return
            
            torrent_hash = add_result.get('hash')

            if torrent_hash:
                try:
                    client.get_status(torrent_hash)
                except Exception as hash_lookup_error:
                    self.logger.warning(
                        "qBittorrent could not resolve returned torrent hash '%s' for download %s: %s. "
                        "Attempting discovery fallback.",
                        torrent_hash,
                        download_id,
                        hash_lookup_error,
                    )
                    torrent_hash = None

            if torrent_hash:
                actual_dir = self._detect_actual_save_path(client, torrent_hash)
                if actual_dir and os.path.isdir(actual_dir):
                    requested_dir = os.path.abspath(download_dir)
                    actual_dir_abs = os.path.abspath(actual_dir)
                    if actual_dir_abs != requested_dir:
                        self.logger.warning(
                            "qBittorrent stored download %s in '%s' instead of requested path '%s'. "
                            "Check category defaults or Auto TMM settings if this is unintended.",
                            download_id,
                            actual_dir_abs,
                            requested_dir,
                        )
                        relocation_target = save_path_for_client
                        if relocation_target and hasattr(client, 'set_location'):
                            if client.set_location(torrent_hash, relocation_target):
                                self.logger.info(
                                    "Requested relocation of download %s back to %s via qBittorrent",
                                    download_id,
                                    relocation_target,
                                )
                                updated_dir = self._detect_actual_save_path(client, torrent_hash)
                                if updated_dir and os.path.isdir(updated_dir):
                                    download_dir = updated_dir
                            else:
                                self.logger.debug(
                                    "qBittorrent refused relocation for download %s",
                                    download_id,
                                )
                        download_dir = actual_dir_abs

            if not torrent_hash:
                book_title = (download.get('book_title') or download.get('title') or '').lower()

                for _attempt in range(3):
                    time.sleep(0.3)
                    try:
                        torrents = client.get_all_torrents()
                        torrent_hash = self._select_torrent_hash_candidate(
                            torrents=torrents,
                            download_dir=download_dir,
                            remote_download_dir=remote_save_path,
                            title_hint=book_title,
                        )
                        if torrent_hash:
                            self.logger.info(
                                "Resolved torrent hash for download %s (attempt %s): %s",
                                download_id,
                                _attempt + 1,
                                torrent_hash,
                            )
                            break
                    except Exception as find_error:
                        self.logger.warning("Could not determine torrent hash (attempt %s): %s", _attempt + 1, find_error)

                try:
                    pass
                except Exception as find_error:
                    self.logger.warning(f"Could not determine torrent hash: {find_error}")

            if not torrent_hash:
                self.logger.warning(f"No torrent hash available for download {download_id} - monitoring may be limited")
            
            update_payload = {
                'download_client': client_name,
                'temp_file_path': download_dir,
                'last_error': None,
                'started_at': datetime.now().isoformat()
            }

            if torrent_hash:
                update_payload['download_client_id'] = torrent_hash
            else:
                self.logger.debug(f"Torrent hash still unresolved for download {download_id}; monitor will retry discovery")

            self.queue_manager.update_download(download_id, update_payload)

            current_status = download.get('status')
            latest_status = current_status
            if current_status != 'DOWNLOADING':
                latest_record = self.queue_manager.get_download(download_id)
                if latest_record:
                    latest_status = latest_record.get('status', current_status)

            if latest_status == 'DOWNLOADING':
                self.logger.debug(f"Download {download_id} already in DOWNLOADING state; skipping transition")
            elif self.state_machine.transition(download_id, 'DOWNLOADING'):
                self.event_emitter.emit_download_started(download_id)
                self.logger.info(f"Download {download_id} started with torrent hash: {torrent_hash}")
            
        except Exception as e:
            self.logger.exception("Error starting download %s", download_id)
            self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=str(e)
                )

    def _start_nzb_download(
        self,
        download_id: int,
        download: Dict[str, Any],
        download_url: str,
    ):
        """Send an NZB URL to SABnzbd or NZBGet."""
        nzb_queued_in_client = False
        try:
            client_name = self.client_selector.select_client('nzb')
            if not client_name:
                error_msg = (
                    "No NZB client available - enable SABnzbd or NZBGet "
                    "in Settings → Download Clients"
                )
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=error_msg,
                )
                return

            client = self.client_selector.get_client(client_name)
            if not client:
                error_msg = f"Could not initialise NZB client: {client_name}"
                self.logger.error(error_msg)
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=error_msg,
                )
                return

            config_service = container.get_config_service()
            category = config_service.get_config_value(client_name, 'category', fallback='') or None
            book_title = (download.get('book_title') or download.get('title') or '').strip()

            add_result = client.add_nzb(
                download_url,
                category=category,
                nzb_name=book_title or None,
            )

            if not add_result.get('success'):
                error_msg = add_result.get('error') or 'NZB client rejected the download'
                self.logger.error(
                    "NZB add failed for download %s via %s: %s",
                    download_id, client_name, error_msg,
                )
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message=error_msg,
                )
                return

            nzb_queued_in_client = True

            job_id = add_result.get('id')
            self.queue_manager.update_download(download_id, {
                'download_client': client_name,
                'download_client_id': str(job_id) if job_id else None,
                'last_error': None,
                'started_at': datetime.now().isoformat(),
            })

            latest_record = self.queue_manager.get_download(download_id)
            latest_status = (latest_record.get('status') if latest_record else None) or ''
            if latest_status.upper() != 'DOWNLOADING':
                if self.state_machine.transition(download_id, 'DOWNLOADING'):
                    self.event_emitter.emit_download_started(download_id)
            self.logger.info(
                "NZB download %s sent to %s (job_id=%s)",
                download_id, client_name, job_id,
            )

        except Exception:
            self.logger.exception("Error starting NZB download %s", download_id)
            if nzb_queued_in_client:
                self.logger.warning(
                    "NZB download %s already queued in client; skipping retry to avoid duplicate submission",
                    download_id,
                )
            else:
                self.retry_handler.handle_failure(
                    download_id=download_id,
                    failure_status='DOWNLOAD_FAILED',
                    error_message='Unexpected error starting NZB download',
                )

    def _normalize_download_url(self, download_url: Optional[str], allow_localhost: bool = False) -> str:
        """Ensure torrent/NZB URLs are reachable by the download client."""

        if not download_url:
            raise RuntimeError("Missing download URL for torrent submission")

        parsed = urlparse(download_url)

        if parsed.scheme and parsed.scheme.lower() == 'magnet':
            return download_url

        if not parsed.scheme:
            raise RuntimeError(f"Download URL missing scheme: {download_url}")

        localhost_hosts = {'localhost', '127.0.0.1', '::1'}
        needs_override = parsed.hostname in localhost_hosts

        base_override = self.jackett_download_base_url
        if not base_override:
            env_override = os.environ.get("JACKETT_DOWNLOAD_BASE_URL")
            if env_override:
                base_override = env_override.strip()

        if needs_override and not base_override:
            if allow_localhost:
                self.logger.debug(
                    "Leaving localhost download URL unchanged because allow_localhost is True",
                )
                return download_url
            raise RuntimeError(
                "Download URL resolves to localhost but no external Jackett base URL is configured. "
                "Set 'download_base_url' under the [jackett] section of config/config.txt or set the "
                "JACKETT_DOWNLOAD_BASE_URL environment variable."
            )

        if needs_override and base_override:
            rewritten = _apply_base_override(parsed, base_override)
            if not rewritten:
                raise RuntimeError(
                    f"Unable to rewrite download URL '{download_url}' using base '{base_override}'."
                )
            return rewritten

        return download_url
