"""
Module Name: hardcover_api.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    JSON API for the Hardcover.app integration's discovery pages (Trending,
    New Releases, Search, Lists, Genres & Moods) and the sync queue widget.
    Connection management (connect/disconnect/token) lives in routes/profile.py
    alongside the other Connected Services endpoints.

Location:
    /api/hardcover/hardcover_api.py

Endpoints:

GET
    /api/hardcover/book/<book_id>/actions           Hardcover shelf status, rating and privacy for a local book
    /api/hardcover/browse                           Browse by genre or mood (type=genre|mood, term)
    /api/hardcover/genres                           List available genres
    /api/hardcover/lists/<list_id>                  List details and a page of its audiobooks (limit, offset)
    /api/hardcover/lists/browse                     Browse public lists (sort=featured|popular|new|updated, page)
    /api/hardcover/moods                            List available moods
    /api/hardcover/new-releases                     New audiobook releases (refresh=1 to bypass cache)
    /api/hardcover/search                           Search Hardcover (q, type=book|author|series|list, page)
    /api/hardcover/shelves                          Live pull of the connected account's Hardcover library
    /api/hardcover/status                           Hardcover connection status
    /api/hardcover/sync-queue                       Recent sync queue entries
    /api/hardcover/trending                         Trending audiobooks (window=1m|3m|1y|all, refresh=1)

POST
    /api/hardcover/add-to-library                   Add a Hardcover book to the library
    /api/hardcover/book/<book_id>/actions           Push shelf status, rating or privacy changes for a local book
    /api/hardcover/book/<book_id>/like              Like or unlike the linked local book's user_book entry
    /api/hardcover/book/<book_id>/lists/<list_id>   Add a linked local book to one of the connected account's own lists
    /api/hardcover/book/<book_id>/match             Link a local book to Hardcover (auto-match or chosen book)
    /api/hardcover/book/<book_id>/owned             Toggle the owned flag for a linked local book's edition
    /api/hardcover/lists                            Create a new list owned by the connected account
    /api/hardcover/refresh                          Refresh the Hardcover cache now
    /api/hardcover/sync-queue/<push_id>/retry       Reset a failed sync entry to pending

DELETE
    /api/hardcover/book/<book_id>/link              Remove a local book's Hardcover link
    /api/hardcover/lists/books/<list_book_id>       Remove a book from one of the connected account's own lists
"""

from flask import Blueprint, jsonify, request

from core.database.repositories import hardcover_cache
from core.logging import get_module_logger
from services.settings.languages import get_preferred_languages
from services.container import container
from services.sync.accounts import hardcover_account

from integrations.hardcover.connection import get_client, HardcoverAPIError, HardcoverAuthError
from integrations.hardcover.trending import WINDOWS, fetch_trending
from integrations.hardcover.new_releases import fetch_new_releases
from integrations.hardcover.search import search as hc_search, browse_by_genre, browse_by_mood, browse_lists, BROWSE_GENRES, BROWSE_MOODS
from integrations.hardcover.list_detail import fetch_list_detail, fetch_list_books
from integrations.hardcover.shelves import fetch_my_shelves, find_user_book, push_shelf_update, set_edition_owned
from integrations.hardcover.matcher import find_verified_asin, match_local_book
from integrations.hardcover.likes import like_entity, unlike_entity, has_liked
from integrations.hardcover.my_lists import fetch_my_lists, create_list, add_book_to_list, remove_book_from_list

hardcover_api_bp = Blueprint('hardcover_api', __name__, url_prefix='/api/hardcover')


