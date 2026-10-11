"""
Module Name: defaults.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Generate and persist default configuration for AuralArchive.

Location:
    /services/settings/defaults.py
"""

import configparser
import os

from core.logging import get_module_logger


class ConfigDefaults:
    """Handle default configuration generation for AuralArchive."""

    def __init__(self, config_file: str, logger=None):
        self.config_file = config_file
        self.logger = logger or get_module_logger("Service.Config.Defaults")
    
    def ensure_config_exists(self):
        """Ensure configuration file exists, create default if not."""
        if not os.path.exists(self.config_file):
            self.logger.warning(
                "Configuration file not found; creating default configuration",
                extra={"config_file": self.config_file},
            )
            self.generate_default_config()
    
    def generate_default_config(self):
        """Generate a complete default configuration file with all sections."""
        config = configparser.ConfigParser()
        
        sections = [
            self._add_audible_config,
            self._add_jackett_config,
            self._add_qbittorrent_config,
            self._add_naming_config,
            self._add_import_config,
            self._add_download_management_config,
            self._add_application_config,
            self._add_auto_search_config,
            self._add_authors_config,
            self._add_conversion_config,
            self._add_paths_config,
            self._add_metadata_refresh_config,
        ]

        for add_section in sections:
            add_section(config)
        
        try:
            with open(self.config_file, "w") as configfile:
                config.write(configfile)
            if os.name != "nt":
                os.chmod(self.config_file, 0o600)
            self.logger.info(
                "Default configuration created",
                extra={"config_file": self.config_file},
            )
        except Exception:
            self.logger.exception(
                "Failed to create default configuration",
                extra={"config_file": self.config_file},
            )
    
    def _add_audible_config(self, config: configparser.ConfigParser):
        """Add Audible API configuration section."""
        config["audible"] = {
            "username": "",
            "password": "",
            "country_code": "us",
            "max_results": "25",
            "cache_duration": "2",
            "auto_authenticate": "True",
            "download_format": "aaxc",
            "download_quality": "best",
            "aax_fallback_enabled": "true",
            "save_voucher": "true",
            "include_cover": "false",
            "include_chapters": "false",
            "include_pdf": "false",
            "concurrent_downloads": "1"
        }
    
    def _add_jackett_config(self, config: configparser.ConfigParser):
        """Add Jackett configuration section."""
        config["jackett"] = {
            "enabled": "false",
            "api_key": "",
            "api_url": "http://localhost:9117/api/v2.0/indexers/all/results/torznab/",
            "indexers": "all",
            "host": "localhost",
            "port": "9117",
            "use_https": "false",
            "timeout": "30",
            "download_base_url": ""
        }
    
    def _add_qbittorrent_config(self, config: configparser.ConfigParser):
        """Add qBittorrent configuration section."""
        config["qbittorrent"] = {
            "enabled": "false",
            "qb_host": "localhost",
            "qb_port": "8080",
            "qb_auth_mode": "credentials",
            "qb_username": "",
            "qb_password": "",
            "qb_api_key": "",
            "auto_download": "false",
            "category": "auralarchive"
        }
    
    def _add_naming_config(self, config: configparser.ConfigParser):
        """Add file-naming configuration section (library folder layout)."""
        config["naming"] = {
            "naming_template": "simple",
            "include_asin_in_path": "false",
            "create_author_folders": "true",
            "create_series_folders": "true"
        }

    def _add_import_config(self, config: configparser.ConfigParser):
        """Add import service configuration section."""
        config["import"] = {
            "verify_after_import": "true",
            "create_backup_on_error": "true",
            "delete_source_after_import": "false",
            "use_hardlinks": "false"
        }
    
    def _add_download_management_config(self, config: configparser.ConfigParser):
        """Add download management configuration section."""
        config["download_management"] = {
            "seeding_enabled": "false",
            "delete_source_after_import": "false",
            "max_concurrent_downloads": "2",
            "monitoring_interval": "2",
            "auto_start_monitoring": "true",
            "monitor_seeding": "true",
            "retry_search_max": "3",
            "retry_download_max": "2",
            "retry_conversion_max": "1",
            "retry_import_max": "2",
            "retry_backoff_seconds": "10"
        }
    
    def _add_application_config(self, config: configparser.ConfigParser):
        """Add application settings section."""
        config["application"] = {
            "log_level": "INFO"
        }
    
    def _add_authors_config(self, config: configparser.ConfigParser):
        """Add author discovery and catalog configuration section."""
        config["authors"] = {
            "auto_add_missing": "false",
            "auto_add_limit": "0",
            "preferred_languages": "english,en"
        }

    def _add_auto_search_config(self, config: configparser.ConfigParser):
        """Add automatic search configuration section."""
        config["auto_search"] = {
            "auto_download_enabled": "false",
            "quality_threshold": "5",
            "scan_interval_seconds": "120",
            "max_batch_size": "2",
            "skip_book_ids": ""
        }
    
    def _add_conversion_config(self, config: configparser.ConfigParser):
        """Add audiobook conversion configuration section."""
        config["conversion"] = {
            "convert_mp3_to_m4b": "false",
            "quality.codec": "aac",
            "quality.bitrate": "64k",
            "quality.sample_rate": "22050",
            "quality.channels": "1",
            "quality.format": "m4b",
        }
        config["conversion.quality"] = {
            "preserve_chapters": "true",
            "embed_cover": "true",
            "preserve_metadata": "true",
        }

    def _add_paths_config(self, config: configparser.ConfigParser):
        """Add media folder locations. Empty = the built-in folder under the
        media root; see core/paths.py."""
        config["paths"] = {
            "library": "",
            "downloads": "",
            "import": "",
            "work": "",
        }

    def _add_metadata_refresh_config(self, config: configparser.ConfigParser):
        """Scheduled metadata refresh (services/metadata/refresh). Intervals in
        days; 0 turns that refresh off."""
        config["metadata_refresh"] = {
            "enabled": "true",
            "library_interval_days": "1",
            "authors_interval_days": "7",
            "series_interval_days": "7",
            "books_interval_days": "30",
            "request_delay_seconds": "2",
        }
