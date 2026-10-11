"""
Module Name: profile.py
Author: AuralArchivist
Created: June 8, 2026
Last Modified: October 9, 2026
Description:
    User profile page and related JSON API endpoints.

Location:
    /routes/profile.py
"""

import os

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from core.auth.auth import change_password, get_user_profile_data, update_user_profile_data
from core.logging import get_module_logger
from core.paths import resolve_audible_auth_file
from services.container import container
from services.sync.accounts import mam_account, hardcover_account
from core.scheduler import get_scheduler
from services.sync.hardcover import TASK_NAME as HARDCOVER_TASK

logger = get_module_logger("Routes.Profile")

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/profile")
@login_required
def profile_page():
    return render_template("profile.html")


@profile_bp.route("/api/profile/me")
@login_required
def api_profile_me():
    """Return current user's profile data."""
    profile = get_user_profile_data(current_user.username)
    config_service = container.get_config_service()
    mam_cookie = mam_account.get_linked_cookie(config_service)
    mam_linked = bool(mam_cookie)
    mam_username = None
    if mam_linked:
        indexer = config_service.get_indexer_config("myanonamouse")
        mam_username = (indexer or {}).get("_mam_username")

    hardcover_link = hardcover_account.get_linked_account(config_service)
    audible_linked = os.path.exists(resolve_audible_auth_file())

    return jsonify({
        "username": profile["username"],
        "display_name": profile["display_name"],
        "created_at": profile["created_at"],
        "mam_linked": mam_linked,
        "mam_username": mam_username,
        "hardcover_linked": hardcover_link is not None,
        "hardcover_username": (hardcover_link or {}).get("username"),
        "audible_linked": audible_linked,
    })


@profile_bp.route("/api/profile/update", methods=["POST"])
@login_required
def api_profile_update():
    """Update display name."""
    data = request.get_json(silent=True) or {}
    display_name = (data.get("display_name") or "").strip()
    ok, msg = update_user_profile_data(current_user.username, display_name)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "error": msg}), 400


@profile_bp.route("/api/profile/change-password", methods=["POST"])
@login_required
def api_profile_change_password():
    """Change the current user's password."""
    data = request.get_json(silent=True) or {}
    current_pw = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    confirm_pw = data.get("confirm_password", "")

    if not current_pw or not new_pw or not confirm_pw:
        return jsonify({"success": False, "error": "All fields are required"}), 400
    if len(new_pw) < 6:
        return jsonify({"success": False, "error": "New password must be at least 6 characters"}), 400
    if len(new_pw) > 128:
        return jsonify({"success": False, "error": "New password must be 128 characters or fewer"}), 400
    if new_pw != confirm_pw:
        return jsonify({"success": False, "error": "New passwords do not match"}), 400

    ok, msg = change_password(current_user.username, current_pw, new_pw)
    if ok:
        return jsonify({"success": True, "message": "Password changed successfully"})
    return jsonify({"success": False, "error": msg}), 400


@profile_bp.route("/api/profile/mam/connect", methods=["POST"])
@login_required
def api_mam_connect():
    """Validate a MAM session cookie and provision the indexer."""
    data = request.get_json(silent=True) or {}
    cookie = (data.get("cookie") or "").strip()
    if not cookie:
        return jsonify({"success": False, "error": "Cookie value is required"}), 400

    ok, result = mam_account.validate_cookie(cookie)
    if not ok:
        return jsonify({"success": False, "error": result.get("error", "Validation failed")}), 400

    config_service = container.get_config_service()
    provisioned = mam_account.provision_indexer(cookie, result, config_service)
    mam_account.invalidate_cache(cookie)

    if not provisioned:
        logger.error("MAM cookie validated but indexer provisioning failed for user '%s'", current_user.username)
        return jsonify({"success": False, "error": "Could not save indexer configuration"}), 500

    return jsonify({
        "success": True,
        "message": f"Connected as {result['username']}",
        "account": result,
    })


@profile_bp.route("/api/profile/mam/stats")
@login_required
def api_mam_stats():
    """Return cached MAM account stats, re-fetching if stale."""
    config_service = container.get_config_service()
    cookie = mam_account.get_linked_cookie(config_service)
    if not cookie:
        return jsonify({"linked": False}), 200

    stats = mam_account.get_cached_stats(cookie)
    if stats is None:
        return jsonify({"linked": True, "expired": True,
                        "error": "Session cookie may have expired - please re-enter it"}), 200

    return jsonify({"linked": True, "expired": False, "account": stats})


@profile_bp.route("/api/profile/mam/buy-wedges", methods=["POST"])
@login_required
def api_mam_buy_wedges():
    """Spend bonus points on a freeleech wedge. Explicit user action only - never automatic."""
    config_service = container.get_config_service()
    cookie = mam_account.get_linked_cookie(config_service)
    if not cookie:
        return jsonify({"success": False, "error": "MAM account not linked"}), 400

    success, result = mam_account.buy_wedges(cookie)
    if not success:
        return jsonify({"success": False, "error": result.get("error", "Purchase failed")}), 200

    stats = mam_account.get_cached_stats(cookie)
    return jsonify({"success": True, "account": stats})