def _as_int(value):
    """Parse a client-supplied integer id; None if missing or malformed."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
logger = get_module_logger("API.Hardcover")


def _config_service():
    return container.get_config_service()


def _connected_client_or_error():
    """Return (client, None) or (None, (json_response, status_code))."""
    try:
        client = get_client(_config_service())
        return client, None
    except HardcoverAuthError:
        return None, (jsonify({"error": "Hardcover account not connected"}), 401)


def _enrich_library_status(entries):
    """Attach a `library_status` (in_library/wanted/not_in_library) to each
    cache entry based on its ASIN, so the frontend can render the correct
    add-to-library affordance (mirrors routes/discover.py's approach).

    Entries without an ASIN are dropped entirely - there's no way to add them
    to the library, so they'd just be a dead-end card. (Also guards against
    stale cache rows written before ASIN-less books were excluded upstream.)
    """
    if not entries:
        return entries
    db_service = container.get_database_service()
    result = []
    for entry in entries:
        asin = entry.get("asin")
        if not asin:
            continue
        book = db_service.books.get_book_by_asin(asin)
        if book:
            entry["library_status"] = (book.get("library_status") or "wanted").lower()
        else:
            entry["library_status"] = "not_in_library"
        result.append(entry)
    return result


@hardcover_api_bp.route("/status")
def api_status():
    account = hardcover_account.get_linked_account(_config_service())
    return jsonify({
        "connected": account is not None,
        "username": (account or {}).get("username"),
    })


@hardcover_api_bp.route("/trending")
def api_trending():
    window = request.args.get("window", "1m")
    if window not in WINDOWS:
        return jsonify({"error": f"Invalid window '{window}', expected one of {WINDOWS}"}), 400

    force_refresh = request.args.get("refresh") == "1"
    engine = container.get_engine()

    with engine.connect() as conn:
        cached = hardcover_cache.get_trending_cache(conn, window)
        refreshed_at = hardcover_cache.get_trending_refreshed_at(conn, window)

    if force_refresh or not cached:
        client, error = _connected_client_or_error()
        if error:
            if cached:
                return jsonify({"window": window, "entries": _enrich_library_status(cached), "refreshed_at": refreshed_at, "stale": True})
            return error
        try:
            entries = fetch_trending(client, window)
            with engine.begin() as conn:
                hardcover_cache.replace_trending_cache(conn, window, entries)
                cached = hardcover_cache.get_trending_cache(conn, window)
                refreshed_at = hardcover_cache.get_trending_refreshed_at(conn, window)
        except HardcoverAPIError as exc:
            logger.warning("Trending fetch failed: %s", exc)
            if cached:
                return jsonify({"window": window, "entries": _enrich_library_status(cached), "refreshed_at": refreshed_at, "stale": True})
            return jsonify({"error": str(exc)}), 502

    return jsonify({"window": window, "entries": _enrich_library_status(cached), "refreshed_at": refreshed_at, "stale": False})


@hardcover_api_bp.route("/new-releases")
def api_new_releases():
    force_refresh = request.args.get("refresh") == "1"
    engine = container.get_engine()

    with engine.connect() as conn:
        cached = hardcover_cache.get_new_releases_cache(conn)
        refreshed_at = hardcover_cache.get_new_releases_refreshed_at(conn)

    if force_refresh or not cached:
        client, error = _connected_client_or_error()
        if error:
            if cached:
                return jsonify({"entries": _enrich_library_status(cached), "refreshed_at": refreshed_at, "stale": True})
            return error
        try:
            entries = fetch_new_releases(client)
            with engine.begin() as conn:
                hardcover_cache.replace_new_releases_cache(conn, entries)
                cached = hardcover_cache.get_new_releases_cache(conn)
                refreshed_at = hardcover_cache.get_new_releases_refreshed_at(conn)
        except HardcoverAPIError as exc:
            logger.warning("New releases fetch failed: %s", exc)
            if cached:
                return jsonify({"entries": _enrich_library_status(cached), "refreshed_at": refreshed_at, "stale": True})
            return jsonify({"error": str(exc)}), 502

    return jsonify({"entries": _enrich_library_status(cached), "refreshed_at": refreshed_at, "stale": False})


@hardcover_api_bp.route("/refresh", methods=["POST"])
def api_refresh():
    """Manually trigger an immediate cache refresh (bypasses the background interval)."""
    hardcover_service = container.get_hardcover_service()
    if not hardcover_service.is_connected():
        return jsonify({"success": False, "error": "Hardcover account not connected"}), 401
    try:
        hardcover_service.refresh_all_caches()
    except Exception as exc:
        logger.error("Manual Hardcover refresh failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 502
    return jsonify({"success": True})


@hardcover_api_bp.route("/search")
def api_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    query_type = (request.args.get("type") or "book").lower()
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 50)

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        result = hc_search(client, query, query_type, per_page=per_page, page=page)
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify(result)


@hardcover_api_bp.route("/lists/browse")
def api_lists_browse():
    """Browse public lists without a search query (Featured/Popular/New/Recently
    Updated), mirroring Hardcover's own Lists page."""
    sort = (request.args.get("sort") or "featured").lower()
    if sort not in ("featured", "popular", "new", "updated"):
        return jsonify({"error": f"Invalid sort '{sort}', expected one of featured/popular/new/updated"}), 400
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 50)

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        lists = browse_lists(client, sort, per_page=per_page, page=page)
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"lists": lists, "sort": sort, "page": page})


