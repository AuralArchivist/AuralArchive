"""
Module Name: error_handling.py
Author: AuralArchivist
Created: August 18, 2025
Last Modified: October 9, 2026
Description:
    Response validation and logging helpers for Audible catalog requests.

Location:
    /integrations/audible/catalog/error_handling.py
"""

from typing import Optional

import requests

from core.logging import get_module_logger

class AudibleErrorHandler:
    """Response validation and logging for Audible catalog requests"""
    
    def __init__(self):
        self.logger = get_module_logger("Service.Audible.CatalogErrorHandling")
    
    def validate_response(self, response: requests.Response, operation: str) -> bool:
        """Validate API response and log appropriately"""
        try:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if isinstance(data, dict) and 'products' in data:
                        product_count = len(data.get('products', []))
                        self.logger.debug("Operation successful", extra={"operation": operation, "product_count": product_count})
                        return True
                    else:
                        self.logger.warning("Unexpected data format", extra={"operation": operation})
                        return False
                except ValueError as e:
                    self.logger.error("Invalid JSON response", extra={"operation": operation, "error": str(e)})
                    return False
            else:
                self.logger.error("Operation failed", extra={"operation": operation, "status_code": response.status_code, "reason": response.reason})
                return False
        
        except Exception as e:
            self.logger.exception("Error validating response", extra={"operation": operation, "error": str(e)})
            return False
    
    def log_request_info(self, url: str, params: dict, operation: str):
        """Log request information for debugging"""
        safe_params = {k: v for k, v in params.items() if k not in ['api_key', 'token']}
        self.logger.debug("Request info", extra={"operation": operation, "url": url, "params": safe_params})
    
    def handle_api_quota(self, response: requests.Response) -> Optional[int]:
        """Handle API quota information from response headers"""
        remaining = response.headers.get('X-RateLimit-Remaining')
        if remaining is None:
            return None
        try:
            remaining_requests = int(remaining)
        except ValueError:
            self.logger.debug("Unparseable X-RateLimit-Remaining header: %r", remaining)
            return None
        if remaining_requests < 10:
            self.logger.warning("API quota low", extra={"remaining": remaining_requests})
        return remaining_requests

error_handler = AudibleErrorHandler()