@profile_bp.route("/api/profile/mam/disconnect", methods=["POST"])
@login_required
def api_mam_disconnect():
    """Remove the MAM session cookie and de-provision the indexer."""
    config_service = container.get_config_service()
    cookie = mam_account.get_linked_cookie(config_service)
    if cookie:
        mam_account.invalidate_cache(cookie)
    mam_account.deprovision_indexer(config_service)
    return jsonify({"success": True, "message": "MAM account disconnected"})


@profile_bp.route("/api/profile/mam/active-downloads")
@login_required
def api_mam_active_downloads():
    """Return in-progress downloads sourced from MyAnonamouse."""
    config_service = container.get_config_service()
    if not mam_account.get_linked_cookie(config_service):
        return jsonify({"linked": False, "downloads": [], "count": 0})

    try:
        dms = container.get_download_management_service()
        queue = dms.get_queue()
    except Exception as exc:
        logger.warning("Failed to fetch download queue for MAM widget: %s", exc)
        return jsonify({"linked": True, "downloads": [], "count": 0, "error": "Queue unavailable"})

    downloads = [
        {
            "id": item.get("id"),
            "title": item.get("book_title") or "Unknown",
            "progress": round(float(item.get("download_progress") or 0.0), 1),
            "status": (item.get("status") or "UNKNOWN").upper(),
        }
        for item in queue
        if _is_mam_download(item)
    ]

    return jsonify({"linked": True, "downloads": downloads, "count": len(downloads)})


def _is_mam_download(item: dict) -> bool:
    """True when a queue item originated from the MAM direct indexer."""
    indexer = (item.get("indexer") or "").lower()
    return "myanonamouse" in indexer


@profile_bp.route("/api/profile/hardcover/connect", methods=["POST"])
@login_required
def api_hardcover_connect():
    """Validate a Hardcover API token and store it."""
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"success": False, "error": "API token is required"}), 400

    config_service = container.get_config_service()
    ok, result = hardcover_account.connect_account(token, config_service)
    if not ok:
        return jsonify({"success": False, "error": result.get("error", "Validation failed")}), 400

    get_scheduler().run_now(HARDCOVER_TASK)

    return jsonify({
        "success": True,
        "message": f"Connected as {result['username']}",
        "account": result,
    })


@profile_bp.route("/api/profile/hardcover/stats")
@login_required
def api_hardcover_stats():
    """Return cached Hardcover account stats, re-fetching if stale."""
    config_service = container.get_config_service()
    account = hardcover_account.get_linked_account(config_service)
    if not account:
        return jsonify({"linked": False}), 200

    stats = hardcover_account.get_cached_stats(config_service)
    if stats is None:
        return jsonify({"linked": True, "expired": True,
                        "error": "Token may have expired - please reconnect"}), 200

    return jsonify({"linked": True, "expired": False, "username": account.get("username"), "account": stats})


@profile_bp.route("/api/profile/hardcover/disconnect", methods=["POST"])
@login_required
def api_hardcover_disconnect():
    """Remove the stored Hardcover token."""
    config_service = container.get_config_service()
    account = hardcover_account.get_linked_account(config_service)
    if account and account.get("user_id"):
        hardcover_account.invalidate_cache(account["user_id"])
    hardcover_account.disconnect_account(config_service)
    return jsonify({"success": True, "message": "Hardcover account disconnected"})


@profile_bp.route("/api/profile/hardcover/settings", methods=["GET", "POST"])
@login_required
def api_hardcover_settings():
    """Get/update Hardcover sync-behavior settings (auto-push, conflict resolution, etc.)."""
    config_service = container.get_config_service()

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        updates = {}
        if "auto_push_status" in data:
            updates["auto_push_status"] = bool(data["auto_push_status"])
        if "auto_push_owned" in data:
            updates["auto_push_owned"] = bool(data["auto_push_owned"])
        if "auto_create_on_hardcover" in data:
            updates["auto_create_on_hardcover"] = bool(data["auto_create_on_hardcover"])
        if "cache_refresh_interval_minutes" in data:
            try:
                updates["cache_refresh_interval_minutes"] = int(data["cache_refresh_interval_minutes"])
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "cache_refresh_interval_minutes must be an integer"}), 400
        if updates:
            config_service.update_hardcover_config(updates)
        return jsonify({"success": True})

    return jsonify({
        "auto_push_status": config_service.get_hardcover_auto_push_status(),
        "auto_push_owned": config_service.get_hardcover_auto_push_owned(),
        "conflict_resolution": config_service.get_hardcover_conflict_resolution(),
        "auto_create_on_hardcover": config_service.get_hardcover_auto_create(),
        "cache_refresh_interval_minutes": config_service.get_hardcover_cache_refresh_minutes(),
    })