@hardcover_api_bp.route("/lists/<int:list_id>")
def api_list_detail(list_id):
    """List metadata + a page of its books (audio-only, ASIN-bearing), for
    drilling into a list from the Lists tab. List metadata is only fetched
    (and returned) on the first page (offset=0) - subsequent "Load More"
    pages skip the extra round-trip since the frontend already has it."""
    limit = min(int(request.args.get("limit", 50)), 100)
    offset = int(request.args.get("offset", 0))

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        list_meta = None
        if offset == 0:
            list_meta = fetch_list_detail(client, list_id)
            if not list_meta:
                return jsonify({"error": "List not found"}), 404
        page = fetch_list_books(client, list_id, limit=limit, offset=offset)
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({
        "list": list_meta,
        "entries": _enrich_library_status(page["entries"]),
        "next_offset": page["next_offset"],
        "has_more": page["has_more"],
    })


@hardcover_api_bp.route("/genres")
def api_genres():
    return jsonify({"genres": BROWSE_GENRES})


@hardcover_api_bp.route("/moods")
def api_moods():
    return jsonify({"moods": BROWSE_MOODS})


@hardcover_api_bp.route("/browse")
def api_browse():
    browse_type = (request.args.get("type") or "genre").lower()
    term = (request.args.get("term") or "").strip()
    if not term:
        return jsonify({"error": "Query parameter 'term' is required"}), 400

    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 50)

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        if browse_type == "mood":
            result = browse_by_mood(client, term, per_page=per_page, page=page)
        else:
            result = browse_by_genre(client, term, per_page=per_page, page=page)
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify(result)


@hardcover_api_bp.route("/shelves")
def api_shelves():
    """Live pull of the linked account's Hardcover library (not cached - Phase 3)."""
    config_service = _config_service()
    account = hardcover_account.get_linked_account(config_service)
    if not account or not account.get("user_id"):
        return jsonify({"error": "Hardcover account not connected"}), 401

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        shelves = fetch_my_shelves(client, int(account["user_id"]))
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"shelves": shelves, "count": len(shelves)})


@hardcover_api_bp.route("/add-to-library", methods=["POST"])
def api_add_to_library():
    """Add a Hardcover book to the library: verify the audio-edition ASIN, insert
    it as Wanted, enrich it from Audible, and save the Hardcover link."""
    data = request.get_json(silent=True) or {}
    hardcover_book_id = _as_int(data.get("hardcover_book_id"))
    if not hardcover_book_id:
        return jsonify({"error": "hardcover_book_id must be an integer"}), 400

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        resolved = find_verified_asin(
            client,
            hardcover_book_id,
            preferred_languages=get_preferred_languages(),
            audnexus=container.get_audnexus_client(),
            preferred_asin=data.get("preferred_asin"),
        )
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    asin = resolved.get("asin")
    if not asin or resolved.get("confidence") == "none":
        return jsonify({"error": "No verified audio edition found for this book"}), 404

    db_service = container.get_database_service()
    if db_service.books.check_book_exists(asin):
        return jsonify({"error": "Book already exists in library"}), 409

    book_data = {
        "Title": data.get("title", ""),
        "Author": data.get("author", ""),
        "Narrator": data.get("narrator", ""),
        "Release Date": data.get("release_date", ""),
        "Cover Image": data.get("cover_url", ""),
        "ASIN": asin,
    }
    if not book_data.get("Title") or not book_data.get("Author"):
        return jsonify({"error": "Title and Author are required"}), 400

    if not db_service.books.add_book(book_data, library_status="wanted"):
        return jsonify({"error": "Failed to add book to database"}), 500

    logger.info("Added book from Hardcover: %s by %s (%s)", book_data["Title"], book_data["Author"], asin)

    added_record = db_service.books.get_book_by_asin(asin)
    if added_record and added_record.get("ID"):
        try:
            with container.get_engine().begin() as conn:
                hardcover_cache.upsert_link(
                    conn, added_record["ID"], hardcover_book_id,
                    hardcover_edition_id=resolved.get("edition_id"), asin=asin,
                    match_confidence=resolved.get("confidence") or "unverified",
                )
        except Exception as exc:
            logger.warning("Could not save Hardcover link for %s: %s", asin, exc)
        try:
            metadata_service = container.get_metadata_update_service()
            ok, message = metadata_service.update_single_book(added_record["ID"])
            if not ok:
                logger.warning("Metadata enrichment skipped for %s: %s", asin, message)
        except Exception as exc:
            logger.warning("Metadata enrichment failed for %s: %s", asin, exc)

    return jsonify({
        "success": True,
        "message": "Book added to library successfully",
        "book": {
            "title": book_data["Title"],
            "author": book_data["Author"],
            "asin": asin,
        },
    })


