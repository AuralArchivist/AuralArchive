"""
Module Name: service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    The download service: one shared instance that loads the download
    settings and joins the pipeline stages from the modules beside it.

Location:
    /services/acquisition/service.py

Workflow:
    QUEUED → SEARCHING → FOUND → DOWNLOADING → COMPLETE →
    (Audible) CONVERTING → CONVERTED → IMPORTING → IMPORTED
    (Torrent/NZB) IMPORTING → IMPORTED

Download Clients:
    - qBittorrent (torrents/magnets)
    - SABnzbd, NZBGet (Usenet)
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from core.convert import to_bool, to_int
from core.logging import get_module_logger
from core.paths import get_path_resolver

from .audible_download import AudibleDownloadMixin
from .client_paths import ClientPathsMixin
from .completion import CompletionMixin
from .grab import GrabMixin
from .path_mapping import strip_trailing_separators
from .pipeline import PipelineMixin
from .queue_actions import QueueActionsMixin
from .scoring import ScoringMixin
from .searching import SearchStageMixin
from .torrent_fetch import TorrentFetchMixin
from services.container import container

_LOGGER = get_module_logger("Service.DownloadManagement.Service")


class DownloadManagementService(
    QueueActionsMixin,
    PipelineMixin,
    SearchStageMixin,
    ScoringMixin,
    GrabMixin,
    TorrentFetchMixin,
    AudibleDownloadMixin,
    CompletionMixin,
    ClientPathsMixin,
):
    """Shared download service; each stage's methods come from its mixin."""

    def __init__(self, *_, logger=None, **__):
        self.logger = logger or _LOGGER
        self.logger.debug("Initializing DownloadManagementService...")

        self._build_pipeline()

        self._audible_context_lock = threading.Lock()
        self._audible_download_context: Dict[int, Dict[str, Any]] = {}
        self._audible_executor_lock = threading.Lock()
        self._audible_executor: Optional[ThreadPoolExecutor] = None
        self._audible_max_workers = 1

        self._search_executor: Optional[ThreadPoolExecutor] = None
        self._search_executor_lock = threading.Lock()
        self._searches_in_flight: set = set()
        self._searches_in_flight_lock = threading.Lock()
        self._conversion_executor: Optional[ThreadPoolExecutor] = None
        self._conversion_executor_lock = threading.Lock()
        self._conversions_in_flight: set = set()
        self._conversions_in_flight_lock = threading.Lock()
        self._import_executor: Optional[ThreadPoolExecutor] = None
        self._import_executor_lock = threading.Lock()
        self._imports_in_flight: set = set()
        self._imports_in_flight_lock = threading.Lock()


        self.polling_interval = 2
        self._next_work_sweep = 0.0
        self._monitor_failed = False
        self._config_reload_lock = threading.RLock()
        self.jackett_download_base_url: Optional[str] = None
        self.jackett_verify_ssl: bool = True

        self.seeding_enabled = False
        self.delete_source_after_import = False
        self.convert_mp3_to_m4b = False


        self.torrent_download_root = None
        self.torrent_download_root_local = None
        self.torrent_download_root_remote = None
        self.qbittorrent_path_mappings = []
        self.max_concurrent_downloads = 2
        self.retry_limits = {
            'SEARCH_FAILED': 3,
            'DOWNLOAD_FAILED': 2,
            'CONVERSION_FAILED': 1,
            'IMPORT_FAILED': 2
        }
        self.retry_backoff_seconds = 10
        self.keep_torrent_active = True
        self.wait_for_seeding_completion = True
        self.delete_temp_files = True
        self.retention_days = 7
        self.auto_start_monitoring = True
        self.monitor_seeding_enabled = True
        self.auto_process_queue = True
        self.queue_priority_default = 5
        self.max_active_searches = 2
        self.direct_provider_sessions: Dict[str, Dict[str, str]] = {}
        self.minimum_free_space = 100
        self.free_space_unit = 'MB'
        self.skip_free_space_check = False
        self.ratio_protection_enabled = False
        self.ratio_floor = 1.0
        self.prefer_freeleech = False
        self.hold_non_freeleech = False
        self.freeleech_hold_days = 14
        self.duplicate_grab_protection = True

        self._initialize_service()

        self.logger.debug("Download management service ready")

    @property
    def downloads_path(self) -> str:
        return get_path_resolver().get_downloads_dir()

    @property
    def import_path(self) -> str:
        return get_path_resolver().get_import_dir()

    @property
    def library_path(self) -> str:
        return get_path_resolver().get_library_dir()

    @property
    def audible_dl_path(self) -> str:
        return get_path_resolver().get_audible_work_dir()

    @property
    def conversion_path(self) -> str:
        return get_path_resolver().get_conversion_dir()

    @property
    def temp_failed_path(self) -> str:
        return get_path_resolver().get_failed_dir()

    def _initialize_service(self):
        """Initialize the download management service."""
        try:
            self._load_configuration()
            self._configure_audible_concurrency()
            self._ensure_monitor_running()
            self.logger.debug("Download management service ready")
        except Exception as e:
            self.logger.error(f"Error initializing download management service: {e}")
            raise

    def _build_pipeline(self):
        """Instantiate companion objects and wire shared references between them.

        Called once from __init__ during singleton setup. Each companion receives
        the same shared instances so they operate on a single consistent state
        rather than creating independent copies via their own lazy-init fallbacks.
        """
        from .queue_manager import QueueManager
        from .state_machine import StateMachine
        from .download_monitor import DownloadMonitor
        from .client_selector import ClientSelector
        from .progress_tracker import ProgressTracker
        from .retry_handler import RetryHandler
        from .cleanup_manager import CleanupManager
        from .event_emitter import EventEmitter

        self.queue_manager = QueueManager()
        self.state_machine = StateMachine()
        self.download_monitor = DownloadMonitor()
        self.client_selector = ClientSelector()
        self.progress_tracker = ProgressTracker()
        self.retry_handler = RetryHandler()
        self.cleanup_manager = CleanupManager()
        self.event_emitter = EventEmitter()

        self.download_monitor.set_client_selector(self.client_selector)
        self.event_emitter.attach_lookup(self.queue_manager.get_download)
        self.download_monitor.set_event_emitter(self.event_emitter)
        self.retry_handler.attach_event_emitter(self.event_emitter)
        self.retry_handler.set_queue_manager(self.queue_manager)
        self.retry_handler.set_state_machine(self.state_machine)
        self.cleanup_manager.set_queue_manager(self.queue_manager)
        self.cleanup_manager.set_client_selector(self.client_selector)
        self.download_monitor.set_queue_manager(self.queue_manager)
        self.download_monitor.set_state_machine(self.state_machine)
        self.download_monitor.set_cleanup_manager(self.cleanup_manager)
        self.state_machine.set_queue_manager(self.queue_manager)

    def _load_configuration(self):
        """Load download management configuration from config.txt"""
        try:
            config_service = container.get_config_service()
            dm_config = config_service.get_section('download_management')
            
            if dm_config:
                def _coerce_float(value, default):
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return default

                self.seeding_enabled = to_bool(dm_config.get('seeding_enabled', self.seeding_enabled), self.seeding_enabled)
                self.keep_torrent_active = to_bool(dm_config.get('keep_torrent_active', self.keep_torrent_active), self.keep_torrent_active)
                self.wait_for_seeding_completion = to_bool(dm_config.get('wait_for_seeding_completion', self.wait_for_seeding_completion), self.wait_for_seeding_completion)
                self.delete_source_after_import = to_bool(dm_config.get('delete_source_after_import', self.delete_source_after_import), self.delete_source_after_import)
                self.delete_temp_files = to_bool(dm_config.get('delete_temp_files', self.delete_temp_files), self.delete_temp_files)
                self.retention_days = to_int(dm_config.get('retention_days', self.retention_days), self.retention_days)
                self.auto_process_queue = to_bool(dm_config.get('auto_process_queue', self.auto_process_queue), self.auto_process_queue)

                monitoring_raw = dm_config.get('monitoring_interval', dm_config.get('polling_interval_seconds', self.polling_interval))
                monitoring_value = to_int(monitoring_raw, self.polling_interval)
                self.polling_interval = max(1, monitoring_value)
                self.auto_start_monitoring = to_bool(dm_config.get('auto_start_monitoring', self.auto_start_monitoring), self.auto_start_monitoring)
                self.monitor_seeding_enabled = to_bool(dm_config.get('monitor_seeding', self.monitor_seeding_enabled), self.monitor_seeding_enabled)
                self.max_concurrent_downloads = to_int(dm_config.get('max_concurrent_downloads', self.max_concurrent_downloads), self.max_concurrent_downloads)
                self.queue_priority_default = to_int(dm_config.get('queue_priority_default', self.queue_priority_default), self.queue_priority_default)
                self.max_active_searches = max(1, to_int(dm_config.get('max_active_searches', self.max_active_searches), self.max_active_searches))

                self.minimum_free_space = to_int(dm_config.get('minimum_free_space', self.minimum_free_space), self.minimum_free_space)
                free_space_unit = str(dm_config.get('free_space_unit', self.free_space_unit) or self.free_space_unit).strip().upper()
                self.free_space_unit = free_space_unit if free_space_unit in {'MB', 'GB', 'TB'} else self.free_space_unit
                self.skip_free_space_check = to_bool(dm_config.get('skip_free_space_check', self.skip_free_space_check), self.skip_free_space_check)

                self.ratio_protection_enabled = to_bool(dm_config.get('ratio_protection_enabled', self.ratio_protection_enabled), self.ratio_protection_enabled)
                self.ratio_floor = _coerce_float(dm_config.get('ratio_floor', self.ratio_floor), self.ratio_floor)
                self.prefer_freeleech = to_bool(dm_config.get('prefer_freeleech', self.prefer_freeleech), self.prefer_freeleech)
                self.hold_non_freeleech = to_bool(dm_config.get('hold_non_freeleech', self.hold_non_freeleech), self.hold_non_freeleech)
                self.freeleech_hold_days = max(0, to_int(dm_config.get('freeleech_hold_days', self.freeleech_hold_days), self.freeleech_hold_days))
                self.duplicate_grab_protection = to_bool(dm_config.get('duplicate_grab_protection', self.duplicate_grab_protection), self.duplicate_grab_protection)

                self.retry_limits = {
                    'SEARCH_FAILED': to_int(dm_config.get('retry_search_max', self.retry_limits.get('SEARCH_FAILED', 3)), self.retry_limits.get('SEARCH_FAILED', 3)),
                    'DOWNLOAD_FAILED': to_int(dm_config.get('retry_download_max', self.retry_limits.get('DOWNLOAD_FAILED', 2)), self.retry_limits.get('DOWNLOAD_FAILED', 2)),
                    'CONVERSION_FAILED': to_int(dm_config.get('retry_conversion_max', self.retry_limits.get('CONVERSION_FAILED', 1)), self.retry_limits.get('CONVERSION_FAILED', 1)),
                    'IMPORT_FAILED': to_int(dm_config.get('retry_import_max', self.retry_limits.get('IMPORT_FAILED', 2)), self.retry_limits.get('IMPORT_FAILED', 2))
                }

                backoff_seconds_value = None
                backoff_seconds_raw = dm_config.get('retry_backoff_seconds')
                if backoff_seconds_raw is not None:
                    try:
                        backoff_seconds_value = int(backoff_seconds_raw)
                    except (TypeError, ValueError):
                        self.logger.warning(f"Invalid retry_backoff_seconds value: {backoff_seconds_raw}")

                if backoff_seconds_value is None:
                    backoff_minutes_raw = dm_config.get('retry_backoff_minutes')
                    if backoff_minutes_raw is not None:
                        try:
                            minutes_int = int(backoff_minutes_raw)
                            backoff_seconds_value = minutes_int * 60
                        except (TypeError, ValueError):
                            self.logger.warning(f"Invalid retry_backoff_minutes value: {backoff_minutes_raw}")

                if backoff_seconds_value is None or backoff_seconds_value <= 0:
                    backoff_seconds_value = 10

                self.retry_backoff_seconds = max(10, backoff_seconds_value)
                self.retry_handler.set_retry_backoff(self.retry_backoff_seconds)
                self.retry_handler.set_retry_limits(self.retry_limits)
                self.logger.debug(f"Configured download retry backoff: {self.retry_backoff_seconds} seconds")
                
                self.logger.debug(
                    "Download management configuration loaded: seeding=%s, delete_source=%s, polling_interval=%ss",
                    self.seeding_enabled,
                    self.delete_source_after_import,
                    self.polling_interval
                )
            else:
                self.logger.warning("No [download_management] section in config - using defaults")

            self.convert_mp3_to_m4b = config_service.get_config_bool(
                'conversion', 'convert_mp3_to_m4b', self.convert_mp3_to_m4b
            )

            qbittorrent_cfg = {}
            try:
                qbittorrent_cfg = self.client_selector.get_client_config('qbittorrent', refresh=True) or {}
            except Exception as cfg_error:
                self.logger.warning(f"Unable to load qBittorrent configuration: {cfg_error}")

            self.qbittorrent_path_mappings = qbittorrent_cfg.get('path_mappings') or []

            remote_root_candidate = ''
            local_root_candidate = ''

            if qbittorrent_cfg:
                if self.qbittorrent_path_mappings:
                    remote_root_candidate = self.qbittorrent_path_mappings[0].get('remote', '') or remote_root_candidate
                    local_root_candidate = self.qbittorrent_path_mappings[0].get('local', '') or local_root_candidate

                if not remote_root_candidate:
                    remote_root_candidate = (
                        qbittorrent_cfg.get('download_path_remote')
                        or qbittorrent_cfg.get('download_path')
                        or ''
                    )

                if not local_root_candidate:
                    local_root_candidate = qbittorrent_cfg.get('download_path_local') or ''

            if local_root_candidate:
                local_root_abs = os.path.abspath(local_root_candidate)
                try:
                    os.makedirs(local_root_abs, exist_ok=True)
                    self.torrent_download_root_local = local_root_abs
                    self.logger.debug(
                        "Configured qBittorrent local save path: %s",
                        self.torrent_download_root_local,
                    )
                except Exception as path_error:
                    self.logger.warning(
                        "Failed to prepare qBittorrent local save path '%s': %s",
                        local_root_abs,
                        path_error,
                    )

            if not self.torrent_download_root_local and remote_root_candidate:
                mapped_local = self._map_remote_to_local(remote_root_candidate)
                if mapped_local:
                    try:
                        os.makedirs(mapped_local, exist_ok=True)
                        self.torrent_download_root_local = mapped_local
                        self.logger.debug(
                            "Derived qBittorrent local save path from remote mapping: %s",
                            self.torrent_download_root_local,
                        )
                    except Exception as map_error:
                        self.logger.warning(
                            "Failed to prepare mapped qBittorrent local save path '%s': %s",
                            mapped_local,
                            map_error,
                        )

            if remote_root_candidate:
                self.torrent_download_root_remote = strip_trailing_separators(remote_root_candidate)
                self.logger.debug(
                    "Configured qBittorrent remote save path: %s",
                    self.torrent_download_root_remote,
                )

            if not self.torrent_download_root_local:
                fallback_local = os.path.abspath(self.downloads_path)
                os.makedirs(fallback_local, exist_ok=True)
                self.torrent_download_root_local = fallback_local
                self.logger.debug(
                    "Using temporary download directory for torrents: %s",
                    self.torrent_download_root_local,
                )

            if not self.torrent_download_root_remote and self.torrent_download_root_local:
                self.torrent_download_root_remote = self.torrent_download_root_local

            self.torrent_download_root = self.torrent_download_root_local

            jackett_section = config_service.get_section('jackett') or {}
            self.jackett_download_base_url = self._extract_jackett_download_base(jackett_section)
            verify_value = jackett_section.get('verify_ssl') or jackett_section.get('verify_cert')
            if isinstance(verify_value, str):
                verify_value = verify_value.strip().lower()
                if verify_value in {"false", "0", "no", "off"}:
                    self.jackett_verify_ssl = False
                elif verify_value in {"true", "1", "yes", "on"}:
                    self.jackett_verify_ssl = True
            elif isinstance(verify_value, bool):
                self.jackett_verify_ssl = verify_value
            if self.jackett_download_base_url:
                self.logger.debug(
                    "Configured external Jackett base URL for torrent downloads: %s",
                    self.jackett_download_base_url,
                )

            self._load_direct_provider_sessions()
                
        except Exception as e:
            self.logger.error(f"Error loading download management configuration: {e}", exc_info=True)

    def reload_configuration(self) -> bool:
        """Reload download management configuration without recreating the singleton."""
        with self._config_reload_lock:
            try:
                self.logger.debug("Reloading download management configuration...")
                self.client_selector.invalidate_cache()
                self._load_configuration()
                self._configure_audible_concurrency()

                if self.auto_start_monitoring:
                    self._ensure_monitor_running()
                elif self.monitor_running:
                    self.logger.debug("Auto-start disabled; stopping active monitor thread")
                    self.stop_monitoring()

                return True
            except Exception as exc:
                self.logger.error(f"Failed to reload download management configuration: {exc}", exc_info=True)
                return False

    def _extract_jackett_download_base(self, jackett_section: Dict[str, Any]) -> Optional[str]:
        """Determine an alternate Jackett base URL for download link rewriting."""

        candidates = [
            os.environ.get("JACKETT_DOWNLOAD_BASE_URL"),
            jackett_section.get('download_base_url'),
            jackett_section.get('download_url_base'),
            jackett_section.get('download_url_host'),
            jackett_section.get('download_host'),
            jackett_section.get('external_base_url'),
        ]

        for candidate in candidates:
            if not candidate:
                continue
            if isinstance(candidate, bool):
                continue
            value = str(candidate).strip()
            if value:
                return value
        return None

    def get_service_status(self) -> Dict[str, Any]:
        """Get service status and statistics."""
        queue_stats = self.queue_manager.get_queue_statistics()
        
        active_standard = len(self.queue_manager.get_queue(status_filter='DOWNLOADING'))
        active_audible = len(self.queue_manager.get_queue(status_filter='AUDIBLE_DOWNLOADING'))

        return {
            'monitor_running': self.monitor_running,
            'polling_interval': self.polling_interval,
            'queue_statistics': queue_stats,
            'active_downloads': active_standard + active_audible
        }
