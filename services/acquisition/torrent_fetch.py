"""
Module Name: torrent_fetch.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Fetches .torrent files for the download client, including direct
    providers that need a session cookie.

Location:
    /services/acquisition/torrent_fetch.py
"""

import re
from html import unescape
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from core import http
from core.net_guard import OutboundURLRejected, validate_outbound_url
from services.container import container


def _looks_like_torrent_payload(content: bytes) -> bool:
    """Best-effort check that payload bytes resemble a bencoded .torrent file."""
    if not content:
        return False

    sample = content[:4096].lstrip()
    if not sample:
        return False

    lower_sample = sample.lower()
    html_or_text_markers = (
        b"<!doctype html",
        b"<html",
        b"<?xml",
        b"<body",
        b"<head",
    )
    if any(lower_sample.startswith(marker) for marker in html_or_text_markers):
        return False

    if sample[:1] != b"d":
        return False

    return b"4:info" in content and (b"8:announce" in content or b"13:announce-list" in content)


def _extract_magnet_from_text(text: str) -> Optional[str]:
    """Extract the first magnet URI from text/HTML payloads."""
    if not text:
        return None
    decoded = unescape(text)
    match = re.search(r"magnet:\?xt=urn:btih:[A-Za-z0-9]+[^\s\"'<>]*", decoded, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(0).strip()


def _extract_info_hash_from_text(text: str) -> Optional[str]:
    """Extract a likely 40-char hex info-hash from provider HTML."""
    if not text:
        return None
    decoded = unescape(text)
    labeled = re.search(
        r"info\s*hash[^0-9a-fA-F]{0,40}([0-9a-fA-F]{40})",
        decoded,
        flags=re.IGNORECASE,
    )
    if labeled:
        return labeled.group(1).lower()

    generic = re.search(r"\b([0-9a-fA-F]{40})\b", decoded)
    if generic:
        return generic.group(1).lower()
    return None


def _extract_torrent_url_from_html(text: str, base_url: str) -> Optional[str]:
    """Find an embedded .torrent URL in HTML and resolve to absolute URL."""
    if not text:
        return None
    decoded = unescape(text)
    match = re.search(r"href=[\"']([^\"']+\.torrent[^\"']*)[\"']", decoded, flags=re.IGNORECASE)
    if not match:
        return None
    href = match.group(1).strip()
    if not href:
        return None
    return urljoin(base_url, href)


def _looks_like_login_gate(response: requests.Response, text_body: str) -> bool:
    """Detect common provider login/redirect pages returned instead of torrent payload."""
    response_url = (getattr(response, "url", "") or "").lower()
    body_lower = (text_body or "").lower()

    indicators = (
        "/member/login",
        "member/login",
        "signin",
        "log in",
        "login",
    )

    if any(indicator in response_url for indicator in indicators):
        return True

    if "http-equiv='refresh'" in body_lower and "member/login" in body_lower:
        return True

    return False


class TorrentFetchMixin:
    """Torrent file fetching for the grab stage. Mixed into DownloadManagementService."""

    def _load_direct_provider_sessions(self) -> None:
        """Build a map of direct providers that require session cookies for downloads."""

        mapping: Dict[str, Dict[str, str]] = {}

        try:
            config_service = container.get_config_service()
            indexers = config_service.list_indexers_config() or {}
        except Exception as exc:
            self.logger.debug("Unable to load direct provider sessions: %s", exc)
            self.direct_provider_sessions = mapping
            return

        for key, data in indexers.items():
            if not isinstance(data, dict):
                continue

            enabled = data.get('enabled', True)
            if isinstance(enabled, str):
                enabled = enabled.strip().lower() in {'true', '1', 'yes', 'on'}
            if not enabled:
                continue

            idx_type = str(data.get('type', '')).lower()
            protocol = str(data.get('protocol', '')).lower()
            if idx_type != 'direct' and protocol != 'direct':
                continue

            session_id = (data.get('session_id') or '').strip()
            base_url = (data.get('base_url') or '').strip()
            if not session_id or not base_url:
                continue

            hostname = urlparse(base_url).hostname or base_url
            if not hostname:
                continue

            host_keys = {hostname.lower()}
            if hostname.lower().startswith('www.'):
                host_keys.add(hostname.lower()[4:])

            meta = {
                'session_id': session_id,
                'indexer': data.get('name') or key,
                'base_url': base_url.rstrip('/'),
            }

            for host in host_keys:
                mapping[host] = meta

        self.direct_provider_sessions = mapping
        if mapping:
            self.logger.debug(
                "Loaded %d direct provider session(s) for download bridge",
                len(mapping),
            )

    def _resolve_direct_session(self, download_url: str) -> Optional[Dict[str, str]]:
        if not self.direct_provider_sessions:
            return None

        try:
            hostname = urlparse(download_url).hostname or ''
        except ValueError:
            hostname = ''

        if not hostname:
            return None

        candidates = [hostname.lower()]
        if hostname.lower().startswith('www.'):
            candidates.append(hostname.lower()[4:])

        for candidate in candidates:
            meta = self.direct_provider_sessions.get(candidate)
            if meta:
                return meta
        return None

    def _fetch_remote_torrent(self, download_url: str, download: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Any], str]:
        """Retrieve the torrent payload, handling redirects to magnet URIs when necessary."""

        try:
            validate_outbound_url(download_url, default_scheme=None)
        except OutboundURLRejected as exc:
            self.logger.warning("Refusing to fetch torrent URL: %s", exc)
            return None, "rejected"

        self._load_direct_provider_sessions()
        session_meta = self._resolve_direct_session(download_url)
        max_attempts = 2
        attempt = 0

        def _build_headers(meta: Optional[Dict[str, str]]) -> Tuple[Dict[str, str], Optional[Dict[str, str]]]:
            headers = {
                "User-Agent": "AuralArchive-DownloadBridge/1.0",
                "Accept": "application/x-bittorrent, application/octet-stream;q=0.9, */*;q=0.1",
            }
            cookies: Optional[Dict[str, str]] = None
            if meta:
                session_id = meta.get('session_id', '').strip()
                if session_id:
                    headers.update({
                        "Authorization": f"Bearer {session_id}",
                        "X-Session-ID": session_id,
                        "Referer": meta.get('base_url') or download_url,
                    })
                    cookies = {
                        "session": session_id,
                        "session_id": session_id,
                        "mam_id": session_id,
                    }
            return headers, cookies

        current_meta = session_meta

        while attempt < max_attempts:
            headers, cookies = _build_headers(current_meta)
            try:
                request_kwargs = {
                    "timeout": 30,
                    "verify": self.jackett_verify_ssl,
                    "headers": headers,
                    "allow_redirects": False,
                }
                if cookies:
                    request_kwargs["cookies"] = cookies

                response = http.get(download_url, **request_kwargs)

                if response.status_code in {401, 403}:
                    current_session = (current_meta or {}).get('session_id', '').strip()
                    self.logger.warning(
                        "Direct provider auth rejected torrent fetch for %s (attempt %s, has_session=%s)",
                        download_url,
                        attempt + 1,
                        bool(current_session),
                    )

                    if attempt + 1 < max_attempts:
                        self._load_direct_provider_sessions()
                        refreshed_meta = self._resolve_direct_session(download_url)
                        refreshed_session = (refreshed_meta or {}).get('session_id', '').strip()
                        if refreshed_session and refreshed_session != current_session:
                            current_meta = refreshed_meta
                            attempt += 1
                            continue

                    if not current_session:
                        raise PermissionError(
                            "Direct provider session is missing for torrent download; "
                            "set a valid session_id in Settings -> Indexers"
                        )

                    raise PermissionError(
                        f"Direct provider authentication failed for {download_url}"
                    )

                if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
                    location = (response.headers.get("Location") or "").strip()
                    if location.lower().startswith("magnet:"):
                        return location, "magnet"
                    if location:
                        location = urljoin(download_url, location)
                        try:
                            validate_outbound_url(location, default_scheme=None)
                        except OutboundURLRejected as exc:
                            self.logger.warning("Refusing torrent redirect target: %s", exc)
                            return None, "rejected"
                        self.logger.debug("Following redirect for torrent payload: %s", location)
                        same_host = urlparse(location).netloc == urlparse(download_url).netloc
                        follow_kwargs = {
                            "timeout": 30,
                            "verify": self.jackett_verify_ssl,
                            # Indexer credentials only go back to the indexer's own host.
                            "headers": headers if same_host else {"User-Agent": headers.get("User-Agent", "AuralArchive")},
                            "allow_redirects": False,
                        }
                        if cookies and same_host:
                            follow_kwargs["cookies"] = cookies
                        response = http.get(location, **follow_kwargs)

                response.raise_for_status()

                content_type_header = response.headers.get("Content-Type", "")
                content_type_lower = content_type_header.lower()

                text_body = response.text.strip() if content_type_lower.startswith("text/") else ""
                if text_body.lower().startswith("magnet:"):
                    return text_body, "magnet"

                if content_type_lower.startswith("text/") and _looks_like_login_gate(response, text_body):
                    raise PermissionError(
                        "Direct provider authentication appears invalid or expired; "
                        "refresh the provider session_id in Settings -> Indexers"
                    )

                if content_type_lower.startswith("text/"):
                    html_magnet = _extract_magnet_from_text(response.text)
                    if html_magnet:
                        return html_magnet, "magnet"

                    html_hash = _extract_info_hash_from_text(response.text)
                    if html_hash:
                        return f"magnet:?xt=urn:btih:{html_hash}", "magnet"

                    html_torrent_url = _extract_torrent_url_from_html(response.text, response.url)
                    if html_torrent_url:
                        self.logger.debug("Following embedded .torrent URL from HTML payload: %s", html_torrent_url)
                        follow_kwargs = {
                            "timeout": 30,
                            "verify": self.jackett_verify_ssl,
                            "headers": headers,
                        }
                        if cookies:
                            follow_kwargs["cookies"] = cookies
                        response = http.get(html_torrent_url, **follow_kwargs)
                        response.raise_for_status()
                        content_type_header = response.headers.get("Content-Type", "")
                        content_type_lower = content_type_header.lower()

                content = response.content
                if not content:
                    self.logger.warning("Torrent download returned empty payload from %s", download_url)
                    return None, ""

                prefix_text = content[:1024].decode("utf-8", errors="ignore").strip()
                if prefix_text.lower().startswith("magnet:"):
                    magnet_uri = prefix_text.splitlines()[0].strip()
                    if magnet_uri:
                        return magnet_uri, "magnet"

                if not _looks_like_torrent_payload(content):
                    self.logger.warning(
                        "Fetched payload from %s does not look like a valid .torrent file (content-type='%s')",
                        download_url,
                        content_type_header,
                    )
                    return None, ""

                if 'bittorrent' not in content_type_lower and not download_url.lower().endswith('.torrent'):
                    self.logger.debug(
                        "Torrent payload from %s has unexpected content-type '%s'",
                        download_url,
                        content_type_header,
                    )

                return content, "bytes"

            except requests.RequestException as exc:
                self.logger.error("Failed to fetch torrent file from %s: %s", download_url, exc)
                return None, ""

        return None, ""

    def _is_direct_provider_download(self, download: Dict[str, Any], download_url: str) -> bool:
        """Best-effort detection for direct-provider sourced torrents."""
        indexer_name = str(download.get('indexer') or '').strip().lower()
        if indexer_name:
            for meta in self.direct_provider_sessions.values():
                candidate = str(meta.get('indexer') or '').strip().lower()
                if candidate and candidate == indexer_name:
                    return True

        try:
            host = (urlparse(download_url).hostname or '').lower()
        except ValueError:
            host = ''
        if host:
            if host in self.direct_provider_sessions:
                return True
            if host.startswith('www.') and host[4:] in self.direct_provider_sessions:
                return True
        return False