@hardcover_api_bp.route("/sync-queue")
def api_sync_queue():
    """Recent push-queue entries, for the profile pane's sync-status list."""
    engine = container.get_engine()
    with engine.connect() as conn:
        entries = hardcover_cache.get_sync_queue(conn)
    return jsonify({"entries": entries})


@hardcover_api_bp.route("/sync-queue/<int:push_id>/retry", methods=["POST"])
def api_sync_queue_retry(push_id):
    """Manual override: force a failed push back to pending regardless of retry_count."""
    engine = container.get_engine()
    with engine.begin() as conn:
        hardcover_cache.reset_push_for_retry(conn, push_id)
    return jsonify({"success": True})


def _linked_hardcover_user_id():
    """Return (user_id, None) or (None, (json_response, status_code))."""
    account = hardcover_account.get_linked_account(_config_service())
    if not account or not account.get("user_id"):
        return None, (jsonify({"error": "Hardcover account not connected"}), 401)
    return int(account["user_id"]), None


def _book_link_or_error(book_id: int):
    """Return (link_row, None) or (None, (json_response, status_code)) for a
    local book_id's confirmed Hardcover match."""
    engine = container.get_engine()
    with engine.connect() as conn:
        link = hardcover_cache.get_link_for_book(conn, book_id)
    if not link:
        return None, (jsonify({"error": "This book has no confirmed Hardcover match"}), 404)
    return link, None


@hardcover_api_bp.route("/book/<int:book_id>/match", methods=["POST"])
def api_match_book(book_id: int):
    """Link a local book to Hardcover. With hardcover_book_id, link that book;
    otherwise search and link a confident match, or return candidates."""
    data = request.get_json(silent=True) or {}
    book = container.get_database_service().books.get_book_by_id(book_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404
    client, error = _connected_client_or_error()
    if error:
        return error
    title, author, asin = book.get("Title"), book.get("Author"), book.get("ASIN")
    chosen = _as_int(data.get("hardcover_book_id"))
    try:
        if chosen:
            match = {"hardcover_book_id": chosen, "edition_id": None, "asin": None, "confidence": "manual"}
            candidates = []
        else:
            result = match_local_book(client, title, author, asin)
            match, candidates = result["match"], result["candidates"]
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    with container.get_engine().begin() as conn:
        if not match:
            hardcover_cache.record_match_attempt(conn, book_id)
            return jsonify({"success": True, "linked": False, "candidates": candidates})
        hardcover_cache.upsert_link(
            conn, book_id, match["hardcover_book_id"],
            hardcover_edition_id=match.get("edition_id"), asin=match.get("asin"),
            match_confidence=match["confidence"],
        )
    logger.info("Linked book %s to Hardcover book %s (%s)", book_id, match["hardcover_book_id"], match["confidence"])
    return jsonify({"success": True, "linked": True, "match": match})


@hardcover_api_bp.route("/book/<int:book_id>/link", methods=["DELETE"])
def api_unlink_book(book_id: int):
    """Remove a local book's Hardcover link."""
    with container.get_engine().begin() as conn:
        hardcover_cache.delete_link(conn, book_id)
    return jsonify({"success": True})


@hardcover_api_bp.route("/book/<int:book_id>/actions", methods=["GET"])
def api_book_actions_get(book_id):
    """Return the current Hardcover-side state for a linked local book:
    shelf status, rating, privacy, owned flag, and the user's own lists
    (for populating an Add to List picker)."""
    link, error = _book_link_or_error(book_id)
    if error:
        return error
    user_id, error = _linked_hardcover_user_id()
    if error:
        return error

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        user_book = find_user_book(client, user_id, link["hardcover_book_id"]) or {}
        my_lists = fetch_my_lists(client, user_id)
        liked = has_liked(client, user_book["id"], "UserBook", user_id) if user_book.get("id") else False
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({
        "hardcover_book_id": link["hardcover_book_id"],
        "hardcover_edition_id": link.get("hardcover_edition_id"),
        "status_id": user_book.get("status_id"),
        "rating": user_book.get("rating"),
        "privacy_setting_id": user_book.get("privacy_setting_id"),
        "owned": bool(user_book.get("owned")),
        "liked": liked,
        "hardcover_user_book_id": user_book.get("id"),
        "lists": my_lists,
    })


@hardcover_api_bp.route("/book/<int:book_id>/actions", methods=["POST"])
def api_book_actions_post(book_id):
    """Push shelf status / rating / privacy changes for a linked local book.
    Only fields present in the request body are sent - see
    shelves.push_shelf_update()'s docstring."""
    link, error = _book_link_or_error(book_id)
    if error:
        return error
    user_id, error = _linked_hardcover_user_id()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    kwargs = {}
    if "status_id" in data:
        kwargs["status_id"] = data.get("status_id")
    if "rating" in data:
        kwargs["rating"] = data.get("rating")
    if "privacy_setting_id" in data:
        kwargs["privacy_setting_id"] = data.get("privacy_setting_id")
    if not kwargs:
        return jsonify({"error": "At least one of status_id/rating/privacy_setting_id is required"}), 400

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        result = push_shelf_update(client, user_id, link["hardcover_book_id"], **kwargs)
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"success": True, "user_book": result.get("user_book") or {}})


