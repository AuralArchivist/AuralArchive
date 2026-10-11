"""
Module Name: matcher.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Matches between Hardcover books and local books.

Location:
    /integrations/hardcover/matcher.py
"""

import json
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from core.language_matching import language_allowed
from core.logging import get_module_logger
from core.text import strip_subtitle
from .connection import HardcoverClient, AUDIO_READING_FORMAT_IDS

_LOGGER = get_module_logger("Integration.Hardcover.Matcher")

TITLE_MATCH_THRESHOLD = 0.6
AUTO_LINK_TITLE_SCORE = 0.95
AUTO_LINK_LEAD = 0.05

_EDITIONS_QUERY = """
query BookEditions($bookId: Int!) {
  editions(where: { book_id: { _eq: $bookId } }) {
    id
    asin
    edition_format
    reading_format_id
    physical_format
    publisher { name }
    language { language }
    book { title contributions { author { name } } }
  }
}
"""

_TITLE_JUNK_RE = re.compile(r"[^a-z0-9 ]+")


def _normalize_title(title: Optional[str]) -> str:
    """Lowercase, drop the subtitle (same rule as search_normalization's
    strip_subtitle), and strip punctuation so "Dune: Book One" and "DUNE"
    compare equal."""
    if not title:
        return ""
    title = strip_subtitle(title).lower()
    title = _TITLE_JUNK_RE.sub(" ", title)
    return re.sub(r"\s+", " ", title).strip()


