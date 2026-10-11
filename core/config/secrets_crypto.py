"""
Module Name: secrets_crypto.py
Author: AuralArchivist
Created: July 7, 2026
Last Modified: October 9, 2026
Description:
    Encrypts credential values in config.txt at the ConfigService boundary.

Location:
    /core/config/secrets_crypto.py
"""

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from core.logging import get_module_logger
from core.paths import get_path_resolver

_LOGGER = get_module_logger("Core.Config.SecretsCrypto")

ENCRYPTED_PREFIX = "enc:"

_SENSITIVE_SUBSTRINGS = (
    "password",
    "token",
    "api_key",
    "apikey",
    "secret",
    "session_id",
    "session_cookie",
    "auth_key",
    "private_key",
    "access_key",
)

_fernet: Optional[Fernet] = None


def _key_file_path() -> str:
    return get_path_resolver().get_secret_file("encryption_key")


def _load_or_generate_key() -> bytes:
    key_file = _key_file_path()
    try:
        if os.path.exists(key_file):
            with open(key_file, "rb") as fh:
                key = fh.read().strip()
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            if key:
                return key
    except OSError:
        pass

    key = Fernet.generate_key()
    try:
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, "wb") as fh:
            fh.write(key)
        os.chmod(key_file, 0o600)
        _LOGGER.info("Generated new config encryption key and persisted to encryption_key")
    except OSError as exc:
        _LOGGER.warning(f"Could not persist config encryption key: {exc} - key will change on restart")

    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_generate_key())
    return _fernet


def get_audible_auth_password() -> str:
    """Password used to encrypt the Audible auth file (device tokens, adp_token,
    private key). Reuses the same persisted key backing config.txt encryption -
    one master secret to protect instead of a second standalone file."""
    return _load_or_generate_key().decode("ascii")


def is_sensitive_key(option_name: str) -> bool:
    """Return True if a config option name looks like it holds a credential."""
    lowered = (option_name or "").lower()
    return any(marker in lowered for marker in _SENSITIVE_SUBSTRINGS)


MASKED_VALUE = "••••••••"
_MASK_PLACEHOLDERS = {MASKED_VALUE, "***", "••••", "REDACTED", "MASKED"}


def is_masked_value(value) -> bool:
    """True if a submitted value is a mask placeholder rather than a real secret."""
    return isinstance(value, str) and value.strip() in _MASK_PLACEHOLDERS


def redact_config(data):
    """Return a copy of ``data`` with every credential-named string value masked.

    Walks nested dicts and lists. Only non-empty *string* values under a
    sensitive key name are replaced, so flags such as ``has_api_key: true``
    and already-masked display strings pass through meaningfully.
    """
    if isinstance(data, dict):
        redacted = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                redacted[key] = redact_config(value)
            elif isinstance(value, str) and value and is_sensitive_key(str(key)):
                redacted[key] = MASKED_VALUE
            else:
                redacted[key] = value
        return redacted
    if isinstance(data, list):
        return [redact_config(item) for item in data]
    return data


def encrypt_value(value: str) -> str:
    """Encrypt a plaintext value for disk storage. Idempotent - a value that's
    already encrypted (or empty) is returned unchanged, so callers can freely
    re-run this over values that may or may not already be encrypted."""
    if not value or value.startswith(ENCRYPTED_PREFIX):
        return value
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return ENCRYPTED_PREFIX + token


_decrypt_failures = 0


def decryption_failure_count() -> int:
    """Number of config values that failed to decrypt since the process started."""
    return _decrypt_failures


def decrypt_value(value: str) -> str:
    """Decrypt a value read from disk. Legacy plaintext (no enc: prefix) is
    returned unchanged, so config.txt written before this feature existed
    keeps working and gets encrypted transparently on the next save.

    On failure the original ``enc:`` token is returned rather than "": the
    token is useless as a credential either way, but because encrypt_value()
    leaves ``enc:`` values untouched, the next config save writes it back
    as-is instead of overwriting it with a blank. Restoring the right key
    file therefore recovers every secret.
    """
    global _decrypt_failures
    if not value or not value.startswith(ENCRYPTED_PREFIX):
        return value
    token = value[len(ENCRYPTED_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        _decrypt_failures += 1
        _LOGGER.error(
            "Failed to decrypt a config value - the encryption key file was probably "
            f"lost or replaced ({_key_file_path()}): {exc!r}"
        )
        return value