@hardcover_api_bp.route("/book/<int:book_id>/owned", methods=["POST"])
def api_book_owned_toggle(book_id):
    """Toggle the owned flag for a linked local book's edition. edition_owned
    is a toggle mutation (no direct set), so the current state is checked
    first and the call is skipped if it already matches the desired state."""
    link, error = _book_link_or_error(book_id)
    if error:
        return error
    if not link.get("hardcover_edition_id"):
        return jsonify({"error": "No Hardcover edition recorded for this book"}), 404
    user_id, error = _linked_hardcover_user_id()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    desired_owned = bool(data.get("owned"))

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        existing = find_user_book(client, user_id, link["hardcover_book_id"]) or {}
        current_owned = bool(existing.get("owned"))
        if current_owned != desired_owned:
            set_edition_owned(client, link["hardcover_edition_id"])
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"success": True, "owned": desired_owned})


@hardcover_api_bp.route("/book/<int:book_id>/like", methods=["POST"])
def api_book_like(book_id):
    """Like or unlike the linked local book's user_book entry. Requires the
    book to already be shelved (have a user_book row) since likes attach to
    that entry, not the book itself."""
    link, error = _book_link_or_error(book_id)
    if error:
        return error
    user_id, error = _linked_hardcover_user_id()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    liked = bool(data.get("liked", True))

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        user_book = find_user_book(client, user_id, link["hardcover_book_id"])
        if not user_book:
            return jsonify({"error": "Set a shelf status for this book before liking it"}), 409
        if liked:
            like_entity(client, user_book["id"], "UserBook")
        else:
            unlike_entity(client, user_book["id"], "UserBook")
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"success": True, "liked": liked})


@hardcover_api_bp.route("/lists", methods=["POST"])
def api_lists_create():
    """Create a new list owned by the connected account."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    privacy_setting_id = _as_int(data.get("privacy_setting_id", 1))
    if privacy_setting_id is None:
        return jsonify({"error": "privacy_setting_id must be an integer"}), 400

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        result = create_list(
            client,
            name,
            description=data.get("description"),
            privacy_setting_id=privacy_setting_id,
        )
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"success": True, "list": result.get("list") or {}})


@hardcover_api_bp.route("/book/<int:book_id>/lists/<int:list_id>", methods=["POST"])
def api_book_add_to_list(book_id, list_id):
    """Add a linked local book to one of the connected account's own lists."""
    link, error = _book_link_or_error(book_id)
    if error:
        return error

    client, error = _connected_client_or_error()
    if error:
        return error

    try:
        result = add_book_to_list(client, list_id, link["hardcover_book_id"])
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"success": True, "list_book": result.get("list_book") or {}})


@hardcover_api_bp.route("/lists/books/<int:list_book_id>", methods=["DELETE"])
def api_list_remove_book(list_book_id):
    """Remove a book from one of the connected account's own lists."""
    client, error = _connected_client_or_error()
    if error:
        return error
    try:
        removed = remove_book_from_list(client, list_book_id)
    except HardcoverAPIError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"success": removed})