def _title_similarity(title_a: Optional[str], title_b: Optional[str]) -> float:
    """0.0-1.0 similarity ratio between two normalized titles. Returns 0.0 if
    either side is empty - missing data should never look like a match."""
    a, b = _normalize_title(title_a), _normalize_title(title_b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _normalize_author_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _authors_overlap(hardcover_authors: List[str], audnexus_authors: List[str]) -> bool:
    """True if any author name is shared between the two sides. Substring
    matching handles formatting differences ("J.R.R. Tolkien" vs "J. R. R.
    Tolkien"). If either side has no author data at all, don't penalize the
    match - missing data isn't proof of a mismatch, and title similarity is
    still the primary signal."""
    a = {_normalize_author_name(n) for n in hardcover_authors if n}
    b = {_normalize_author_name(n) for n in audnexus_authors if n}
    if not a or not b:
        return True
    return any(x == y or x in y or y in x for x in a for y in b)


def _book_title_authors(edition: Dict[str, Any]) -> Tuple[str, List[str]]:
    book = edition.get("book") or {}
    title = book.get("title") or ""
    authors = [c["author"]["name"] for c in (book.get("contributions") or []) if c.get("author")]
    return title, authors


def get_audio_editions(client: HardcoverClient, hardcover_book_id: int) -> List[Dict[str, Any]]:
    """Return raw audio-format edition rows (with an ASIN) for a book.

    Language filtering is intentionally NOT done server-side here: Hardcover's
    `language.language` field is freeform text ("English", "en", etc.) and an
    exact GraphQL `_eq` match against it silently drops editions whose tag
    doesn't match verbatim. Callers should filter the returned list with
    `language_allowed()` (see `find_verified_asin`) instead.
    """
    data = client.query(_EDITIONS_QUERY, {"bookId": hardcover_book_id})
    editions = data.get("editions") or []
    return [
        e for e in editions
        if (e.get("reading_format_id") in AUDIO_READING_FORMAT_IDS or e.get("edition_format") == "audiobook")
        and e.get("asin")
    ]


def find_verified_asin(
    client: HardcoverClient,
    hardcover_book_id: int,
    *,
    preferred_languages: Optional[Set[str]],
    audnexus,
    preferred_asin: Optional[str] = None,
) -> Dict[str, Any]:
    """Find the best audio-edition match for a Hardcover book, verified via Audnexus
    AND cross-checked against the Hardcover book's own title/author.

    Returns a dict: {"asin": str|None, "edition_id": int|None, "confidence": "verified"|"unverified"|"none"}.

    Candidates are filtered down to the configured preferred language(s) first
    (falling back to the full audio-edition list if none match, so a missing/odd
    language tag never zeroes out an otherwise-valid match). `preferred_asin`
    (e.g. a locally-known Audible ASIN) is tried first among the remaining
    candidates when present, but it still has to pass the same title/author
    check as everything else - a locally-known ASIN isn't automatically correct.

    Every candidate whose ASIN resolves on Audnexus is scored by title
    similarity (+ an author-overlap check) against the Hardcover book, and the
    highest-scoring candidate at or above TITLE_MATCH_THRESHOLD wins - just
    resolving on Audnexus is NOT enough to be "verified", since Hardcover's
    catalog can have stale/duplicate/region-mismatched edition rows whose ASIN
    happens to belong to an entirely different audiobook.
    """
    all_candidates = get_audio_editions(client, hardcover_book_id)
    if not all_candidates:
        return {"asin": None, "edition_id": None, "confidence": "none"}

    language_matches = [
        e for e in all_candidates
        if language_allowed((e.get("language") or {}).get("language"), preferred_languages)
    ]
    candidates = language_matches or all_candidates

    if preferred_asin:
        candidates.sort(key=lambda e: 0 if e.get("asin") == preferred_asin else 1)

    hardcover_title, hardcover_authors = _book_title_authors(candidates[0])

    first_candidate = candidates[0]

    best_asin: Optional[str] = None
    best_edition_id: Optional[int] = None
    best_score: float = -1.0

    for edition in candidates:
        asin = edition["asin"]
        try:
            details = audnexus.get_book_details(asin)
        except Exception as exc:
            _LOGGER.debug("Audnexus lookup failed for asin=%s: %s", asin, exc)
            continue

        if not details:
            continue

        title_score = _title_similarity(hardcover_title, details.get("title"))
        audnexus_authors = [a.get("name") for a in (details.get("authors") or []) if a.get("name")]
        authors_ok = _authors_overlap(hardcover_authors, audnexus_authors)

        if title_score < TITLE_MATCH_THRESHOLD or not authors_ok:
            _LOGGER.debug(
                "Rejected ASIN candidate as a title/author mismatch",
                extra={
                    "hardcover_book_id": hardcover_book_id,
                    "asin": asin,
                    "hardcover_title": hardcover_title,
                    "audnexus_title": details.get("title"),
                    "title_score": round(title_score, 3),
                    "authors_ok": authors_ok,
                },
            )
            continue

        if title_score > best_score:
            best_asin, best_edition_id, best_score = asin, edition["id"], title_score

    if best_asin is not None:
        return {"asin": best_asin, "edition_id": best_edition_id, "confidence": "verified"}

    return {"asin": first_candidate["asin"], "edition_id": first_candidate["id"], "confidence": "unverified"}


def _search_documents(search_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Documents from a Hardcover (Typesense) search result."""
    raw = (search_result or {}).get("results")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if isinstance(raw, dict) and isinstance(raw.get("hits"), list):
        return [h.get("document", h) for h in raw["hits"] if isinstance(h, dict)]
    return raw if isinstance(raw, list) else []


def _split_authors(author: Optional[str]) -> List[str]:
    return [a.strip() for a in re.split(r",|&|\band\b", author or "") if a.strip()]


def match_local_book(
    client: HardcoverClient,
    title: str,
    author: Optional[str],
    asin: Optional[str] = None,
    *,
    limit: int = 5,
) -> Dict[str, Any]:
    """Find the Hardcover book for a local book.

    Returns {"match": dict|None, "candidates": [...]}. A match is made when an
    audio edition has the same ASIN ("verified"), or on a near-exact title with
    a shared author and a clear lead ("title"). Otherwise the caller shows the
    candidates for the user to pick.
    """
    from .search import search as hc_search

    local_authors = _split_authors(author)
    query = " ".join(p for p in (strip_subtitle(title or ""), local_authors[0] if local_authors else "") if p)
    if not query:
        return {"match": None, "candidates": []}

    candidates: List[Dict[str, Any]] = []
    for doc in _search_documents(hc_search(client, query, "book", per_page=10)):
        try:
            hardcover_book_id = int(doc.get("id"))
        except (TypeError, ValueError):
            continue
        authors = doc.get("author_names") or []
        if isinstance(authors, str):
            authors = [authors]
        score = _title_similarity(title, doc.get("title"))
        if not _authors_overlap(authors, local_authors):
            score *= 0.5
        image = doc.get("image")
        candidates.append({
            "hardcover_book_id": hardcover_book_id,
            "title": doc.get("title") or "",
            "authors": authors,
            "has_audiobook": bool(doc.get("has_audiobook")),
            "image": image.get("url") if isinstance(image, dict) else image,
            "score": round(score, 3),
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    candidates = candidates[:limit]

    match = None
    if asin:
        for cand in candidates[:3]:
            if cand["score"] < TITLE_MATCH_THRESHOLD:
                break
            for edition in get_audio_editions(client, cand["hardcover_book_id"]):
                if (edition.get("asin") or "").upper() == asin.upper():
                    match = {**cand, "edition_id": edition.get("id"), "asin": asin.upper(), "confidence": "verified"}
                    break
            if match:
                break
    if not match and candidates and candidates[0]["score"] >= AUTO_LINK_TITLE_SCORE:
        runner_up = candidates[1]["score"] if len(candidates) > 1 else 0.0
        if candidates[0]["score"] - runner_up >= AUTO_LINK_LEAD:
            match = {**candidates[0], "edition_id": None, "asin": None, "confidence": "title"}
    return {"match": match, "candidates": candidates}
