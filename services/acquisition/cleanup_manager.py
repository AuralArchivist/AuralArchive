"""
Module Name: cleanup_manager.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Post-download cleanup, seeding handling and work folder retention.

Location:
    /services/acquisition/cleanup_manager.py
"""

import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text

from core.logging import get_module_logger
from core.paths import get_path_resolver
from services.container import container

_DONE_STATUSES = {'IMPORTED', 'SEEDING', 'SEEDING_COMPLETE', 'CANCELLED'}
_FAILED_STATUSES = {
    'SEARCH_FAILED', 'DOWNLOAD_FAILED', 'AUDIBLE_DOWNLOAD_FAILED',
    'CONVERSION_FAILED', 'IMPORT_FAILED', 'FAILED',
}
_AUDIBLE_DIR_RE = re.compile(r'^\d+$')
_CONVERT_DIR_RE = re.compile(r'^convert_(\d+)$')
_STRAY_FILE_MAX_AGE_SECONDS = 24 * 3600


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


class CleanupManager:
    """Manages file cleanup after download completion."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.DownloadManagement.CleanupManager")
        self._queue_manager = None
        self._client_selector = None
    
    def _get_queue_manager(self):
        """Lazy load QueueManager."""
        if self._queue_manager is None:
            from .queue_manager import QueueManager
            self._queue_manager = QueueManager()
        return self._queue_manager
    
    def _get_client_selector(self):
        """Lazy load ClientSelector."""
        if self._client_selector is None:
            from .client_selector import ClientSelector
            self._client_selector = ClientSelector()
        return self._client_selector

    def set_queue_manager(self, queue_manager):
        """Attach shared QueueManager from DownloadManagementService."""
        self._queue_manager = queue_manager

    def set_client_selector(self, client_selector):
        """Attach shared ClientSelector from DownloadManagementService."""
        self._client_selector = client_selector

    def cleanup_after_import(self, download_id: int, download_data: dict,
                            seeding: bool = False, delete_source: bool = False):
        """Clean up files after successful import."""
        try:
            download_type = download_data.get('download_type', 'torrent')
            self.logger.info("Cleanup start", extra={
                "download_id": download_id,
                "download_type": download_type,
                "seeding": seeding,
                "delete_source": delete_source
            })
            
            if download_type == 'audible':
                self._cleanup_audible_files(download_id, download_data)
            elif download_type == 'torrent':
                if seeding:
                    self.logger.debug("Seeding mode, keeping torrent", extra={
                        "download_id": download_id
                    })
                    self._cleanup_temp_files(download_id, download_data)
                else:
                    if download_data.get('download_client_id'):
                        self.remove_from_client(
                            download_data['download_client'],
                            download_data['download_client_id'],
                            delete_files=delete_source
                        )
                    self._cleanup_temp_files(download_id, download_data)
            else:
                self._cleanup_temp_files(download_id, download_data)
            
            self.logger.info("Cleanup finished", extra={
                "download_id": download_id
            })

        except Exception as e:
            self.logger.exception("Error cleaning up download", extra={
                "download_id": download_id,
                "error": str(e)
            })
    
    def _cleanup_audible_files(self, download_id: int, download_data: dict):
        """Clean up Audible-specific files after successful conversion.

        Deletes:
        - Voucher files (.voucher)
        - Original AAX/AAXC files (after conversion to M4B)
        - Temporary working directory
        """
        try:
            voucher_path = download_data.get('voucher_file_path')
            if voucher_path and os.path.exists(voucher_path):
                try:
                    os.remove(voucher_path)
                    self.logger.debug("Deleted voucher file", extra={
                        "download_id": download_id,
                        "voucher_path": voucher_path
                    })
                except Exception as e:
                    self.logger.warning("Failed to delete voucher", extra={
                        "download_id": download_id,
                        "voucher_path": voucher_path,
                        "error": str(e)
                    })
            
            temp_file = download_data.get('temp_file_path')
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    self.logger.debug("Deleted original Audible file", extra={
                        "download_id": download_id,
                        "temp_file": temp_file
                    })
                except Exception as e:
                    self.logger.warning("Failed to delete temp file", extra={
                        "download_id": download_id,
                        "temp_file": temp_file,
                        "error": str(e)
                    })
            
            self._cleanup_temp_files(download_id, download_data)
            
            self.logger.debug("Completed Audible-specific cleanup", extra={
                "download_id": download_id
            })
            
        except Exception as e:
            self.logger.exception("Error during Audible cleanup", extra={
                "download_id": download_id,
                "error": str(e)
            })
    
    def remove_from_client(self, client_name: str, client_id: str,
                          delete_files: bool = False):
        """Remove download from client.

        The actual client network call runs in a background daemon thread so
        that callers (cancel, clear-queue) are never blocked by a slow or
        unreachable download client.
        """
        def _do_remove():
            try:
                client_selector = self._get_client_selector()
                client = client_selector.get_client(client_name)

                if not client:
                    self.logger.warning("Client not available for removal", extra={
                        "client_name": client_name,
                        "client_id": client_id,
                    })
                    return

                success = client.remove(client_id, delete_files=delete_files)

                if success:
                    self.logger.info("Removed download from client", extra={
                        "client_name": client_name,
                        "client_id": client_id,
                        "delete_files": delete_files,
                    })
                else:
                    self.logger.warning("Failed to remove from client", extra={
                        "client_name": client_name,
                        "client_id": client_id,
                        "delete_files": delete_files,
                    })

            except Exception as e:
                self.logger.exception("Error removing from client", extra={
                    "client_name": client_name,
                    "client_id": client_id,
                    "error": str(e),
                })

        thread_name = f"client-remove-{(client_id or '')[:12]}"
        threading.Thread(target=_do_remove, daemon=True, name=thread_name).start()
    
    def cleanup_download_files(self, download_id: int):
        """Clean up all files for a download (used during cancellation)."""
        queue_manager = self._get_queue_manager()
        download = queue_manager.get_download(download_id)
        
        if not download:
            return
        
        try:
            if download.get('download_client_id'):
                self.remove_from_client(
                    download['download_client'],
                    download['download_client_id'],
                    delete_files=True
                )
            
            self._cleanup_temp_files(download_id)
            
            self.logger.debug("Cleaned up all files for download", extra={
                "download_id": download_id
            })
            
        except Exception as e:
            self.logger.exception("Error cleaning up download files", extra={
                "download_id": download_id,
                "error": str(e)
            })
    
    @staticmethod
    def _is_in_conversion_temp_dir(path: str) -> bool:
        """True if ``path`` lies inside the conversion service's temp directory."""
        try:
            temp_dir = container.get_conversion_service()._get_shared_temp_dir()
        except (AttributeError, OSError):
            from core.paths import get_path_resolver
            temp_dir = get_path_resolver().get_conversion_dir()
        root = os.path.realpath(temp_dir)
        return os.path.commonpath([os.path.realpath(path), root]) == root

    def _cleanup_temp_files(self, download_id: int, download_data: dict = None):
        """Remove this download's temporary conversion output, if any.

        Only a converted file that is still inside the conversion service's
        work/conversion directory is deleted, so a
        file already moved into the library is never touched.
        """
        try:
            if download_data is None:
                queue_manager = self._get_queue_manager()
                download_data = queue_manager.get_download(download_id)
                if not download_data:
                    self.logger.warning("Download not found for temp cleanup", extra={
                        "download_id": download_id
                    })
                    return

            converted_file = download_data.get('converted_file_path')
            if converted_file and os.path.isfile(converted_file) and self._is_in_conversion_temp_dir(converted_file):
                try:
                    os.remove(converted_file)
                    self.logger.debug("Deleted converted temp file", extra={
                        "download_id": download_id,
                        "path": converted_file
                    })
                except Exception as e:
                    self.logger.warning("Failed to delete converted file", extra={
                        "download_id": download_id,
                        "path": converted_file,
                        "error": str(e)
                    })

            self.remove_work_files(download_id)

            self.logger.debug("Cleaned up temp files", extra={
                "download_id": download_id
            })
            
        except Exception as e:
            self.logger.exception("Error cleaning up temp files", extra={
                "download_id": download_id,
                "error": str(e)
            })
    
    @staticmethod
    def work_dirs_for(download_id: int):
        """The two per-download scratch folders (they may not exist)."""
        work = get_path_resolver().get_work_dir()
        download_id = int(download_id)
        return (
            os.path.join(work, 'audible', str(download_id)),
            os.path.join(work, 'conversion', f'convert_{download_id}'),
        )

    def remove_work_files(self, download_id: int) -> int:
        """Delete work/audible/<id> and work/conversion/convert_<id>.

        Returns the number of bytes freed. Symlinks are never followed.
        """
        freed = 0
        for path in self.work_dirs_for(download_id):
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            size = _dir_size(path)
            shutil.rmtree(path, ignore_errors=True)
            if os.path.exists(path):
                self.logger.warning("Could not fully remove work folder", extra={
                    "download_id": download_id, "path": path
                })
                continue
            freed += size
            self.logger.info("Removed work folder", extra={
                "download_id": download_id, "path": path, "freed_mb": round(size / 1048576, 1)
            })
        return freed

    @staticmethod
    def _parse_timestamp(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed

    def sweep_work_dirs(self, retention_days: int = 7) -> dict:
        """Remove work files no download needs any more.

        - per-download folders whose queue row is gone, or whose download is
          imported / seeding / cancelled
        - folders of permanently failed downloads not touched for
          ``retention_days`` (kept until then so a manual retry still works)
        - loose leftover files in work/conversion and work/failed older than
          a day

        Folders of queued and in-progress downloads are never touched.
        Returns {'removed': n, 'freed_bytes': n}.
        """
        queue_manager = self._get_queue_manager()
        work = get_path_resolver().get_work_dir()
        cutoff = datetime.now() - timedelta(days=max(0, int(retention_days)))
        removed, freed = 0, 0
        decided = {}

        def should_remove(download_id: int, folder: str) -> bool:
            if download_id in decided:
                return decided[download_id]
            record = queue_manager.get_download(download_id) if queue_manager else None
            if record is None:
                verdict = True
            else:
                status = str(record.get('status') or '').upper()
                if status in _DONE_STATUSES:
                    verdict = True
                elif status in _FAILED_STATUSES:
                    touched = self._parse_timestamp(record.get('updated_at'))
                    if touched is None:
                        touched = datetime.fromtimestamp(os.path.getmtime(folder))
                    verdict = touched < cutoff
                else:
                    verdict = False
            decided[download_id] = verdict
            return verdict

        for subdir, pattern in (('audible', _AUDIBLE_DIR_RE), ('conversion', _CONVERT_DIR_RE)):
            root = os.path.join(work, subdir)
            if not os.path.isdir(root):
                continue
            for entry in os.listdir(root):
                path = os.path.join(root, entry)
                match = pattern.match(entry)
                if match and os.path.isdir(path) and not os.path.islink(path):
                    download_id = int(match.group(1) if match.groups() else entry)
                    if should_remove(download_id, path):
                        size = _dir_size(path)
                        shutil.rmtree(path, ignore_errors=True)
                        if not os.path.exists(path):
                            removed += 1
                            freed += size
                elif os.path.isfile(path) and not os.path.islink(path):
                    if time.time() - os.path.getmtime(path) > _STRAY_FILE_MAX_AGE_SECONDS:
                        try:
                            freed += os.path.getsize(path)
                            os.remove(path)
                            removed += 1
                        except OSError:
                            pass

        failed_root = os.path.join(work, 'failed')
        if os.path.isdir(failed_root):
            for entry in os.listdir(failed_root):
                path = os.path.join(failed_root, entry)
                if os.path.islink(path) or datetime.fromtimestamp(os.path.getmtime(path)) >= cutoff:
                    continue
                size = _dir_size(path) if os.path.isdir(path) else os.path.getsize(path)
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
                if not os.path.exists(path):
                    removed += 1
                    freed += size

        if removed:
            self.logger.info("Work folder sweep finished", extra={
                "removed": removed, "freed_mb": round(freed / 1048576, 1)
            })
        return {'removed': removed, 'freed_bytes': freed}

    def check_seeding_complete(self, download_id: int, download_data: dict,
                              seed_ratio_limit: float = 2.0,
                              seed_time_limit_hours: int = 168) -> bool:
        """Check if torrent seeding is complete based on ratio and time goals."""
        try:
            client_name = download_data.get('download_client')
            client_id = download_data.get('download_client_id')
            
            if not client_name or not client_id:
                self.logger.warning("Download missing client info", extra={
                    "download_id": download_id
                })
                return False
            
            client_selector = self._get_client_selector()
            client = client_selector.get_client(client_name)
            
            if not client:
                self.logger.warning("Client not available for seeding check", extra={
                    "client_name": client_name,
                    "download_id": download_id
                })
                return False
            
            try:
                torrent_info = client.get_status(client_id)
            except ValueError:
                self.logger.warning("Torrent not found in client during seeding check", extra={
                    "client_name": client_name,
                    "client_id": client_id,
                    "download_id": download_id
                })
                return False
            
            if not torrent_info:
                self.logger.warning("Torrent not found in client", extra={
                    "client_name": client_name,
                    "client_id": client_id,
                    "download_id": download_id
                })
                return False

            client_ratio_limit = torrent_info.get('seed_ratio_limit')
            if isinstance(client_ratio_limit, (int, float)) and client_ratio_limit > 0:
                seed_ratio_limit = float(client_ratio_limit)

            client_time_limit_seconds = torrent_info.get('seed_time_limit_seconds')
            if isinstance(client_time_limit_seconds, (int, float)) and client_time_limit_seconds > 0:
                seed_time_limit_hours = client_time_limit_seconds / 3600

            state = torrent_info.get('state', '').lower()
            if state in ['error', 'missing', 'removed']:
                self.logger.debug("Torrent in terminal state", extra={
                    "client_id": client_id,
                    "state": state,
                    "download_id": download_id
                })
                return False
            
            ratio = torrent_info.get('ratio', 0.0)
            if ratio >= seed_ratio_limit:
                self.logger.debug("Seed ratio target met", extra={
                    "client_id": client_id,
                    "download_id": download_id,
                    "ratio": round(ratio, 2),
                    "seed_ratio_limit": seed_ratio_limit
                })
                return True
            
            seeding_time = torrent_info.get('seeding_time', 0)
            seeding_hours = seeding_time / 3600
            if seeding_hours >= seed_time_limit_hours:
                self.logger.debug("Seed time target met", extra={
                    "client_id": client_id,
                    "download_id": download_id,
                    "hours": round(seeding_hours, 1),
                    "seed_time_limit_hours": seed_time_limit_hours
                })
                return True
            
            self.logger.debug("Torrent still seeding", extra={
                "client_id": client_id,
                "download_id": download_id,
                "ratio": round(ratio, 2),
                "seed_ratio_limit": seed_ratio_limit,
                "seeding_hours": round(seeding_hours, 1),
                "seed_time_limit_hours": seed_time_limit_hours
            })
            return False
            
        except Exception as e:
            self.logger.exception("Error checking seeding status", extra={
                "download_id": download_id,
                "error": str(e)
            })
            return False
    
    def finalize_seeding(self, download_id: int, download_data: dict,
                        delete_files: bool = True):
        """Finalize a completed seeding download.

        Removes torrent from client and optionally deletes source files.
        """
        try:
            client_name = download_data.get('download_client')
            client_id = download_data.get('download_client_id')
            
            if client_name and client_id:
                self.remove_from_client(client_name, client_id, delete_files=delete_files)
                self.logger.debug("Finalized seeding", extra={
                    "download_id": download_id,
                    "client_name": client_name,
                    "client_id": client_id,
                    "delete_files": delete_files
                })
            
            self._cleanup_temp_files(download_id, download_data)
            
        except Exception as e:
            self.logger.exception("Error finalizing seeding", extra={
                "download_id": download_id,
                "error": str(e)
            })
    
    def cleanup_old_failed_downloads(self, days: int = 7) -> int:
        """Remove permanently failed downloads whose last activity is older than
        ``days``, together with their work files.

        A failed download keeps its queue row (and files) so the user can
        retry it; after ``days`` without activity it is dropped. Last activity
        is updated_at, falling back to queued_at; rows with no parseable
        timestamp are left alone. Timestamps are compared in Python because
        the table holds both ISO ('T') and SQLite default (' ') formats.
        """
        try:
            engine = container.get_database_service().connection_manager.get_engine()

            cutoff = datetime.now() - timedelta(days=max(0, int(days)))
            statuses = sorted(_FAILED_STATUSES)
            params = {f"s{i}": status for i, status in enumerate(statuses)}
            placeholders = ", ".join(f":s{i}" for i in range(len(statuses)))

            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT id, updated_at, queued_at FROM download_queue "
                        f"WHERE UPPER(status) IN ({placeholders})"
                    ),
                    params,
                ).fetchall()

            expired = []
            for row in rows:
                last_activity = self._parse_timestamp(row[1]) or self._parse_timestamp(row[2])
                if last_activity is not None and last_activity < cutoff:
                    expired.append(int(row[0]))

            if not expired:
                return 0

            for download_id in expired:
                self.remove_work_files(download_id)

            id_params = {f"id{i}": download_id for i, download_id in enumerate(expired)}
            with engine.begin() as conn:
                result = conn.execute(
                    text(
                        "DELETE FROM download_queue WHERE id IN ("
                        + ", ".join(f":id{i}" for i in range(len(expired)))
                        + ")"
                    ),
                    id_params,
                )
                deleted = result.rowcount

            self.logger.info(
                "Cleaned up old failed downloads",
                extra={"deleted_count": deleted, "older_than_days": days, "download_ids": expired},
            )
            return deleted

        except Exception as e:
            self.logger.error(
                f"Error cleaning up old failed downloads: {e}",
                exc_info=True,
            )
            return 0
