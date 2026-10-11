"""
Module Name: validation.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Validate configuration sections for AuralArchive services.

Location:
    /services/settings/validation.py
"""

import os
from typing import Dict

from core.config.secrets_crypto import redact_config
from core.logging import get_module_logger


class ConfigValidation:
    """Handle configuration validation for all AuralArchive services."""

    def __init__(self, logger=None, **_kwargs):
        self.logger = logger or get_module_logger("Service.Config.Validation")
    
    def validate_config(self, config: Dict[str, Dict[str, str]]) -> Dict[str, bool]:
        """Validate configuration sections and return status."""
        validation_results = {}
        
        try:
            directories = config.get('directories', {})
            validation_results['directories'] = self._validate_directories(directories)
            
            qb_config = config.get('qbittorrent', {})
            validation_results['qbittorrent'] = self._validate_qbittorrent(qb_config)
            
            jackett_config = config.get('jackett', {})
            validation_results['jackett'] = self._validate_jackett(jackett_config)
            
            audible_config = config.get('audible', {})
            validation_results['audible'] = self._validate_audible(audible_config)
            
        except Exception:
            self.logger.exception(
                "Error during configuration validation",
                extra={"config_sections": list(config.keys()) if config else []},
            )
        
        return validation_results
    
    def _validate_directories(self, directories: Dict[str, str]) -> bool:
        """Validate directory configuration."""
        required_dirs = ['source_dir', 'import_dir', 'library_dir']
        
        for dir_key in required_dirs:
            dir_path = directories.get(dir_key, '')
            if not dir_path:
                self.logger.warning(f"Missing directory configuration: {dir_key}")
                return False
            
            try:
                if not os.path.exists(dir_path):
                    os.makedirs(dir_path, exist_ok=True)
                    self.logger.info("Created directory", extra={"path": dir_path})
            except Exception:
                self.logger.exception(
                    "Cannot create directory",
                    extra={"path": dir_path},
                )
                return False
        
        self.logger.debug("Directory configuration validation passed")
        return True
    
    def _validate_qbittorrent(self, qb_config: Dict[str, str]) -> bool:
        """Validate qBittorrent configuration."""
        host = qb_config.get('qb_host', '')
        port = qb_config.get('qb_port', '')
        auth_mode = qb_config.get('qb_auth_mode', 'credentials')

        if not host or not port:
            self.logger.warning("Incomplete qBittorrent configuration", extra=redact_config(qb_config))
            return False

        if auth_mode == 'api_key':
            api_key = qb_config.get('qb_api_key', '')
            if not api_key:
                self.logger.warning("qBittorrent API key mode requires an API key", extra=redact_config(qb_config))
                return False
        else:
            username = qb_config.get('qb_username', '')
            password = qb_config.get('qb_password', '')
            if not username or not password:
                self.logger.warning("Incomplete qBittorrent configuration", extra=redact_config(qb_config))
                return False

        try:
            port_int = int(port)
            if port_int < 1 or port_int > 65535:
                self.logger.warning(
                    "Invalid qBittorrent port value",
                    extra={"qb_port": port},
                )
                return False
        except ValueError:
            self.logger.warning(
                "Invalid qBittorrent port format",
                extra={"qb_port": port},
            )
            return False
        
        self.logger.debug("qBittorrent configuration validation passed")
        return True
    
    def _validate_jackett(self, jackett_config: Dict[str, str]) -> bool:
        """Validate Jackett configuration."""
        url = jackett_config.get('jackett_url', '')
        api_key = jackett_config.get('jackett_api_key', '')
        
        if not url or not api_key:
            self.logger.warning("Incomplete Jackett configuration", extra=redact_config(jackett_config))
            return False
        
        if not (url.startswith('http://') or url.startswith('https://')):
            self.logger.warning(
                "Invalid Jackett URL format",
                extra={"jackett_url": url},
            )
            return False
        
        self.logger.debug("Jackett configuration validation passed")
        return True
    
    def _validate_audible(self, audible_config: Dict[str, str]) -> bool:
        """Validate Audible configuration."""
        max_results = audible_config.get('max_results', '25')
        region = audible_config.get('default_region', 'us')
        
        try:
            max_results_int = int(max_results)
            if max_results_int < 5 or max_results_int > 100:
                self.logger.warning(
                    "Invalid max_results value",
                    extra={"max_results": max_results},
                )
                return False
        except ValueError:
            self.logger.warning(
                "Invalid max_results format",
                extra={"max_results": max_results},
            )
            return False
        
        valid_regions = ['us', 'uk', 'ca', 'au', 'de', 'fr', 'jp', 'in']
        if region not in valid_regions:
            self.logger.warning("Invalid region", extra={"region": region})
            return False
        
        self.logger.debug("Audible configuration validation passed")
        return True