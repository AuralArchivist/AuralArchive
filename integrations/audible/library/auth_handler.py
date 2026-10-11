"""
Module Name: auth_handler.py
Author: AuralArchivist
Created: August 21, 2025
Last Modified: October 9, 2026
Description:
    Manage Audible auth token discovery and validation for library operations.

Location:
    /integrations/audible/library/auth_handler.py
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

import audible
from core.logging import get_module_logger
from core.paths import resolve_audible_auth_file
from core.config.secrets_crypto import get_audible_auth_password


class AudibleAuthHandler:
    """
    Handles authentication and credential management for Audible services.

    This implementation relies entirely on the Python audible package and the
    modal-based authentication flow that stores tokens in auth/audible_auth.json.
    """

    def __init__(self, config_service=None, logger=None):
        self.config_service = config_service
        self.logger = logger or get_module_logger("Service.Audible.Auth")

        self.default_auth_path = Path(resolve_audible_auth_file())

        self.auth_status: Optional[Dict[str, Any]] = None
        self.profile_info: Dict[str, str] = {}

        self.logger.debug(
            "AudibleAuthHandler initialized",
            extra={"default_auth_path": str(self.default_auth_path)}
        )

    def get_default_auth_file(self) -> str:
        """Return the default auth file path used by the application."""
        return str(self.default_auth_path)

    def _collect_auth_candidate_paths(self) -> List[Path]:
        """Gather potential auth token file locations."""
        candidates: List[Path] = []

        def _add(path: Optional[Path]) -> None:
            if not path:
                return
            try:
                candidates.append(path.expanduser())
            except RuntimeError:
                candidates.append(Path(path))

        _add(self.default_auth_path)

        if self.config_service:
            try:
                config_value = self.config_service.get_config_value('audible', 'auth_file')
                if config_value:
                    _add(Path(config_value))
            except Exception as exc:
                self.logger.debug(
                    "Unable to read audible auth file path from config",
                    extra={"exc": exc}
                )

        env_auth = os.getenv("AURALARCHIVE_AUDIBLE_AUTH_FILE") or os.getenv("AUDIBLE_AUTH_FILE")
        if env_auth:
            _add(Path(env_auth))

        possible_dirs = [
            self.default_auth_path.parent,
            Path.home() / ".audible",
            Path.home() / ".config" / "audible"
        ]

        for directory in possible_dirs:
            try:
                if directory.is_file():
                    _add(directory)
                    continue
                if not directory.is_dir():
                    continue
                for pattern in ("*.json", "*.auth", "*.cfg", "*.ini", "*.toml"):
                    for candidate in directory.glob(pattern):
                        _add(candidate)
            except OSError as exc:
                self.logger.debug(
                    "Failed checking candidate directory",
                    extra={"directory": str(directory), "exc": exc}
                )

        unique_paths: List[Path] = []
        seen: set = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                resolved = candidate
            fingerprint = str(resolved)
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique_paths.append(resolved)
        return unique_paths

    def _is_default_path(self, path: Path) -> bool:
        try:
            return path.resolve() == self.default_auth_path.resolve()
        except (OSError, RuntimeError):
            return str(path) == str(self.default_auth_path)

    def _profile_name_for_path(self, path: Path) -> str:
        if self._is_default_path(path):
            return 'default'

        base = (path.stem or path.name).lower().replace(' ', '_')
        candidate = base
        suffix = 2
        while candidate in self.profile_info:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def discover_auth_files(self) -> List[Dict[str, Any]]:
        """Discover available auth files and basic metadata."""
        files: List[Dict[str, Any]] = []
        self.profile_info = {}

        for candidate in self._collect_auth_candidate_paths():
            info: Dict[str, Any] = {
                'path': str(candidate),
                'exists': candidate.exists(),
                'is_default': self._is_default_path(candidate)
            }

            if candidate.exists():
                try:
                    stats = candidate.stat()
                    info['size'] = stats.st_size
                    info['modified'] = datetime.fromtimestamp(stats.st_mtime).isoformat()
                except OSError as exc:
                    info['error'] = str(exc)

                profile_name = self._profile_name_for_path(candidate)
                self.profile_info[profile_name] = str(candidate)
                info['profile'] = profile_name

            files.append(info)

        return files

    def _ensure_profile_map(self) -> None:
        if not self.profile_info:
            self.discover_auth_files()

    def _resolve_auth_path(self, profile: Optional[str], auth_file: Optional[str]) -> Path:
        if auth_file:
            return Path(auth_file).expanduser()

        self._ensure_profile_map()

        if profile and profile in self.profile_info:
            return Path(self.profile_info[profile])

        if profile:
            potential = Path(profile).expanduser()
            if potential.exists():
                return potential

        return self.default_auth_path

    def test_authentication(self, profile: Optional[str] = None, auth_file: Optional[str] = None) -> Dict[str, Any]:
        """Test that the stored authentication token is usable."""
        try:
            path = self._resolve_auth_path(profile, auth_file)

            if not path.exists():
                self.logger.warning(
                    "Authentication test failed - auth file not found",
                    extra={"profile": profile or "default", "auth_file": str(path)}
                )
                return {
                    'authenticated': False,
                    'profile': profile or 'default',
                    'auth_file': str(path),
                    'error': f'Auth file not found: {path}',
                    'message': 'Authentication test failed'
                }

            auth = audible.Authenticator.from_file(str(path), password=get_audible_auth_password())
            with audible.Client(auth=auth) as client:
                account_info = client.get("1.0/account/information")

            profile_name = profile or self._profile_name_for_path(path)
            self.profile_info[profile_name] = str(path)

            account_data = {
                'name': account_info.get('name'),
                'marketplace': account_info.get('marketplace'),
                'email': account_info.get('email'),
                'account_id': account_info.get('customer_id') or account_info.get('id')
            }

            self.logger.info(
                "Authentication test successful",
                extra={"profile": profile_name, "auth_file": str(path)}
            )
            return {
                'authenticated': True,
                'profile': profile_name,
                'auth_file': str(path),
                'account': account_data,
                'message': 'Authentication test successful'
            }

        except Exception as exc:
            resolved_profile = profile or 'default'
            self.logger.warning(
                "Authentication test failed",
                extra={"profile": resolved_profile, "auth_file": str(path) if 'path' in locals() else None, "exc": exc}
            )
            return {
                'authenticated': False,
                'profile': resolved_profile,
                'auth_file': str(path) if 'path' in locals() else None,
                'error': str(exc),
                'message': 'Authentication test failed'
            }

    def check_existing_authentication(self) -> Dict[str, Any]:
        """Check if authentication tokens exist and optionally validate them."""
        try:
            auth_files = self.discover_auth_files()
            existing_files = [entry for entry in auth_files if entry.get('exists')]

            if not existing_files:
                return {
                    'has_auth': False,
                    'message': 'No Audible authentication tokens found',
                    'config_path': str(self.default_auth_path.parent),
                    'config_file': str(self.default_auth_path),
                    'profiles': []
                }

            profiles = [entry.get('profile', 'default') for entry in existing_files if entry.get('profile')]
            validation_result = None
            errors: List[str] = []

            for profile in profiles or ['default']:
                validation_result = self.test_authentication(profile=profile)
                if validation_result.get('authenticated'):
                    break
                if validation_result.get('error'):
                    errors.append(validation_result['error'])

            response: Dict[str, Any] = {
                'has_auth': True,
                'config_path': str(self.default_auth_path.parent),
                'config_file': str(self.default_auth_path),
                'profiles': profiles or ['default'],
                'auth_files': existing_files
            }

            if validation_result and validation_result.get('authenticated'):
                response['validated'] = True
                response['active_profile'] = validation_result.get('profile')
                response['account'] = validation_result.get('account')
                account_name = validation_result.get('account', {}).get('name') or validation_result.get('profile')
                response['message'] = f"Authentication available for {account_name}"
            else:
                response['validated'] = False
                response['message'] = 'Authentication tokens found but validation failed. Re-authenticate to refresh tokens.'
                if errors:
                    response['errors'] = errors

            return response

        except Exception as exc:
            self.logger.error(
                "Error checking existing authentication",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'has_auth': False,
                'error': str(exc),
                'message': f'Error checking authentication: {exc}'
            }
    
    def get_authentication_instructions(self) -> Dict[str, Any]:
        """Get instructions for setting up Audible authentication."""
        return {
            'title': 'Audible Authentication Setup',
            'description': 'Connect your Audible account using the built-in authentication flow in AuralArchive.',
            'requirements': [
                'Valid Audible account with purchased audiobooks',
                'Access to the email or phone number used for Audible (for OTP verification)',
                'Active internet connection during authentication'
            ],
            'steps': [
                {
                    'step': 1,
                    'title': 'Open Settings > Audible',
                    'description': 'Select the Authenticate action to start the sign-in flow inside AuralArchive.'
                },
                {
                    'step': 2,
                    'title': 'Submit your Audible credentials',
                    'description': 'Enter the email and password associated with your Audible account.'
                },
                {
                    'step': 3,
                    'title': 'Complete two-factor verification',
                    'description': 'Enter the one-time passcode sent by Audible to finish authentication.'
                },
                {
                    'step': 4,
                    'title': 'Sync your library',
                    'description': 'Use Quick Sync for recent changes or Full Sync for a complete refresh once authentication succeeds.'
                }
            ],
            'security_notes': [
                'Authentication tokens are stored securely on your server.',
                'You can revoke access at any time from the Audible settings tab.',
                'AuralArchive only requests data needed to manage your owned titles.'
            ],
            'troubleshooting': [
                'Verify your Audible credentials before submitting the form.',
                'Ensure you can receive the one-time passcode via email or SMS.',
                'Refresh the settings page and retry if the session times out.',
                'Use Validate Credentials to confirm your session remains active.'
            ]
        }
    
    def validate_credentials(self) -> Dict[str, Any]:
        """Validate existing credentials and return status."""
        try:
            auth_check = self.check_existing_authentication()

            if not auth_check.get('has_auth'):
                return {
                    'valid': False,
                    'needs_setup': True,
                    'message': 'No authentication found. Setup required.',
                    'instructions': self.get_authentication_instructions()
                }

            profiles = auth_check.get('profiles') or ['default']
            last_result = None

            for profile in profiles:
                last_result = self.test_authentication(profile=profile)
                if last_result.get('authenticated'):
                    return {
                        'valid': True,
                        'profile': last_result.get('profile'),
                        'available_profiles': profiles,
                        'test_result': last_result,
                        'account': last_result.get('account'),
                        'auth_file': last_result.get('auth_file'),
                        'message': 'Authentication tested successfully'
                    }

            return {
                'valid': False,
                'available_profiles': profiles,
                'test_result': last_result,
                'message': 'Authentication tokens found but validation failed. Re-authenticate via Settings > Audible.'
            }

        except Exception as exc:
            self.logger.error(
                "Error validating credentials",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'valid': False,
                'error': str(exc),
                'message': f'Error validating credentials: {exc}'
            }
