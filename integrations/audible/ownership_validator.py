"""
Module Name: ownership_validator.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Validate Audible ownership metadata and fetch library entries for download gating.

Location:
    /integrations/audible/ownership_validator.py
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Set, Tuple

from core.logging import get_module_logger

DEFAULT_UNVERIFIED_SYNC_STATUSES = frozenset({"pending"})


class OwnershipValidator:
    """Encapsulate ownership validation and Audible library fetching."""

    def __init__(
        self,
        *,
        unverified_sync_statuses: Optional[Set[str]] = None,
        logger=None,
    ) -> None:
        self.logger = logger or get_module_logger("Service.Audible.OwnershipValidator")
        self.unverified_sync_statuses: Set[str] = set(
            DEFAULT_UNVERIFIED_SYNC_STATUSES if unverified_sync_statuses is None else unverified_sync_statuses
        )

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        return False

    @staticmethod
    def _namespace_library_fields(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """audible_library.metadata_source means API vs scrape, not the books
        column of the same name, so keep it under its own key."""
        if not record or record.get("_source_table") != "audible_library" or "metadata_source" not in record:
            return record
        renamed = dict(record)
        renamed["library_metadata_source"] = renamed.pop("metadata_source")
        return renamed

    def merge_audible_records(self, primary: Optional[Dict[str, Any]], secondary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Merge two metadata records, preferring populated values from ``primary``."""
        if primary is None and secondary is None:
            return None

        primary = self._namespace_library_fields(primary)
        secondary = self._namespace_library_fields(secondary)

        if primary is None:
            return dict(secondary)
        if secondary is None:
            return dict(primary)

        merged = dict(primary)
        secondary_source = secondary.get("_source_table") or secondary.get("source_table")

        for key, value in secondary.items():
            if key.startswith("_"):
                continue
            if key not in merged or self._is_missing(merged.get(key)):
                merged[key] = value

        if secondary_source:
            combined = merged.get("_source_table") or primary.get("_source_table")
            if combined and secondary_source not in combined:
                merged["_source_table"] = f"{combined}+{secondary_source}"
            elif not combined:
                merged["_source_table"] = secondary_source

        return merged

    @staticmethod
    def _normalize_purchase_date(raw_value: Any) -> Optional[str]:
        """Normalize stored purchase date values to a canonical string or None."""
        if raw_value is None:
            return None

        if isinstance(raw_value, str):
            cleaned = raw_value.strip()
            if not cleaned:
                return None
            if cleaned.lower() in {"none", "null", "n/a", "pending"}:
                return None
            if cleaned in {"0000-00-00", "0000-00-00T00:00:00"}:
                return None
            return cleaned

        return str(raw_value)

    @staticmethod
    def _normalize_tag_collection(raw_tags: Any) -> Set[str]:
        """Normalize tag metadata into a lowercase set for comparisons."""
        if not raw_tags:
            return set()

        if isinstance(raw_tags, str):
            raw = raw_tags.strip()
            if not raw:
                return set()

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None

            if isinstance(parsed, (list, tuple, set)):
                raw_tags = parsed
            elif isinstance(parsed, dict):
                raw_tags = list(parsed.keys())
            else:
                return {tag.strip().lower() for tag in raw.split(",") if tag.strip()}

        if isinstance(raw_tags, (list, tuple, set)):
            return {str(tag).strip().lower() for tag in raw_tags if str(tag).strip()}

        return {str(raw_tags).strip().lower()} if str(raw_tags).strip() else set()

    def assess_audible_ownership(self, entry: Optional[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
        """Evaluate whether stored metadata confirms Audible ownership.

        Returns:
            Tuple containing the ownership verdict and context details used to
            explain the decision. The context dict always contains human-friendly
            "reason", along with metadata fields for logging/UI purposes.
        """
        details: Dict[str, Any] = {
            "reason": "No Audible library entry found for this ASIN.",
            "sync_status": None,
            "metadata_source": None,
            "purchase_date": None,
            "tags": [],
            "hints": [],
            "library_status": None,
            "source_table": None,
        }

        if not entry:
            self.logger.debug("Ownership validation failed", extra={"reason": "no library entry", "asin": None})
            return False, details

        sync_status = (entry.get("sync_status") or "").strip().lower()
        metadata_source = (entry.get("metadata_source") or entry.get("source") or "").strip().lower()
        purchase_date = self._normalize_purchase_date(entry.get("purchase_date") or entry.get("PurchaseDate"))
        tags = self._normalize_tag_collection(entry.get("tags"))
        library_status_value = (entry.get("library_status") or "").strip().lower()
        source_table = entry.get("_source_table") or entry.get("source_table")

        details.update(
            {
                "sync_status": sync_status or None,
                "metadata_source": metadata_source or None,
                "purchase_date": purchase_date,
                "tags": sorted(tags),
                "library_status": library_status_value or None,
                "source_table": source_table,
            }
        )

        if sync_status and sync_status in self.unverified_sync_statuses:
            details["reason"] = (
                f"Audible library sync status '{sync_status or 'unknown'}' is not verified yet."
            )
            self.logger.debug(
                "Ownership validation failed",
                extra={"asin": entry.get("asin") or entry.get("ASIN"), "reason": "sync status not verified", "sync_status": sync_status},
            )
            return False, details

        ownership_hints = []

        if purchase_date:
            ownership_hints.append("purchase_date")
        if library_status_value == "owned":
            ownership_hints.append(f"library_status:{library_status_value}")

        hints = ownership_hints

        if not purchase_date:
            details["hints"] = []
            details["reason"] = "Audible entry missing purchase date confirmation."
            self.logger.debug(
                "Ownership validation failed",
                extra={"asin": entry.get("asin") or entry.get("ASIN"), "reason": "missing purchase date"},
            )
            return False, details

        if "audible" in (metadata_source or ""):
            details["hints"] = sorted(hints) if hints else ["books"]
            details["reason"] = "Verified via library book record."
            self.logger.debug(
                "Ownership validation accepted",
                extra={"asin": entry.get("asin") or entry.get("ASIN"), "reason": "library book record", "hints": details["hints"]},
            )
            return True, details

        details["hints"] = sorted(hints) if hints else ["audible_library"]
        details["reason"] = "Verified via Audible library record."
        self.logger.debug(
            "Ownership validation accepted",
            extra={"asin": entry.get("asin") or entry.get("ASIN"), "reason": "audible library record", "hints": details["hints"]},
        )
        return True, details

    def fetch_audible_library_entry(self, database_service: Any, asin: Optional[str]) -> Optional[Dict[str, Any]]:
        """Library record for the ASIN, merged from the books and
        audible_library tables."""
        if not asin or not database_service:
            return None

        book_entry: Optional[Dict[str, Any]] = None
        library_entry: Optional[Dict[str, Any]] = None

        raw_book_entry = database_service.books.get_book_by_asin(asin)
        if raw_book_entry:
            book_entry = dict(raw_book_entry)
            book_entry.setdefault("_source_table", "books")

        raw_library_entry = database_service.audible_library.get_book_by_asin(asin)
        if raw_library_entry:
            library_entry = dict(raw_library_entry)
            library_entry.setdefault("_source_table", "audible_library")

        merged = self.merge_audible_records(book_entry, library_entry)
        if merged and book_entry and library_entry:
            merged.setdefault("_merged_from", "books,audible_library")
        return merged


_validator = OwnershipValidator()

_UNVERIFIED_SYNC_STATUSES: Set[str] = _validator.unverified_sync_statuses


def _is_missing(value: Any) -> bool:
    return _validator._is_missing(value)


def merge_audible_records(primary: Optional[Dict[str, Any]], secondary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return _validator.merge_audible_records(primary, secondary)


def _normalize_purchase_date(raw_value: Any) -> Optional[str]:
    return _validator._normalize_purchase_date(raw_value)


def _normalize_tag_collection(raw_tags: Any) -> Set[str]:
    return _validator._normalize_tag_collection(raw_tags)


def assess_audible_ownership(entry: Optional[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    return _validator.assess_audible_ownership(entry)


def fetch_audible_library_entry(database_service: Any, asin: Optional[str]) -> Optional[Dict[str, Any]]:
    return _validator.fetch_audible_library_entry(database_service, asin)

