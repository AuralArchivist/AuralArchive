"""
Module Name: management.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Singleton configuration manager with defaults, validation, and backup helpers.

Location:
    /services/settings/management.py
"""

import configparser
import functools
import json
import os
import tempfile
import threading
from typing import Dict, Any, Optional, Set

from .defaults import ConfigDefaults
from .validation import ConfigValidation
from .export_import import ConfigExportImport
from core import events
from core.config import secrets_crypto
from core.logging import get_module_logger
from core.paths import get_path_resolver

_NAMING_KEYS = (
    'naming_template',
    'include_asin_in_path',
    'create_author_folders',
    'create_series_folders',
    'naming_templates',
)
_REMOVED_SECTIONS = ('audiobookshelf', 'media_management')


def _get_default_config_dir():
    """Get config directory using path resolver."""
    return get_path_resolver().get_config_dir()


def _writes(announce: bool = True):
    """Run a read-change-write of config.txt under the service's lock, so two
    saves at the same time can't undo each other; then announce the sections
    that changed, so saved settings apply straight away."""
    def decorate(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            with self._write_lock:
                before = self._snapshot() if announce else None
                result = method(self, *args, **kwargs)
                changed = self._changed_sections(before) if announce else set()
            if changed:
                events.publish(events.CONFIG_CHANGED, sections=sorted(changed))
            return result
        return wrapper
    return decorate


class ConfigService:
    """Reads and writes config.txt; writes are serialised by one lock."""

    def __init__(
        self,
        config_file: str = "config.txt",
        *,
        logger=None,
        defaults: Optional[ConfigDefaults] = None,
        validation: Optional[ConfigValidation] = None,
        export_import: Optional[ConfigExportImport] = None,
    ):
        self._write_lock = threading.RLock()
        base_dir = _get_default_config_dir()

        if os.path.isabs(config_file):
            self.config_file = config_file
        else:
            self.config_file = os.path.join(base_dir, os.path.basename(config_file))
        self.logger = logger or get_module_logger("Service.Config.Service")

        self.defaults = defaults or ConfigDefaults(self.config_file, logger=self.logger)
        self.validation = validation or ConfigValidation(logger=self.logger)
        self.export_import = export_import or ConfigExportImport(self.config_file, logger=self.logger)

        config_existed_already = os.path.exists(self.config_file)
        self.defaults.ensure_config_exists()
        if config_existed_already:
            self._migrate_removed_sections()

        if config_existed_already:
            try:
                self._write_config(self.load_config())
            except Exception:
                self.logger.exception(
                    "Config encryption migration pass failed",
                    extra={"config_file": self.config_file},
                )
    
    @_writes(announce=False)
    def _migrate_removed_sections(self) -> None:
        """Drop config for removed features (the Audiobookshelf integration and
        the never-used [media_management] section), keeping the file-naming
        settings that lived in [audiobookshelf] by moving them to [naming].
        Idempotent; a no-op once migrated."""
        try:
            config = self.load_config()
            if not any(config.has_section(section) for section in _REMOVED_SECTIONS):
                return
            if config.has_section('audiobookshelf'):
                if not config.has_section('naming'):
                    config.add_section('naming')
                for key in _NAMING_KEYS:
                    if config.has_option('audiobookshelf', key) and not config.has_option('naming', key):
                        config.set('naming', key, config.get('audiobookshelf', key))
            for section in _REMOVED_SECTIONS:
                config.remove_section(section)
            self._write_config(config)
            self.logger.info(
                "Removed config for retired features; naming settings moved to [naming]",
                extra={"config_file": self.config_file, "sections": list(_REMOVED_SECTIONS)},
            )
        except Exception:
            self.logger.exception("Config section migration failed", extra={"config_file": self.config_file})

    def load_config(self) -> configparser.ConfigParser:
        """Load configuration from disk with duplicate section recovery.

        Uses a raw parser with interpolation disabled to tolerate '%' characters
        in values (e.g., titles/paths) without blowing up the parser.
        """
        parser = configparser.RawConfigParser(interpolation=None)
        try:
            with open(self.config_file, "r", encoding="utf-8") as config_handle:
                parser.read_file(config_handle)
            self._decrypt_sensitive_values(parser)
            return parser
        except configparser.DuplicateSectionError as duplicate_error:
            self.logger.warning(
                "Duplicate section detected; attempting automatic recovery",
                extra={"config_file": self.config_file, "error": str(duplicate_error)},
            )
            return self._recover_from_duplicate_sections()
        except FileNotFoundError:
            self.logger.error(
                "Configuration file not found",
                extra={"config_file": self.config_file},
            )
            return parser
        except Exception:
            self.logger.exception(
                "Failed to load configuration",
                extra={"config_file": self.config_file},
            )
            return parser
    
    def get_config_value(self, section: str, key: str, fallback: str = None) -> Optional[str]:
        """Get a specific configuration value."""
        try:
            config = self.load_config()
            return config.get(section.lower(), key.lower(), fallback=fallback)
        except Exception:
            self.logger.exception(
                "Failed to read config value",
                extra={"section": section, "key": key, "config_file": self.config_file},
            )
            return fallback
    
    def get_config_bool(self, section: str, key: str, fallback: bool = False) -> bool:
        """Get a configuration value as boolean."""
        value = self.get_config_value(section, key)
        if value is None:
            return fallback
        return value.lower() in ('true', '1', 'yes', 'on')
    
    def get_config_int(self, section: str, key: str, fallback: int = 0) -> int:
        """Get a configuration value as integer."""
        value = self.get_config_value(section, key)
        if value is None:
            return fallback
        try:
            return int(value)
        except ValueError:
            return fallback
    
    def _snapshot(self) -> Dict[str, Dict[str, str]]:
        config = self.load_config()
        return {name: dict(config[name]) for name in config.sections()}

    def _changed_sections(self, before: Dict[str, Dict[str, str]]) -> Set[str]:
        after = self._snapshot()
        return {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}

    @_writes()
    def replace_section(self, section: str, values: Dict[str, Any]) -> bool:
        """Replace a whole section with these values (keys not given are dropped)."""
        try:
            config = self.load_config()
            section_name = section.lower()
            config.remove_section(section_name)
            config.add_section(section_name)
            for key, value in values.items():
                if value is not None:
                    config.set(section_name, key.lower(), self._coerce_value(value))
            self._write_config(config)
            return True
        except Exception:
            self.logger.exception("Failed to replace configuration section",
                                  extra={"section": section, "config_file": self.config_file})
            return False

    @_writes()
    def update_config(self, section: str, key: str, value: str) -> bool:
        """Update a configuration value."""
        try:
            config = self.load_config()
            section = section.lower()
            key = key.lower()
            
            if secrets_crypto.is_sensitive_key(key) and secrets_crypto.is_masked_value(value):
                # UI sent back the redaction placeholder: keep the stored secret.
                return True

            if not config.has_section(section):
                config.add_section(section)

            config.set(section, key, self._coerce_value(value))

            self._write_config(config)

            self.logger.info(
                "Updated configuration value",
                extra={
                    "section": section,
                    "key": key,
                    "value": "***" if secrets_crypto.is_sensitive_key(key) else value,
                    "config_file": self.config_file,
                },
            )
            return True
        
        except Exception:
            self.logger.exception(
                "Failed to update configuration value",
                extra={"section": section, "key": key, "config_file": self.config_file},
            )
            return False
    
    @_writes()
    def update_multiple_config(self, updates: Dict[str, str]) -> bool:
        """Update multiple configuration values at once."""
        try:
            config = self.load_config()

            if not config.sections() and os.path.exists(self.config_file):
                self.logger.error(
                    "Config load returned empty; aborting multi-update to prevent reset",
                    extra={"config_file": self.config_file},
                )
                return False

            for config_key, value in updates.items():
                if '.' not in config_key:
                    continue
                    
                section, key = config_key.split('.', 1)
                section = section.lower()
                key = key.lower()

                if secrets_crypto.is_sensitive_key(key) and secrets_crypto.is_masked_value(value):
                    continue  # placeholder from the UI: keep the stored secret

                if not config.has_section(section):
                    config.add_section(section)
                
                config.set(section, key, self._coerce_value(value))
                self.logger.debug(
                    "Updated configuration value",
                    extra={
                        "section": section,
                        "key": key,
                        "value": "***" if secrets_crypto.is_sensitive_key(key) else value,
                        "config_file": self.config_file,
                    },
                )
            
            self._write_config(config)
            self.logger.info(
                "Updated multiple configuration values",
                extra={"count": len(updates), "config_file": self.config_file},
            )
            return True
        
        except Exception:
            self.logger.exception(
                "Failed to update multiple configuration values",
                extra={"config_file": self.config_file},
            )
            return False
    
    def list_config(self) -> Dict[str, Dict[str, str]]:
        """Get all configuration as a dictionary."""
        try:
            config = self.load_config()
            return {section: dict(config.items(section)) for section in config.sections()}
        except Exception:
            self.logger.exception(
                "Failed to list configuration",
                extra={"config_file": self.config_file},
            )
            return {}
    
    @_writes()
    def update_section(self, section: str, values: Dict[str, Any]) -> bool:
        """Add or replace values within a configuration section."""
        try:
            config = self.load_config()
            section_name = section.lower()

            if not config.has_section(section_name):
                config.add_section(section_name)

            for key, value in values.items():
                if value is None:
                    continue
                if secrets_crypto.is_sensitive_key(key) and secrets_crypto.is_masked_value(value):
                    continue  # placeholder from the UI: keep the stored secret
                config.set(section_name, key.lower(), self._coerce_value(value))

            self._write_config(config)
            self.logger.info(
                "Updated configuration section",
                extra={"section": section_name, "count": len(values), "config_file": self.config_file},
            )
            return True
        except Exception:
            self.logger.exception(
                "Failed to update configuration section",
                extra={"section": section, "config_file": self.config_file},
            )
            return False

    @_writes()
    def remove_section(self, section: str) -> bool:
        """Remove an entire configuration section if it exists."""
        try:
            config = self.load_config()
            section_name = section.lower()

            if not config.has_section(section_name):
                return True

            config.remove_section(section_name)
            self._write_config(config)
            self.logger.info(
                "Removed configuration section",
                extra={"section": section_name, "config_file": self.config_file},
            )
            return True
        except Exception:
            self.logger.exception(
                "Failed to remove configuration section",
                extra={"section": section, "config_file": self.config_file},
            )
            return False

    def get_hardcover_config(self) -> Dict[str, Any]:
        """Get Hardcover.app integration configuration (token + sync-behavior settings)."""
        config = self.load_config()
        if not config.has_section('hardcover'):
            return {}
        return dict(config.items('hardcover'))

    def update_hardcover_config(self, hardcover_config: Dict[str, Any]) -> bool:
        """Update Hardcover.app configuration (token, user info, sync-behavior settings)."""
        updates = {}
        for key, value in hardcover_config.items():
            updates[f'hardcover.{key}'] = str(value)
        return self.update_multiple_config(updates)

    def is_hardcover_connected(self) -> bool:
        """Check whether a Hardcover.app token has been saved."""
        return bool(self.get_config_value('hardcover', 'token', '').strip())

    def disconnect_hardcover(self) -> bool:
        """Remove the Hardcover.app section entirely (token + settings)."""
        return self.remove_section('hardcover')

    def get_hardcover_auto_push_status(self) -> bool:
        return self.get_config_bool('hardcover', 'auto_push_status', True)

    def get_hardcover_auto_push_owned(self) -> bool:
        return self.get_config_bool('hardcover', 'auto_push_owned', True)

    def get_hardcover_conflict_resolution(self) -> str:
        return self.get_config_value('hardcover', 'conflict_resolution', 'auralarchive_wins')

    def get_hardcover_auto_create(self) -> bool:
        return self.get_config_bool('hardcover', 'auto_create_on_hardcover', False)

    def get_hardcover_cache_refresh_minutes(self) -> int:
        return self.get_config_int('hardcover', 'cache_refresh_interval_minutes', 180)

    def get_author_preferred_languages(self) -> Optional[Set[str]]:
        """Return a normalized set of preferred languages for author catalog filtering."""

        raw_value = self.get_config_value('authors', 'preferred_languages', '')
        if raw_value is None:
            raw_value = ''

        normalized_tokens: Set[str] = set()

        for token in raw_value.replace(';', ',').split(','):
            clean_token = token.strip().lower()
            if not clean_token:
                continue
            if clean_token in {'*', 'all'}:
                return None
            normalized_tokens.add(clean_token)

        if not normalized_tokens:
            return {'english', 'en'}

        return normalized_tokens
    
    def export_config(self, include_secrets: bool = False) -> Dict[str, Any]:
        """Export configuration for backup/transfer.

        Credentials are masked unless ``include_secrets`` is explicitly set, so an
        export can't leak passwords/API keys by default. A masked export can
        still be re-imported: masked values keep the currently stored secret.
        """
        config_dict = self.list_config()
        if not include_secrets:
            config_dict = secrets_crypto.redact_config(config_dict)
        return self.export_import.export_config(config_dict)

    @_writes()
    def import_config(self, config_data: Dict[str, Any]) -> bool:
        """Import configuration from backup/transfer.

        Written through _write_config() so secrets are encrypted at rest, the
        write is atomic and the file stays 0600. Masked values keep the stored
        secret instead of overwriting it with the placeholder.
        """
        try:
            if not isinstance(config_data, dict) or not isinstance(config_data.get('config'), dict):
                self.logger.error("Invalid configuration data format")
                return False

            current = self.load_config()
            config = configparser.ConfigParser()
            for section_name, section_data in config_data['config'].items():
                if not isinstance(section_data, dict):
                    continue
                config.add_section(section_name)
                for key, value in section_data.items():
                    if secrets_crypto.is_sensitive_key(key) and secrets_crypto.is_masked_value(value):
                        value = current.get(section_name, key, fallback='')
                    config.set(section_name, key, str(value))

            self._write_config(config)
            self.logger.info("Configuration imported", extra={"config_file": self.config_file})
            return True
        except Exception:
            self.logger.exception("Failed to import configuration", extra={"config_file": self.config_file})
            return False
    
    def backup_config(self, backup_dir: str = None) -> str:
        """Create a backup of the current configuration."""
        return self.export_import.backup_config(backup_dir)
    
    def restore_config(self, backup_path: str) -> bool:
        """Restore configuration from backup."""
        return self.export_import.restore_config(backup_path)
    
    def reset_to_defaults(self) -> bool:
        """Reset configuration to default values."""
        return self.export_import.reset_to_defaults(self.defaults)
    
    def validate_config(self) -> Dict[str, bool]:
        """Validate configuration sections and return status."""
        config_dict = self.list_config()
        return self.validation.validate_config(config_dict)
    
    def get(self, key: str, default=None):
        """Get configuration value by key with dot notation support."""
        try:
            config = self.load_config()
            
            if '.' in key:
                section, option = key.split('.', 1)
                if config.has_section(section) and config.has_option(section, option):
                    return config.get(section, option)
            else:
                for section in config.sections():
                    if config.has_option(section, key):
                        return config.get(section, key)
            
            return default
        except Exception:
            self.logger.exception(
                "Error getting config key",
                extra={"key": key, "config_file": self.config_file},
            )
            return default
    
    def get_aggregator_connection(self, aggregator: str) -> Dict[str, Any]:
        """Get base_url/api_key connection settings for an aggregator (prowlarr/nzbhydra2)."""
        section = aggregator.lower()
        try:
            config = self.load_config()
            if not config.has_section(section):
                return {'base_url': '', 'api_key': '', 'verify_ssl': True, 'timeout': 30}
            return {
                'base_url': config.get(section, 'base_url', fallback=''),
                'api_key': config.get(section, 'api_key', fallback=''),
                'verify_ssl': config.getboolean(section, 'verify_ssl', fallback=True),
                'timeout': config.getint(section, 'timeout', fallback=30),
            }
        except Exception:
            self.logger.exception("Error reading aggregator connection", extra={"aggregator": aggregator})
            return {'base_url': '', 'api_key': '', 'verify_ssl': True, 'timeout': 30}

    @_writes()
    def set_aggregator_connection(self, aggregator: str, base_url: str, api_key: str,
                                   verify_ssl: bool = True, timeout: int = 30) -> bool:
        """Persist base_url/api_key connection settings for an aggregator."""
        section = aggregator.lower()
        try:
            config = self.load_config()
            if not config.has_section(section):
                config.add_section(section)
            if secrets_crypto.is_masked_value(api_key):
                api_key = config.get(section, 'api_key', fallback='')
            config.set(section, 'base_url', (base_url or '').rstrip('/'))
            config.set(section, 'api_key', api_key or '')
            config.set(section, 'verify_ssl', str(verify_ssl).lower())
            config.set(section, 'timeout', str(int(timeout)))
            self._write_config(config)
            self.logger.info("Saved aggregator connection", extra={"aggregator": aggregator})
            return True
        except Exception:
            self.logger.exception("Failed to save aggregator connection", extra={"aggregator": aggregator})
            return False

    def get_section(self, section_name: str) -> Dict[str, Any]:
        """Get all configuration values from a specific section."""
        try:
            config = self.load_config()
            if not config.has_section(section_name):
                return {}
            
            section_dict = {}
            for key, value in config.items(section_name):
                if value.lower() in ('true', 'false'):
                    section_dict[key] = config.getboolean(section_name, key)
                elif value.isdigit():
                    section_dict[key] = config.getint(section_name, key)
                else:
                    section_dict[key] = value
            
            return section_dict
        except Exception:
            self.logger.exception(
                "Error getting configuration section",
                extra={"section": section_name, "config_file": self.config_file},
            )
            return {}

    # --- Indexer configuration helpers ---

    def list_indexers_config(self) -> Dict[str, Dict[str, Any]]:
        """Return all configured indexers keyed by indexer identifier."""
        config = self.load_config()
        indexers = self._extract_indexer_sections(config)

        if indexers:
            return indexers

        legacy_path = os.path.join(os.path.dirname(self.config_file), 'indexers.json')
        if not os.path.exists(legacy_path):
            return {}

        if self._migrate_legacy_indexers(legacy_path):
            config = self.load_config()
            return self._extract_indexer_sections(config)

        return {}

    def get_indexer_config(self, indexer_key: str) -> Dict[str, Any]:
        """Get configuration dictionary for a specific indexer."""
        config = self.load_config()
        section_name = self._get_indexer_section_name(indexer_key)
        if not config.has_section(section_name):
            return {}
        return self._parse_indexer_section(config, section_name)

    @_writes()
    def set_indexer_config(self, indexer_key: str, config_data: Dict[str, Any]) -> bool:
        """Persist configuration for a specific indexer."""
        section_name = self._get_indexer_section_name(indexer_key)
        try:
            config = self.load_config()
            previous = dict(config.items(section_name)) if config.has_section(section_name) else {}
            if config.has_section(section_name):
                config.remove_section(section_name)
            config.add_section(section_name)

            normalized = self._normalize_indexer_config(config_data)
            for key, value in normalized.items():
                if secrets_crypto.is_sensitive_key(key) and secrets_crypto.is_masked_value(value):
                    value = previous.get(key, '')  # placeholder from the UI: keep the stored secret
                config.set(section_name, key, value)

            self._write_config(config)
            self.logger.info(
                "Saved indexer configuration",
                extra={"indexer": indexer_key, "config_file": self.config_file},
            )
            return True
        except Exception:
            self.logger.exception(
                "Failed to save indexer configuration",
                extra={"indexer": indexer_key, "config_file": self.config_file},
            )
            return False

    @_writes()
    def delete_indexer_config(self, indexer_key: str) -> bool:
        """Remove configuration for a specific indexer."""
        section_name = self._get_indexer_section_name(indexer_key)
        try:
            config = self.load_config()
            if not config.has_section(section_name):
                return True
            config.remove_section(section_name)
            self._write_config(config)
            self.logger.info(
                "Removed indexer configuration",
                extra={"indexer": indexer_key, "config_file": self.config_file},
            )
            return True
        except Exception:
            self.logger.exception(
                "Failed to remove indexer configuration",
                extra={"indexer": indexer_key, "config_file": self.config_file},
            )
            return False

    def get_enabled_services(self) -> Dict[str, bool]:
        """Get status of enabled services."""
        services = {}
        config = self.load_config()
        
        service_sections = ['qbittorrent', 'jackett', 'prowlarr', 'nzbhydra2', 'librivox']
        
        for section in service_sections:
            if config.has_section(section):
                services[section] = config.getboolean(section, 'enabled', fallback=False)
            else:
                services[section] = False
        
        return services

    # --- Internal helpers ---

    def _write_config(self, config: configparser.ConfigParser) -> None:
        """Persist the current configuration parser to disk."""
        self._encrypt_sensitive_values(config)
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                delete=False,
                dir=os.path.dirname(self.config_file),
                prefix=os.path.basename(self.config_file) + ".",
                suffix=".tmp",
            ) as configfile:
                temp_path = configfile.name
                config.write(configfile)
                configfile.flush()
                os.fsync(configfile.fileno())
            os.replace(temp_path, self.config_file)
        except FileNotFoundError:
            with open(self.config_file, "w", encoding="utf-8") as configfile:
                config.write(configfile)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        try:
            os.chmod(self.config_file, 0o600)
        except OSError:
            pass

    @staticmethod
    def _decrypt_sensitive_values(config: configparser.ConfigParser) -> None:
        """Decrypt credential-shaped values in place after reading from disk.

        Legacy plaintext (written before encryption-at-rest existed) passes
        through unchanged and gets encrypted transparently on the next save -
        no explicit migration step needed.
        """
        for section in config.sections():
            for option in config.options(section):
                if secrets_crypto.is_sensitive_key(option):
                    raw = config.get(section, option, fallback="")
                    if raw:
                        config.set(section, option, secrets_crypto.decrypt_value(raw))

    @staticmethod
    def _encrypt_sensitive_values(config: configparser.ConfigParser) -> None:
        """Encrypt credential-shaped values in place before writing to disk.

        Idempotent - values already encrypted (e.g. untouched fields carried
        through from a prior load_config()) are left as-is, so this is safe
        to call on every write regardless of what actually changed.
        """
        for section in config.sections():
            for option in config.options(section):
                if secrets_crypto.is_sensitive_key(option):
                    raw = config.get(section, option, fallback="")
                    if raw:
                        config.set(section, option, secrets_crypto.encrypt_value(raw))

    @staticmethod
    def _coerce_value(value: Any) -> str:
        """Normalize configuration values to strings."""
        if isinstance(value, bool):
            return 'true' if value else 'false'
        return '' if value is None else str(value)

    @_writes(announce=False)
    def _recover_from_duplicate_sections(self) -> configparser.ConfigParser:
        """Attempt to repair duplicate sections by rewriting a clean copy."""
        recovery_parser = configparser.ConfigParser(strict=False)
        try:
            with open(self.config_file, "r", encoding="utf-8") as config_handle:
                recovery_parser.read_file(config_handle)

            cleaned_parser = configparser.ConfigParser()
            for section in recovery_parser.sections():
                cleaned_parser[section] = {key: value for key, value in recovery_parser.items(section)}

            self._write_config(cleaned_parser)
            self.logger.info(
                "Duplicate sections removed; configuration rewritten",
                extra={"config_file": self.config_file},
            )
            self._decrypt_sensitive_values(cleaned_parser)
            return cleaned_parser
        except Exception:
            self.logger.exception(
                "Failed to recover configuration from duplicate sections",
                extra={"config_file": self.config_file},
            )
            return configparser.ConfigParser()

    # --- Indexer helper methods ---

    def _extract_indexer_sections(self, config: configparser.ConfigParser) -> Dict[str, Dict[str, Any]]:
        indexers: Dict[str, Dict[str, Any]] = {}
        for section in config.sections():
            if not section.startswith('indexer:'):
                continue
            key = section.split(':', 1)[1]
            indexers[key] = self._parse_indexer_section(config, section)
        return indexers

    def _parse_indexer_section(self, config: configparser.ConfigParser, section: str) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        items = dict(config.items(section))

        data['name'] = items.get('name', '')
        data['enabled'] = config.getboolean(section, 'enabled', fallback=False)
        data['private'] = config.getboolean(section, 'private', fallback=False)
        data['feed_url'] = items.get('feed_url', '')
        data['base_url'] = items.get('base_url', '')
        data['api_key'] = items.get('api_key', '')
        data['session_id'] = items.get('session_id', '')
        data['type'] = items.get('type', 'jackett')
        data['protocol'] = items.get('protocol', 'torznab')
        data['search_type'] = items.get('search_type', 'all').lower() or 'all'
        data['priority'] = config.getint(section, 'priority', fallback=999)
        categories = items.get('categories', '')
        if categories:
            data['categories'] = [cat.strip() for cat in categories.split(',') if cat.strip()]
        else:
            data['categories'] = []
        data['indexer_id'] = config.getint(section, 'indexer_id', fallback=0)
        data['indexer_name'] = items.get('indexer_name', '')
        data['verify_ssl'] = config.getboolean(section, 'verify_ssl', fallback=True)
        data['timeout'] = config.getint(section, 'timeout', fallback=30)

        rps = items.get('rate_limit_requests_per_second') or items.get('rate_limit.request_per_second')
        max_concurrent = items.get('rate_limit_max_concurrent') or items.get('rate_limit.max_concurrent')
        data['rate_limit'] = {
            'requests_per_second': int(rps) if self._is_int(rps) else 1,
            'max_concurrent': int(max_concurrent) if self._is_int(max_concurrent) else 1
        }

        return data

    @staticmethod
    def _is_int(value: Any) -> bool:
        try:
            int(value)
            return True
        except (TypeError, ValueError):
            return False

    def _normalize_indexer_config(self, config_data: Dict[str, Any]) -> Dict[str, str]:
        normalized: Dict[str, str] = {}

        normalized['name'] = self._coerce_value(config_data.get('name', ''))
        normalized['enabled'] = self._coerce_value(config_data.get('enabled', False))
        normalized['private'] = self._coerce_value(config_data.get('private', False))
        normalized['feed_url'] = self._coerce_value(config_data.get('feed_url', ''))
        normalized['base_url'] = self._coerce_value(config_data.get('base_url', ''))
        normalized['api_key'] = self._coerce_value(config_data.get('api_key', ''))
        normalized['session_id'] = self._coerce_value(config_data.get('session_id', ''))
        normalized['type'] = self._coerce_value(config_data.get('type', 'jackett')).lower()
        normalized['protocol'] = self._coerce_value(config_data.get('protocol', 'torznab')).lower()
        normalized['search_type'] = self._coerce_value(str(config_data.get('search_type', 'all')).lower()) or 'all'
        normalized['priority'] = self._coerce_value(config_data.get('priority', 999))

        categories = config_data.get('categories') or []
        if isinstance(categories, (list, tuple)):
            categories_value = ','.join(str(cat).strip() for cat in categories if str(cat).strip())
        else:
            categories_value = self._coerce_value(categories)
        normalized['categories'] = categories_value

        normalized['indexer_id'] = self._coerce_value(config_data.get('indexer_id', 0))
        normalized['indexer_name'] = self._coerce_value(config_data.get('indexer_name', ''))
        normalized['verify_ssl'] = self._coerce_value(config_data.get('verify_ssl', True))
        normalized['timeout'] = self._coerce_value(config_data.get('timeout', 30))

        rate_limit = config_data.get('rate_limit') or {}
        normalized['rate_limit_requests_per_second'] = self._coerce_value(rate_limit.get('requests_per_second', 1))
        normalized['rate_limit_max_concurrent'] = self._coerce_value(rate_limit.get('max_concurrent', 1))

        return normalized

    @staticmethod
    def _get_indexer_section_name(indexer_key: str) -> str:
        return f"indexer:{indexer_key.strip().lower()}"

    def _migrate_legacy_indexers(self, legacy_path: str) -> bool:
        """Migrate indexer data from legacy JSON file to config.txt."""
        try:
            with open(legacy_path, 'r', encoding='utf-8') as legacy_file:
                legacy_indexers = json.load(legacy_file)
        except Exception:
            self.logger.exception(
                "Failed to read legacy indexers.json",
                extra={"legacy_path": legacy_path},
            )
            return False

        migrated_any = False
        for key, config_data in legacy_indexers.items():
            if not isinstance(config_data, dict):
                continue
            if self.set_indexer_config(key, config_data):
                migrated_any = True

        if migrated_any:
            try:
                backup_path = f"{legacy_path}.legacy"
                os.replace(legacy_path, backup_path)
                self.logger.info(
                    "Migrated legacy indexers.json to config.txt",
                    extra={"backup_path": backup_path, "legacy_path": legacy_path, "config_file": self.config_file},
                )
            except OSError as exc:
                self.logger.warning(
                    "Migrated indexers but failed to archive legacy file",
                    extra={"legacy_path": legacy_path, "backup_error": str(exc)},
                )

        return migrated_any