"""
Module Name: newznab_base_indexer.py
Author: AuralArchivist
Created: February 18, 2026
Last Modified: October 9, 2026
Description:
    Protocol base for all Newznab indexers (Prowlarr NZB endpoint, NZBHydra2).
    Owns all Newznab XML parsing, capabilities parsing, and NZB result
    normalisation. Subclasses are responsible only for computing the API
    endpoint URL.

Location:
    /integrations/indexers/newznab_base_indexer.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse
import xml.etree.ElementTree as ET

import requests

from core.convert import to_int

from .feed_indexer import FeedIndexer


NEWZNAB_NS = {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"}


@dataclass
class NewznabItem:
    """Normalised representation of a single Newznab feed item."""

    title: str
    download_url: str
    info_url: str
    publish_date: str
    size_bytes: int
    category: str
    guid: str
    attributes: Dict[str, str] = field(default_factory=dict)


class NewznabBaseIndexer(FeedIndexer):
    """
    Protocol base for Newznab indexers.

    Newznab search parameters, NZB links and result shaping on top of
    FeedIndexer. Subclasses compute the API endpoint and pass it in.

    Example subclass::

        class NzbHydra2Indexer(NewznabBaseIndexer):
            def __init__(self, config, *, logger=None):
                config = dict(config)
                api_endpoint = f"{config['base_url']}/api"
                super().__init__(config, api_endpoint, logger=logger)
    """

    PROTOCOL = "newznab"
    PROTOCOL_LABEL = "Newznab"
    NS = NEWZNAB_NS

    # --- BaseIndexer interface ---

    def search(
        self,
        query: str,
        author: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        if not self.is_available():
            self.logger.warning(
                "Newznab indexer unavailable, skipping search",
                extra={"indexer_name": self.name},
            )
            return []

        query = query or ""
        cleaned_query = self._clean_query(query)
        caps = self.capabilities or self.get_capabilities()
        supports_book = caps.get("book_search_available", False)

        params = self._base_params("search")
        if author or title:
            if supports_book:
                params = self._base_params("book")
                if author:
                    params["author"] = author.strip()
                if title:
                    params["title"] = title.strip()
                params["q"] = cleaned_query or query
            else:
                combined = " ".join(p for p in [title, author, query] if p)
                params["q"] = self._clean_query(combined) or combined
        else:
            params["q"] = cleaned_query or query

        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset

        try:
            response = self._request(params)
            root = ET.fromstring(response.content)
            parsed_items = [i for i in self._parse_items(root) if i.download_url]
            results = [self._build_result(i) for i in parsed_items]
            self.mark_success()
            self.logger.debug(
                "Newznab search complete",
                extra={"indexer_name": self.name, "result_count": len(results)},
            )
            return results
        except requests.exceptions.Timeout:
            self.mark_failure(f"Search timeout after {self.timeout}s")
        except requests.exceptions.ConnectionError as exc:
            self.mark_failure(f"Connection error during search: {exc}")
        except ET.ParseError as exc:
            self.mark_failure(f"Invalid XML response: {exc}")
        except Exception as exc:
            self.mark_failure(f"Unexpected search error: {exc}")
            self.logger.exception(
                "Error searching Newznab indexer",
                extra={"indexer_name": self.name},
            )
        return []


    # --- XML parsing ---

    def _parse_single_item(self, element: ET.Element) -> Optional[NewznabItem]:
        title = self._get_text(element, "title", "")
        info_url = self._get_text(element, "link", "")
        guid = self._get_text(element, "guid", "")
        publish_date = self._normalize_date(self._get_text(element, "pubDate", ""))
        attrs = self._extract_attributes(element)

        download_url = self._select_nzb_url(element)
        if not download_url:
            self.logger.debug(
                "Skipping item - no NZB enclosure URL found",
                extra={"title": title},
            )
            return None

        size_bytes = 0
        enclosure = element.find("enclosure")
        if enclosure is not None:
            size_bytes = to_int(enclosure.get("length", 0))
        if not size_bytes:
            size_bytes = to_int(attrs.get("size", 0))

        category = attrs.get("category", self.CATEGORY_AUDIOBOOK)

        return NewznabItem(
            title=title,
            download_url=download_url,
            info_url=info_url,
            publish_date=publish_date,
            size_bytes=size_bytes,
            category=category,
            guid=guid,
            attributes=attrs,
        )

    def _parse_capabilities(self, root: ET.Element) -> Dict[str, Any]:
        caps: Dict[str, Any] = {
            "search_available": False,
            "book_search_available": False,
            "author_search_available": False,
            "categories": [],
            "limits": {},
        }

        searching = root.find(".//searching")
        if searching is not None:
            for child in list(searching):
                available = child.get("available", "no").lower() == "yes"
                tag = child.tag.lower()
                if tag == "search":
                    caps["search_available"] = available
                elif tag == "book-search":
                    caps["book_search_available"] = available
                elif tag == "audio-search":
                    caps["book_search_available"] = available

        for category in root.findall(".//category"):
            cat_id = category.get("id") or ""
            cat_name = category.get("name") or "Unknown"
            caps["categories"].append({"id": cat_id, "name": cat_name})
            for subcat in category.findall(".//subcat"):
                caps["categories"].append(
                    {
                        "id": subcat.get("id") or "",
                        "name": subcat.get("name") or "Unknown",
                    }
                )

        limits = root.find(".//limits")
        if limits is not None:
            caps["limits"] = {
                "max": to_int(limits.get("max", 100), default=100),
                "default": to_int(limits.get("default", 100), default=100),
            }

        return caps

    # --- NZB URL selection ---

    def _select_nzb_url(self, element: ET.Element) -> Optional[str]:
        """Extract the NZB download URL from the enclosure element."""
        enclosure = element.find("enclosure")
        if enclosure is not None:
            url = enclosure.get("url") or ""
            content_type = (enclosure.get("type") or "").lower()
            if self._is_nzb_url(url, content_type):
                return url
        fallback = self._get_text(element, "link", "")
        return fallback if self._is_nzb_url(fallback, "") else None

    def _is_nzb_url(self, url: str, content_type: str) -> bool:
        if not url or not url.startswith(("http://", "https://")):
            return False
        if "nzb" in content_type or "x-nzb" in content_type:
            return True
        if "/getnzb/" in url or "get=" in url:
            return True
        path = (urlparse(url).path or "").lower()
        return path.endswith(".nzb")

    def _rewrite_download_url(self, url: str) -> str:
        """
        If the indexer returns a download URL with a localhost/loopback host,
        rewrite it to use the configured base_url's host and port so that
        AuralArchive can actually fetch it.
        """
        if not url:
            return url
        try:
            parsed = urlparse(url)
            if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                return url
            configured_base = self.config.get("base_url", "").rstrip("/")
            if not configured_base:
                return url
            base_parsed = urlparse(configured_base)
            rewritten = urlunparse((
                base_parsed.scheme or parsed.scheme,
                base_parsed.netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))
            self.logger.debug(
                "Rewrote localhost download URL to configured base",
                extra={"original": url, "rewritten": rewritten},
            )
            return rewritten
        except ValueError:
            return url

    # --- Result shaping ---

    def _build_result(self, item: NewznabItem) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "indexer": self.name,
            "title": item.title,
            "download_url": self._rewrite_download_url(item.download_url),
            "size": item.size_bytes,
            "size_bytes": item.size_bytes,
            "publish_date": item.publish_date,
            "protocol": "nzb",
            "result_type": "nzb",
            "indexer_id": item.guid,
            "category": item.category,
            "info_url": item.info_url,
            "seeders": -1,
            "peers": -1,
        }

        format_info = self._extract_format(item.title)
        media = self._extract_media_details(item.attributes)

        if media.get("format"):
            format_info["format"] = media.pop("format")
        if media.get("bitrate"):
            format_info["bitrate"] = media.pop("bitrate")

        result["format"] = format_info.get("format", "unknown")
        result["bitrate"] = format_info.get("bitrate", 0)

        author = media.pop("author", None) or self._extract_author(item.title)
        if author:
            result["author"] = author
        for field_name in ("narrator", "language", "series", "sequence"):
            if media.get(field_name):
                result[field_name] = media.pop(field_name)

        if item.attributes:
            result["raw_attributes"] = item.attributes

        return result

    def _extract_media_details(self, attrs: Optional[Dict[str, str]]) -> Dict[str, Any]:
        """Extract known media fields from newznab:attr elements."""
        media: Dict[str, Any] = {}
        if not attrs:
            return media

        normalized = {str(k).lower(): v for k, v in attrs.items() if v is not None}

        def grab(*keys: str) -> Optional[str]:
            for key in keys:
                if normalized.get(key):
                    return normalized[key]
            return None

        bitrate = grab("bitrate", "audio:bitrate", "bitratekbps")
        if bitrate:
            media["bitrate"] = to_int(bitrate, 0)
        codec = grab("codec", "format", "audioformat")
        if codec:
            media["format"] = codec.lower()
        author = grab("author", "bookauthor", "writer")
        if author:
            media["author"] = author
        narrator = grab("narrator", "reader")
        if narrator:
            media["narrator"] = narrator
        language = grab("language", "lang")
        if language:
            media["language"] = language
        series = grab("series", "bookseries")
        if series:
            media["series"] = series
        sequence = grab("booknumber", "booknum", "seriesnumber", "volume")
        if sequence:
            media["sequence"] = sequence

        return media


    # --- Shared helpers ---

    def _extract_format(self, title: str) -> Dict[str, Any]:
        format_patterns = [
            r"\[([Mm]4[Bb])\]", r"\[([Mm]4[Aa])\]", r"\[([Mm][Pp]3)\]",
            r"\[([Ff][Ll][Aa][Cc])\]", r"\[([Aa][Aa][Cc])\]",
            r"\(([Mm]4[Bb])\)", r"\(([Mm]4[Aa])\)", r"\(([Mm][Pp]3)\)",
            r"\b([Mm]4[Bb])\b", r"\b([Mm]4[Aa])\b", r"\b([Mm][Pp]3)\b",
        ]
        bitrate_patterns = [
            r"[\[\(]?(\d+)\s*[Kk][Bb][Pp][Ss][\]\)]?",
            r"[\[\(]?(\d+)\s*[Kk][Bb]/[Ss][\]\)]?",
        ]
        result = {"format": "unknown", "bitrate": 0}
        for pattern in format_patterns:
            m = re.search(pattern, title)
            if m:
                result["format"] = m.group(1).lower()
                break
        for pattern in bitrate_patterns:
            m = re.search(pattern, title)
            if m:
                try:
                    result["bitrate"] = int(m.group(1))
                except ValueError:
                    pass
                break
        return result
