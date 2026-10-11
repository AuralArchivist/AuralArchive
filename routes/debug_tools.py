"""
Module Name: debug_tools.py
Author: AuralArchivist
Created: July 18, 2025
Last Modified: October 9, 2026
Description:
    Login-gated troubleshooting console for probing arbitrary Audible API
    calls with the user's own credentials.

Location:
    /routes/debug_tools.py
"""

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

from integrations.audible.metadata_sync.audible_api_helper import AudibleApiHelper
from services.container import container
from core.logging import get_module_logger

logger = get_module_logger("Routes.DebugTools")

debug_bp = Blueprint("debug_tools", __name__)

CALL_TYPES: Dict[str, Dict[str, Any]] = {
    "library_item": {
        "label": "GET /1.0/library/{asin} - single library item",
        "path": "1.0/library/{asin}",
        "needs_asin": True,
        "default_response_groups": (
            "contributors, media, price, product_attrs, product_desc, product_details, "
            "product_extended_attrs, product_plan_details, product_plans, rating, sample, sku, "
            "series, reviews, ws4v, origin, relationships, review_attrs, categories, "
            "badge_types, category_ladders, claim_code_url, is_downloaded, is_finished, "
            "is_returnable, origin_asin, pdf_url, percent_complete, periodicals, provided_review"
        ),
    },
    "catalog_product": {
        "label": "GET /1.0/catalog/products/{asin} - catalog lookup (no ownership required)",
        "path": "1.0/catalog/products/{asin}",
        "needs_asin": True,
        "default_response_groups": (
            "contributors, product_attrs, product_desc, product_extended_attrs, "
            "series, rating, media, relationships, customer_rights, sku"
        ),
    },
    "content_metadata": {
        "label": "GET /1.0/content/{asin}/metadata - chapter info (requires ownership)",
        "path": "1.0/content/{asin}/metadata",
        "needs_asin": True,
        "default_response_groups": "chapter_info",
    },
    "recommendations": {
        "label": "GET /1.0/recommendations - personalized recommendations (no ASIN)",
        "path": "1.0/recommendations",
        "needs_asin": False,
        "default_response_groups": (
            "contributors, media, product_attrs, product_desc, "
            "product_extended_attrs, rating, sample, sku"
        ),
    },
    "account_information": {
        "label": "GET /1.0/account/information - account/marketplace info",
        "path": "1.0/account/information",
        "needs_asin": False,
        "default_response_groups": "",
    },
    "custom": {
        "label": "Custom path - type any endpoint below",
        "path": "",
        "needs_asin": False,
        "default_response_groups": "",
    },
}

KNOWN_RESPONSE_GROUPS = sorted([
    "badge_types", "categories", "category_ladders", "chapter_info", "claim_code_url",
    "contributors", "customer_rights", "in_wishlist", "is_downloaded", "is_finished",
    "is_returnable", "listening_status", "media", "origin", "origin_asin", "pdf_url",
    "percent_complete", "periodicals", "price", "product_attrs", "product_desc",
    "product_details", "product_extended_attrs", "product_plan_details", "product_plans",
    "provided_review", "rating", "relationships", "review_attrs", "reviews", "sample",
    "series", "sku", "ws4v",
])


def _get_helper() -> Optional[AudibleApiHelper]:
    helper = AudibleApiHelper()
    if not helper.is_available():
        return None
    return helper


def _parse_extra_params(raw: str) -> Dict[str, str]:
    """Parse a 'key=value' per-line textarea into a params dict."""
    params: Dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            params[key] = value
    return params


@debug_bp.route("/tests/audible_api", methods=["GET"])
@login_required
def audible_api_tester():
    """Render the Audible API troubleshooting console."""
    helper = _get_helper()
    return render_template(
        "debug_audible.html",
        call_types=CALL_TYPES,
        known_response_groups=KNOWN_RESPONSE_GROUPS,
        authenticated=bool(helper),
    )


@debug_bp.route("/tests/audible_api/run", methods=["POST"])
@login_required
def audible_api_run():
    """Execute a built Audible API call and return the raw JSON response."""
    payload = request.get_json(silent=True) or {}

    call_type = (payload.get("call_type") or "custom").strip()
    asin = (payload.get("asin") or "").strip()
    custom_path = (payload.get("path") or "").strip()
    response_groups = (payload.get("response_groups") or "").strip()
    extra_params_raw = payload.get("extra_params") or ""

    preset = CALL_TYPES.get(call_type, CALL_TYPES["custom"])

    if preset["needs_asin"] and not asin:
        return jsonify({"success": False, "error": "This call type requires an ASIN."}), 400

    path = custom_path if call_type == "custom" else preset["path"]
    if not path:
        return jsonify({"success": False, "error": "No path to call."}), 400

    if "{asin}" in path:
        if not asin:
            return jsonify({"success": False, "error": "Path requires an ASIN but none was provided."}), 400
        path = path.format(asin=asin)

    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc:
        return jsonify({"success": False, "error": "Path must be a relative Audible API path, not a full URL."}), 400

    helper = _get_helper()
    if not helper:
        return jsonify({"success": False, "error": "Audible authentication is missing. Re-authenticate and try again."}), 401

    params = _parse_extra_params(extra_params_raw)
    if response_groups:
        params["response_groups"] = response_groups

    try:
        result = helper.raw_get(path, **params)
        return jsonify({
            "success": True,
            "path": path,
            "params_sent": params,
            "result": result,
        })
    except Exception as exc:
        logger.warning("Audible API tester call failed: %s", exc, exc_info=True)
        return jsonify({
            "success": False,
            "path": path,
            "params_sent": params,
            "error": str(exc),
        }), 200


@debug_bp.route("/tests/audible_api/search-titles", methods=["GET"])
@login_required
def audible_api_search_titles():
    """Search the local library by title/author/series for the ASIN picker."""
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"success": True, "results": []})

    db_service = container.get_database_service()
    try:
        matches = db_service.books.search_books(query)
    except Exception as exc:
        logger.warning("Title search failed: %s", exc)
        return jsonify({"success": False, "error": str(exc), "results": []}), 200

    results = [
        {
            "asin": book.get("ASIN"),
            "title": book.get("Title"),
            "author": book.get("Author"),
        }
        for book in matches[:20]
        if book.get("ASIN")
    ]
    return jsonify({"success": True, "results": results})
