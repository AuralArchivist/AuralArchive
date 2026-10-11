"""
Module Name: torznab_base_indexer.py
Author: AuralArchivist
Created: February 18, 2026
Last Modified: October 9, 2026
Description:
    Base class for Torznab indexers (Jackett, Prowlarr).

Location:
    /integrations/indexers/torznab_base_indexer.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse
import xml.etree.ElementTree as ET

import requests

from core.convert import to_int

from .feed_indexer import FeedIndexer


TORZNAB_NS = {"torznab": "http://torznab.com/schemas/2015/feed"}


@dataclass
class TorznabItem:
    """Normalised representation of a single Torznab feed item."""

    title: str
    download_url: str
    info_url: str
    publish_date: str
    size_bytes: int
    seeders: int
    peers: int
    category: str
    guid: str
    attributes: Dict[str, str] = field(default_factory=dict)


class TorznabBaseIndexer(FeedIndexer):
    """
    Protocol base for Torznab indexers.

    Torznab search parameters, torrent/magnet links and result shaping on
    top of FeedIndexer. Subclasses compute the API endpoint and pass it in.

    Example subclass::

        class JackettIndexer(TorznabBaseIndexer):
            def __init__(self, config, *, logger=None):
                config = dict(config)
                api_endpoint = f"{config['base_url']}/api/v2.0/indexers/all/results/torznab"
                super().__init__(config, api_endpoint, logger=logger)
    """

    PROTOCOL = "torznab"
    PROTOCOL_LABEL = "Torznab"
    NS = TORZNAB_NS

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
                "Indexer unavailable, skipping search",
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
                combined = cleaned_query or query
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
                "Torznab search complete",
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
                "Error searching Torznab indexer",
                extra={"indexer_name": self.name},
            )
        return []


    # --- XML parsing ---

    def _parse_single_item(self, element: ET.Element) -> Optional[TorznabItem]:
        title = self._get_text(element, "title", "")
        info_url = self._get_text(element, "comments", "")
        guid = self._get_text(element, "guid", "")
        publish_date = self._normalize_date(self._get_text(element, "pubDate", ""))
        size_bytes = to_int(self._get_text(element, "size", "0"))
        attrs = self._extract_attributes(element)

        download_url = self._select_torrent_url(element)
        if not download_url:
            info_hash = attrs.get("infohash")
            if info_hash:
                download_url = self._build_magnet_from_attributes(
                    info_hash, attrs, title
                )
        if not download_url:
            self.logger.debug(
                "Skipping item - no .torrent URL or infohash",
                extra={"title": title},
            )
            return None

        return TorznabItem(
            title=title,
            download_url=download_url,
            info_url=info_url,
            publish_date=publish_date,
            size_bytes=size_bytes,
            seeders=to_int(attrs.get("seeders", -1), default=-1),
            peers=to_int(attrs.get("peers", -1), default=-1),
            category=attrs.get("category", self.CATEGORY_AUDIOBOOK),
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
                elif tag == "author-search":
                    caps["author_search_available"] = available

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

    # --- Torrent URL / magnet helpers ---

    def _select_torrent_url(self, element: ET.Element) -> Optional[str]:
        enclosure = element.find("enclosure")
        if enclosure is not None:
            url = enclosure.get("url") or ""
            content_type = (enclosure.get("type") or "").lower()
            if self._is_torrent_url(url, content_type):
                return url
        fallback = self._get_text(element, "link", "")
        return fallback if self._is_torrent_url(fallback, "") else None

    def _is_torrent_url(self, url: str, content_type: str) -> bool:
        if not url or not url.startswith(("http://", "https://")):
            return False
        if "bittorrent" in content_type:
            return True
        parsed = urlparse(url)
        if (parsed.path or "").lower().endswith(".torrent"):
            return True
        query_params = parse_qs(parsed.query)
        filename_params = query_params.get("file") or query_params.get("filename")
        return bool(
            filename_params
            and any(n.lower().endswith(".torrent") for n in filename_params)
        )

    def _build_magnet_from_attributes(
        self,
        info_hash: str,
        attrs: Dict[str, str],
        title: str,
    ) -> Optional[str]:
        info_hash = info_hash.strip()
        if not info_hash:
            return None

        trackers: List[str] = []
        raw = attrs.get("tracker") or attrs.get("trackers")
        if raw:
            tokens = [raw]
            for sep in ["|", ",", ";"]:
                tokens = [sub for tok in tokens for sub in tok.split(sep)]
            trackers.extend(t.strip() for t in tokens if t.strip())

        if not trackers:
            trackers = [
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://tracker.torrent.eu.org:451/announce",
                "udp://tracker.openbittorrent.com:6969/announce",
                "udp://exodus.desync.com:6969/announce",
                "udp://tracker.dler.org:6969/announce",
                "udp://tracker.moeking.me:6969/announce",
            ]

        display_name = attrs.get("dn") or attrs.get("title") or title
        parts = [f"magnet:?xt=urn:btih:{info_hash.lower()}"]
        parts.extend(f"tr={quote(tr, safe=':/?&=')}" for tr in trackers)
        if display_name:
            parts.append(f"dn={quote(display_name)}")
        return "&".join(parts)


    # --- Result shaping ---

    def _build_result(self, item: TorznabItem) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "indexer": self.name,
            "title": item.title,
            "download_url": item.download_url,
            "size": item.size_bytes,
            "size_bytes": item.size_bytes,
            "publish_date": item.publish_date,
            "seeders": item.seeders,
            "peers": item.peers,
            "protocol": "torrent",
            "result_type": "torrent",
            "indexer_id": item.guid,
            "category": item.category,
            "info_url": item.info_url,
        }

        format_info = self._extract_format(item.title)
        media_primary, media_extras = self._extract_media_details(item.attributes)

        if media_primary.get("format"):
            format_info["format"] = media_primary.pop("format")
        if media_primary.get("bitrate"):
            format_info["bitrate"] = media_primary.pop("bitrate")

        result["format"] = format_info.get("format", "unknown")
        result["bitrate"] = format_info.get("bitrate", 0)

        author = media_primary.pop("author", None) or self._extract_author(item.title)
        if author:
            result["author"] = author
        for field_name in ("narrator", "language", "series", "sequence"):
            if media_primary.get(field_name):
                result[field_name] = media_primary.pop(field_name)
        if media_extras:
            result["media_info"] = media_extras
        freeleech = self._freeleech_status(item.title, item.attributes)
        if freeleech:
            result["freeleech"] = freeleech
        if item.attributes:
            result["raw_attributes"] = item.attributes

        return result

    @staticmethod
    def _freeleech_status(title: str, attrs: Optional[Dict[str, str]]) -> Optional[str]:
        """'vip' or 'fl' in the same form the direct MAM adapter uses. Torznab
        marks freeleech with downloadvolumefactor 0; Prowlarr also tags MAM VIP
        torrents in the title and the tag attribute."""
        attrs = attrs or {}
        tags = {t.strip().lower() for t in str(attrs.get("tag") or "").split(",") if t.strip()}
        if "[vip]" in (title or "").lower() or "vip" in tags:
            return "vip"
        try:
            free = float(attrs.get("downloadvolumefactor", 1)) == 0
        except (TypeError, ValueError):
            free = False
        return "fl" if free or "freeleech" in tags else None

    def _extract_media_details(
        self,
        attrs: Optional[Dict[str, str]],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Return (primary_fields, extra_media_info) from Torznab attributes."""
        primary: Dict[str, Any] = {}
        extras: Dict[str, Any] = {}
        if not attrs:
            return primary, extras

        normalized = {str(k).lower(): v for k, v in attrs.items() if v is not None}

        def grab(*keys: str) -> Optional[str]:
            for key in keys:
                if normalized.get(key):
                    return normalized[key]
            return None

        bitrate = grab("bitrate", "audio:bitrate", "bitratekbps", "audio_bitrate")
        if bitrate:
            primary["bitrate"] = to_int(bitrate, 0)
        codec = grab("codec", "format", "audioformat", "encoding")
        if codec:
            primary["format"] = codec.lower()
        author = grab("author", "bookauthor", "writer")
        if author:
            primary["author"] = author
        narrator = grab("narrator", "reader")
        if narrator:
            primary["narrator"] = narrator
        language = grab("language", "lang")
        if language:
            primary["language"] = language
        series = grab("series", "bookseries")
        if series:
            primary["series"] = series
        sequence = grab(
            "booknumber", "booknum", "seriesnumber", "volume", "issue", "sequence"
        )
        if sequence:
            primary["sequence"] = sequence

        channels = grab("channels", "audio:channels")
        if channels:
            extras["channels"] = channels
        samplerate = grab("samplerate", "sample_rate")
        if samplerate:
            extras["sample_rate"] = samplerate
        runtime = grab("duration", "runtime", "length")
        if runtime:
            extras["runtime"] = runtime
        quality = grab("quality", "releasequality")
        if quality:
            extras["quality"] = quality

        return primary, extras

    # --- Shared helpers ---

    def _extract_format(self, title: str) -> Dict[str, Any]:
        format_patterns = [
            r"\[([Mm]4[Bb])\]", r"\[([Mm]4[Aa])\]", r"\[([Mm][Pp]3)\]",
            r"\[([Ff][Ll][Aa][Cc])\]", r"\[([Aa][Aa][Cc])\]", r"\[([Oo][Gg][Gg])\]",
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
