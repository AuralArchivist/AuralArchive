"""
Module Name: audible_api_helper.py
Author: AuralArchivist
Created: August 25, 2025
Last Modified: October 9, 2026
Description:
    Python API-based library access replacing CLI helpers for metadata sync workflows.

Location:
    /integrations/audible/metadata_sync/audible_api_helper.py
"""

import audible
import math
from typing import List, Dict, Any, Optional
from pathlib import Path
from core.logging import get_module_logger
from core.paths import resolve_audible_auth_file
from core.config.secrets_crypto import get_audible_auth_password

logger = get_module_logger("Service.Audible.MetadataSync.ApiHelper")


class AudibleApiHelper:
    """
    Helper class for Audible Python API operations.
    Uses the audible package directly instead of CLI subprocess calls.
    """
    
    def __init__(self, auth_file: str = None):
        self.auth_file = auth_file or resolve_audible_auth_file()
        self.auth = None
        self._load_auth()
    
    def _load_auth(self) -> bool:
        """Load authentication from file."""
        try:
            auth_path = Path(self.auth_file)
            if not auth_path.exists():
                logger.warning(
                    "Auth file not found",
                    extra={"auth_file": self.auth_file}
                )
                return False
            
            self.auth = audible.Authenticator.from_file(str(auth_path), password=get_audible_auth_password())
            logger.info(
                "Loaded Audible authentication",
                extra={"auth_file": self.auth_file}
            )
            return True
            
        except Exception as exc:
            logger.error(
                "Failed to load authentication",
                extra={"auth_file": self.auth_file, "exc": exc}, exc_info=True
            )
            return False
    
    def is_available(self) -> bool:
        """Check if the API helper is available and authenticated."""
        return self.auth is not None
    
    def get_library_list(self, purchased_after: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every item in the user's Audible library with full metadata, fetched page by page.

        Raises:
            Exception: If not authenticated or API call fails
        """
        if not self.auth:
            raise Exception("Not authenticated. Please authenticate via the web UI.")
        
        try:
            logger.info("Fetching library from Audible API")
            
            response_groups = (
                "contributors, customer_rights, media, price, product_attrs, "
                "product_desc, product_extended_attrs, product_plan_details, "
                "product_plans, rating, sample, sku, series, reviews, ws4v, "
                "relationships, review_attrs, categories, category_ladders, "
                "claim_code_url, in_wishlist, listening_status, periodicals, "
                "provided_review, product_details"
            )
            
            params = {
                'num_results': 1000,
                'page': 1,
                'response_groups': response_groups
            }
            
            if purchased_after:
                params['purchased_after'] = purchased_after
                logger.info(
                    "Filtering library items after purchase date",
                    extra={"purchased_after": purchased_after}
                )
            
            all_items = []
            
            with audible.Client(auth=self.auth) as client:
                logger.info("Fetching page 1")
                response = client.get("library", **params)
                
                items = response.get('items', [])
                total_results = response.get('total_results', 0)
                
                all_items.extend(items)
                logger.info(
                    "Fetched page",
                    extra={"page": 1, "items": len(items), "total_results": total_results}
                )
                
                total_pages = math.ceil(total_results / 1000)
                
                if total_pages > 1:
                    logger.info(
                        "Fetching remaining pages",
                        extra={"remaining_pages": total_pages - 1}
                    )
                    
                    for page in range(2, total_pages + 1):
                        params['page'] = page
                        logger.info(
                            "Fetching page",
                            extra={"page": page, "total_pages": total_pages}
                        )
                        
                        page_response = client.get("library", **params)
                        page_items = page_response.get('items', [])
                        
                        all_items.extend(page_items)
                        logger.info(
                            "Fetched page",
                            extra={"page": page, "items": len(page_items)}
                        )
            
            logger.info(
                "Library fetch complete",
                extra={"total_items": len(all_items)}
            )
            return all_items
            
        except Exception as exc:
            logger.error(
                "Error fetching library from API",
                extra={"exc": exc},
                exc_info=True
            )
            raise
    
    def raw_get(self, path: str, **params: Any) -> Dict[str, Any]:
        """Issue an arbitrary authenticated GET against the Audible API.

        Troubleshooting/debug escape hatch - callers are responsible for
        supplying a sane path (e.g. ``1.0/catalog/products/{asin}``) and
        query params (e.g. ``response_groups``). No GET-only restriction is
        enforced by the ``audible`` package itself, but this method only
        ever calls ``client.get`` so it can never mutate account state.
        """
        if not path or not str(path).strip():
            raise ValueError("path is required")

        if not self.auth and not self._load_auth():
            raise Exception("Not authenticated. Please authenticate via the web UI.")

        clean_params = {k: v for k, v in params.items() if v is not None and v != ''}

        logger.info("Issuing raw Audible API GET", extra={"path": path, "params": clean_params})

        with audible.Client(auth=self.auth) as client:
            response = client.get(path, **clean_params)

        return response or {}

    def get_chapter_info(self, asin: str) -> Dict[str, Any]:
        """Fetch chapter metadata for a title from the Audible content endpoint.

        Calls ``1.0/content/{asin}/metadata?response_groups=chapter_info`` - the
        authenticated, authoritative source for per-chapter timings of owned
        content. Returns the ``chapter_info`` mapping, which contains a
        ``chapters`` list (each entry has ``title``, ``start_offset_ms`` and
        ``length_ms``) plus ``runtime_length_ms`` and intro/outro durations.

        Returns:
            The ``chapter_info`` dict, or ``{}`` if unavailable.

        Raises:
            ValueError: If no ASIN is supplied.
            Exception: If not authenticated.
        """
        if not asin or not str(asin).strip():
            raise ValueError("ASIN is required")

        if not self.auth and not self._load_auth():
            raise Exception("Not authenticated. Please authenticate via the web UI.")

        path = f"1.0/content/{str(asin).strip()}/metadata"
        logger.info("Fetching Audible chapter info", extra={"asin": asin})

        with audible.Client(auth=self.auth) as client:
            response = client.get(path, response_groups="chapter_info")

        chapter_info = (response or {}).get('content_metadata', {}).get('chapter_info', {})
        logger.info(
            "Audible chapter info fetched",
            extra={"asin": asin, "chapter_count": len(chapter_info.get('chapters', []) or [])}
        )
        return chapter_info or {}

    def check_status(self) -> Dict[str, Any]:
        """Check the status of the API helper."""
        try:
            if not self.auth:
                return {
                    'available': False,
                    'authenticated': False,
                    'error': 'Not authenticated'
                }
            
            with audible.Client(auth=self.auth) as client:
                account_info = client.get("1.0/account/information")
            
            return {
                'available': True,
                'authenticated': True,
                'version': audible.__version__,
                'marketplace': account_info.get('marketplace'),
                'account': account_info.get('name')
            }
            
        except Exception as exc:
            logger.error(
                "Status check failed",
                extra={"exc": exc}, exc_info=True
            )
            return {
                'available': True,
                'authenticated': False,
                'error': str(exc)
            }

    def get_activation_bytes(self, reload: bool = False) -> Dict[str, Any]:
        """Retrieve activation bytes using the audible Python API."""
        try:
            if not self.auth and not self._load_auth():
                return {
                    'success': False,
                    'error': 'Not authenticated'
                }

            if reload:
                try:
                    self.auth.refresh_access_token()
                except Exception as exc:
                    logger.debug(
                        "Failed to refresh access token before reloading activation bytes",
                        extra={"exc": exc}
                    )

            activation_bytes = self.auth.get_activation_bytes()

            if not activation_bytes:
                return {
                    'success': False,
                    'error': 'Activation bytes unavailable'
                }

            normalized = self._normalize_activation_bytes(activation_bytes)

            try:
                self.auth.to_file(self.auth_file)
            except Exception as exc:
                logger.debug(
                    "Unable to persist activation bytes to auth file",
                    extra={"auth_file": self.auth_file, "exc": exc}
                )

            return {
                'success': True,
                'activation_bytes': normalized
            }

        except Exception as exc:
            logger.error(
                "Error retrieving activation bytes via API",
                extra={"auth_file": self.auth_file, "exc": exc}, exc_info=True
            )
            return {
                'success': False,
                'error': str(exc)
            }

    @staticmethod
    def _normalize_activation_bytes(raw: Any) -> str:
        """Normalize activation bytes into a consistent uppercase hex string."""
        if isinstance(raw, (bytes, bytearray)):
            return raw.hex().upper()
        if isinstance(raw, str):
            return raw.strip().upper()
        if isinstance(raw, dict):
            for key in ('activation_bytes', 'bytes', 'value'):
                if key in raw and raw[key]:
                    return AudibleApiHelper._normalize_activation_bytes(raw[key])
        return str(raw).strip()
