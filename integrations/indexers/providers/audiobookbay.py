"""
Module Name: audiobookbay.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Direct provider adapter that scrapes AudiobookBay search and detail pages
    to produce normalized torrent results.

Location:
    /integrations/indexers/providers/audiobookbay.py
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from . import register_provider
from .base import DirectProviderAdapter, ProviderRequestSpec
from core.logging import get_module_logger
from core import http


@register_provider
class AudiobookBayAdapter(DirectProviderAdapter):
    """Adapter that scrapes AudiobookBay search and detail pages."""

    key = "audiobookbay"
    domains = (
        "audiobookbay.",
        "audiobookbay.lu",
        "audiobookbay.se",
        "audiobookbay.li",
        "audiobookbay.tw",
        "audiobookbay.is",
    )

    SEARCH_PATH = "/"

    def __init__(self, config: Dict[str, Any], *, logger=None):
        super().__init__(config)
        self.timeout = config.get("timeout", 20)
        self.verify_ssl = config.get("verify_ssl", True)
        self.logger = logger or get_module_logger("Service.Indexers.AudiobookBay")
        self._last_query_params: Dict[str, str] = {}

    def build_health_request(self) -> ProviderRequestSpec:
        return ProviderRequestSpec(
            method="GET",
            path="/",
            headers={"User-Agent": self._user_agent()},
            expects_json=False,
            allow_missing=True,
        )

    def parse_health_response(self, payload: Any) -> Dict[str, Any]:
        return {
            "capabilities": {
                "search_available": True,
                "book_search_available": True,
                "author_search_available": True,
                "categories": [
                    {"id": "direct", "name": "AudiobookBay"},
                ],
                "limits": {"max": 30, "default": 20},
            },
            "version": "AudiobookBay",
        }

    def build_search_request(
        self,
        query: str,
        author: str,
        title: str,
        limit: int,
        offset: int,
    ) -> ProviderRequestSpec:
        focus = self._compose_query(query, author, title)
        clean = re.sub(r"[\W]+", " ", focus).strip().lower()
        params = {"s": clean, "tt": "1"}
        self._last_query_params = dict(params)
        return ProviderRequestSpec(
            method="GET",
            path=self.SEARCH_PATH,
            params=params,
            headers={"User-Agent": self._user_agent()},
            expects_json=False,
        )

    def parse_search_results(self, payload: Any) -> Sequence[Dict[str, Any]]:
        pages_html: List[str] = []
        page1 = payload or ""
        pages_html.append(page1 if isinstance(page1, str) else str(page1))

        page2 = self._fetch_search_page(page=2, params=self._last_query_params)
        if page2:
            pages_html.append(page2)

        results: List[Dict[str, Any]] = []
        seen_urls = set()

        for html in pages_html:
            soup = BeautifulSoup(html, "html.parser")
            posts = soup.select("div.post")
            if not posts:
                posts = soup.select("div.postTitle")

            for post in posts:
                detail_url = self._extract_detail_url(post, soup)
                if not detail_url or detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)
                title = self._extract_title(post) or None
                parsed = self._fetch_and_parse_detail(detail_url, title)
                if parsed:
                    results.append(parsed)
        return results

    # --- Scraping helpers ---

    def _extract_detail_url(self, post: Any, soup: BeautifulSoup) -> Optional[str]:
        link = None
        if hasattr(post, "select_one"):
            link = post.select_one("div.postTitle a") or post.select_one("a")
        if not link and soup:
            canonical = soup.find("link", rel="canonical")
            if canonical and canonical.get("href"):
                return urljoin(f"{self.base_url}/", canonical.get("href"))
        if not link or not link.get("href"):
            return None
        href = link.get("href")
        return urljoin(f"{self.base_url}/", href)

    @staticmethod
    def _extract_title(post: Any) -> str:
        if not hasattr(post, "select_one"):
            return ""
        title_el = post.select_one("div.postTitle a") or post.select_one("div.postTitle h1")
        if not title_el:
            return ""
        return title_el.get_text(strip=True)

    def _fetch_and_parse_detail(self, detail_url: str, fallback_title: Optional[str]) -> Optional[Dict[str, Any]]:
        try:
            response = http.get(
                detail_url,
                headers={"User-Agent": self._user_agent()},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            if response.status_code >= 400:
                self.logger.debug("Detail fetch failed %s status=%s", detail_url, response.status_code)
                return None
            return self._parse_detail_page(response.text, detail_url, fallback_title)
        except Exception as exc:
            self.logger.debug("Detail fetch error %s: %s", detail_url, exc)
            return None

    def _fetch_search_page(self, page: int, params: Optional[Dict[str, str]]) -> Optional[str]:
        if page < 2:
            return None
        query = ""
        if params:
            from urllib.parse import urlencode

            query = "?" + urlencode(params)
        url = urljoin(f"{self.base_url}/", f"page/{page}/{query}")
        try:
            resp = http.get(
                url,
                headers={"User-Agent": self._user_agent()},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException:
            self.logger.debug("Search page %s failed", url, exc_info=True)
            return None
        if resp.status_code >= 400:
            self.logger.debug("Search page %s returned HTTP %s", url, resp.status_code)
            return None
        return resp.text

    def _parse_detail_page(self, html: str, detail_url: str, fallback_title: Optional[str]) -> Dict[str, Any]:
        soup = BeautifulSoup(html or "", "html.parser")

        title = fallback_title
        title_el = soup.select_one("div.postTitle h1")
        if title_el:
            title = title_el.get_text(strip=True)
        title = title or "Unknown"

        info_hash = self._extract_table_value(soup, "info hash")
        size_text = self._extract_table_value(soup, "combined file size")
        size_bytes = self._parse_size(size_text)

        format_el = soup.select_one("div.desc .format")
        fmt = format_el.get_text(strip=True) if format_el else None

        author_el = soup.select_one("div.desc .author")
        author = author_el.get_text(strip=True) if author_el else None

        category_el = soup.select_one("div.postInfo a")
        category = category_el.get_text(strip=True) if category_el else "direct"

        trackers = self._extract_trackers(soup)
        download_url = self._extract_download_url(soup)
        magnet_uri = self._build_magnet(info_hash, trackers, title) if info_hash else None

        publish_meta = soup.select_one('meta[itemprop="datePublished"]')
        publish_date = publish_meta.get("content") if publish_meta else None

        cover_el = soup.select_one("div.postContent img[itemprop='image']")
        cover_url = cover_el.get("src") if cover_el and cover_el.get("src") else None
        if cover_url:
            cover_url = urljoin(f"{self.base_url}/", cover_url)

        return {
            "indexer": self.indexer_name,
            "title": title,
            "author": author,
            "narrator": None,
            "series": None,
            "sequence": None,
            "language": None,
            "format": fmt,
            "bitrate": 0,
            "size": size_bytes,
            "size_bytes": size_bytes,
            "seeders": 1,
            "peers": 1,
            "protocol": "torrent",
            "indexer_id": info_hash or detail_url,
            "category": category,
            "publish_date": publish_date,
            "download_url": magnet_uri or download_url or detail_url,
            "info_url": detail_url,
            "info_hash": info_hash,
            "magnet_uri": magnet_uri,
            "torrent_url": download_url,
            "cover_url": cover_url,
            "trackers": trackers,
            "_source": "direct-audiobookbay",
        }

    @staticmethod
    def _extract_table_value(soup: BeautifulSoup, label: str) -> Optional[str]:
        label_lower = label.lower()
        selectors = ("table.torrent_info td", "table.torrent_infos td", "table td")
        seen_cells = []
        for selector in selectors:
            cells = soup.select(selector)
            if cells:
                seen_cells = cells
                break
        for cell in seen_cells:
            text = cell.get_text(" ", strip=True).lower()
            if label_lower in text:
                sibling = cell.find_next_sibling("td")
                if sibling:
                    return sibling.get_text(" ", strip=True)
        return None

    def _extract_download_url(self, soup: BeautifulSoup) -> Optional[str]:
        link = soup.find("a", href=re.compile(r"downld", re.IGNORECASE))
        if link and link.get("href"):
            return urljoin(f"{self.base_url}/", link.get("href"))
        return None

    @staticmethod
    def _parse_size(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return 0
        match = re.match(r"(?P<num>[\d]+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]+)", text)
        if not match:
            return 0
        number = float(match.group("num"))
        unit = match.group("unit").lower().rstrip("s")
        multipliers = {
            "b": 1,
            "bytes": 1,
            "kb": 1024,
            "kib": 1024,
            "mb": 1024 ** 2,
            "mib": 1024 ** 2,
            "gb": 1024 ** 3,
            "gib": 1024 ** 3,
            "tb": 1024 ** 4,
            "tib": 1024 ** 4,
        }
        multiplier = multipliers.get(unit, 1)
        return int(number * multiplier)

    @staticmethod
    def _extract_trackers(soup: BeautifulSoup) -> List[str]:
        trackers: List[str] = []
        selectors = ("table.torrent_info td", "table.torrent_infos td", "table td")
        seen_cells = []
        for selector in selectors:
            cells = soup.select(selector)
            if cells:
                seen_cells = cells
                break
        for cell in seen_cells:
            text = cell.get_text(" ", strip=True).lower()
            if text.startswith("tracker") or text.startswith("announce"):
                sibling = cell.find_next_sibling("td")
                if sibling:
                    tracker_url = sibling.get_text(" ", strip=True)
                    if tracker_url:
                        trackers.append(tracker_url)
        return trackers

    def _build_magnet(self, info_hash: str, trackers: List[str], title: str) -> str:
        if not info_hash:
            return ""
        magnet = f"magnet:?xt=urn:btih:{info_hash.strip()}"
        if title:
            magnet += f"&dn={quote_plus(title)}"
        for tracker in trackers:
            magnet += f"&tr={quote_plus(tracker)}"
        return magnet

    @staticmethod
    def _compose_query(query: str, author: str, title: str) -> str:
        parts = [part.strip() for part in (title, author, query) if part]
        if not parts:
            return ""
        return " ".join(parts)

    @staticmethod
    def _user_agent() -> str:
        return "Mozilla/5.0 (X11; Linux x86_64) AuralArchive/DirectIndexer"