"""
Module Name: response_redaction.py
Author: AuralArchivist
Created: September 23, 2026
Last Modified: October 9, 2026
Description:
    Masks credential fields in JSON responses from the settings, profile and
    hardcover blueprints.

Location:
    /core/response_redaction.py
"""

import json

from core.config.secrets_crypto import redact_config
from core.logging import get_module_logger

_logger = get_module_logger("Core.ResponseRedaction")


def redact_json_response(response):
    """Flask after_request hook: mask sensitive fields in JSON bodies."""
    if response.direct_passthrough or response.mimetype != 'application/json':
        return response
    try:
        payload = json.loads(response.get_data(as_text=True))
    except (ValueError, TypeError):
        return response
    redacted = redact_config(payload)
    if redacted != payload:
        response.set_data(json.dumps(redacted))
    return response


def install_response_redaction(app, blueprint_names):
    """Apply the JSON redaction hook to every response from the named blueprints.

    Registered once at app level (filtered by request.blueprint) so it works
    regardless of whether the blueprints were already registered.
    """
    from flask import request

    names = frozenset(blueprint_names)

    @app.after_request
    def _redact_sensitive_json(response):
        if request.blueprint in names:
            return redact_json_response(response)
        return response
