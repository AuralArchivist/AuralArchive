"""
Module Name: config.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Loads environment configuration and central application defaults.

Location:
    /core/config/config.py
"""

import os
from dotenv import load_dotenv

from core.logging import get_module_logger
from core.paths import get_path_resolver

load_dotenv()

_LOGGER = get_module_logger("Config.Config")


_PLACEHOLDER_KEY = 'dev-secret-key-change-in-production'


def _load_or_generate_secret_key() -> str:
    """
    Return the Flask SECRET_KEY using this priority order:
    1. SECRET_KEY env var (if set and not the placeholder)
    2. Persisted key file at {data_dir}/secrets/secret_key
    3. Generate a new random key, persist it, and return it

    Generating once and persisting means sessions survive server restarts
    without the user needing to manually configure anything.
    """
    import secrets as _secrets

    env_key = os.environ.get('SECRET_KEY', '')
    if env_key and env_key != _PLACEHOLDER_KEY:
        return env_key

    key_file = get_path_resolver().get_secret_file('secret_key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as fh:
                stored = fh.read().strip()
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            if stored and stored != _PLACEHOLDER_KEY:
                return stored
    except OSError:
        pass

    new_key = _secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, 'w', encoding='utf-8') as fh:
            fh.write(new_key)
        os.chmod(key_file, 0o600)
        _LOGGER.info("Generated new SECRET_KEY and persisted to secrets/secret_key")
    except OSError as exc:
        _LOGGER.warning(f"Could not persist SECRET_KEY: {exc} - key will change on restart")

    return new_key


class Config:
    SECRET_KEY = _load_or_generate_secret_key()
    DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes')
    
    DATABASE_URL = os.environ.get('DATABASE_URL') or f"sqlite:///{get_path_resolver().get_database_path()}"
    
    LOG_LEVEL = os.environ.get('LOG_LEVEL') or 'INFO'
    LOG_FILE = os.environ.get('LOG_FILE') or 'auralarchive_web.log'
    
    SOCKETIO_ASYNC_MODE = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')
    
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024 * 1024

    AUDIBLE_DEBUG_TOKEN = os.environ.get('AUDIBLE_DEBUG_TOKEN')
    
    MONITOR_ENABLED = os.environ.get('MONITOR_ENABLED', 'true').lower() == 'true'
    
    REMEMBER_COOKIE_DURATION = 30 * 24 * 60 * 60
    SESSION_COOKIE_SECURE = os.environ.get('HTTPS_ENABLED', 'false').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'

    TRUSTED_PROXY_COUNT = int(os.environ.get('TRUSTED_PROXY_COUNT', '0') or 0)

    _cors_env = os.environ.get('CORS_ALLOWED_ORIGINS', '')
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(',') if o.strip()] if _cors_env else None
    
    INDEXERS = {}


_LOGGER.info(
    "Config loaded",
    extra={
        "log_level": Config.LOG_LEVEL,
        "log_file": Config.LOG_FILE,
        "monitor_enabled": Config.MONITOR_ENABLED,
        "socketio_async_mode": Config.SOCKETIO_ASYNC_MODE,
    },
)
