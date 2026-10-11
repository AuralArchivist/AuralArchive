"""
Module Name: audible_download_helper.py
Author: AuralArchivist
Created: August 20, 2025
Last Modified: October 9, 2026
Description:
    Download Audible content with cooperative cancellation and progress reporting.

Location:
    /integrations/audible/download/audible_download_helper.py
"""

import asyncio
import json
import secrets
import time
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode

import audible
from audible.exceptions import NotResponding

from core.logging import get_module_logger
from core.paths import resolve_audible_auth_file
from core.config.secrets_crypto import get_audible_auth_password
from integrations.audible.voucher import decrypt_license_response


class AudibleDownloadHelper:
    """
    Helper class for downloading audiobooks from Audible using the Python API.

    Supports both AAX and AAXC formats with real-time progress tracking
    and accepts a cancellation event to stop long-running downloads.
    """

    def __init__(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_event: Optional[Event] = None
    ):
        self.logger = get_module_logger("Service.Audible.DownloadHelper")
        self.auth_file = Path(resolve_audible_auth_file())
        self.auth = None
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event
        self._load_auth()

    def _load_auth(self):
        """Load authentication from file."""
        if not self.auth_file.exists():
            raise Exception("Authentication file not found. Please authenticate first.")

        try:
            self.auth = audible.Authenticator.from_file(self.auth_file, password=get_audible_auth_password())
            self.logger.debug("Loaded Audible authentication", extra={"auth_file": str(self.auth_file)})
        except Exception as exc:
            raise Exception(f"Failed to load authentication: {exc}") from exc

    def _raise_if_cancelled(self):
        """Raise asyncio.CancelledError if a cancellation request is pending."""
        if self.cancel_event and self.cancel_event.is_set():
            self.logger.info("Audible download cancellation requested; aborting transfer")
            raise asyncio.CancelledError("Audible download cancelled")

    def _call_with_retries(self, request_call: Callable[[], Any], label: str, retries: int = 3) -> Any:
        """Retry timeout-prone Audible API calls with exponential backoff."""
        for attempt in range(1, retries + 1):
            self._raise_if_cancelled()
            try:
                return request_call()
            except NotResponding:
                if attempt >= retries:
                    raise
                backoff_seconds = attempt * 2
                self.logger.warning(
                    "Audible API timeout during %s (attempt %s/%s); retrying in %ss",
                    label,
                    attempt,
                    retries,
                    backoff_seconds,
                )
                time.sleep(backoff_seconds)

    def _get_codec_info(self, available_codecs: list, quality: str) -> tuple:
        """Return the best codec tuple for the requested quality."""
        if not available_codecs:
            return None, None

        codec_high_quality = "AAX_44_128"
        codec_normal_quality = "AAX_22_64"

        if quality == "best":
            best = (None, 0, 0, None)
            for codec in available_codecs:
                name = codec.get("name", "")
                if not name.startswith("aax_"):
                    continue
                try:
                    sample_rate_str, bitrate_str = name[4:].split("_")
                    sample_rate = int(sample_rate_str)
                    bitrate = int(bitrate_str)
                except (ValueError, AttributeError):
                    continue

                if sample_rate > best[1] or (sample_rate == best[1] and bitrate > best[2]):
                    best = (name.upper(), sample_rate, bitrate, codec.get("enhanced_codec"))

            return best[0], best[3] if best[0] else (None, None)

        verify = codec_high_quality if quality == "high" else codec_normal_quality
        for codec in available_codecs:
            if verify == codec.get("name", "").upper():
                return verify, codec.get("enhanced_codec")

        return None, None

    def download_file(
        self,
        client: audible.Client,
        url: str,
        output_path: Path,
        expected_content_types: Optional[list] = None
    ) -> bool:
        """
        Download a file from URL with progress tracking using an authenticated client.
        """
        expected = expected_content_types or [
            "audio/aax",
            "audio/vnd.audible.aax",
            "audio/audible",
            "audio/mpeg",
            "audio/x-m4a",
        ]

        self._raise_if_cancelled()
        self.logger.debug("Starting download", extra={"output_path": str(output_path)})

        with client.session.stream("GET", url, follow_redirects=True) as response:
            self._raise_if_cancelled()
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if expected and content_type not in expected:
                self.logger.warning("Unexpected content type", extra={"content_type": content_type})

            try:
                total_size = int(response.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                total_size = 0

            self.logger.debug(
                "Download size",
                extra={
                    "bytes": total_size,
                    "mb": total_size / 1024 / 1024 if total_size else 0.0,
                },
            )

            downloaded = 0
            chunk_size = 65536
            last_progress_reported = -1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "wb") as target:
                for chunk in response.iter_bytes(chunk_size):
                    self._raise_if_cancelled()
                    if not chunk:
                        continue
                    target.write(chunk)
                    downloaded += len(chunk)

                    if self.progress_callback:
                        if total_size > 0:
                            progress_pct = int((downloaded / total_size) * 100)
                            if progress_pct > last_progress_reported:
                                last_progress_reported = progress_pct
                                self.progress_callback(downloaded, total_size, f"{progress_pct}% complete")
                        else:
                            if downloaded - max(last_progress_reported, 0) >= 5 * 1024 * 1024:
                                last_progress_reported = downloaded
                                self.progress_callback(downloaded, total_size, "downloading...")

            self._raise_if_cancelled()
            if self.progress_callback and total_size > 0 and last_progress_reported < 100:
                self.progress_callback(total_size, total_size, "100% complete")
            self.logger.debug("Download complete", extra={"output_path": str(output_path)})

        return True

    def download_aax(
        self,
        client: audible.Client,
        asin: str,
        output_dir: Path,
        filename: str,
        quality: str = "best"
    ) -> Path:
        """Download a book in AAX format using an authenticated client."""
        self._raise_if_cancelled()

        library_response = self._call_with_retries(
            lambda: client.get(
                "library",
                asin=asin,
                response_groups="media, product_attrs"
            ),
            label=f"library lookup for {asin}",
        )
        self._raise_if_cancelled()

        if not library_response.get("items"):
            raise Exception(f"Book {asin} not found in library")

        item = library_response["items"][0]
        codec_name, _ = self._get_codec_info(item.get("available_codecs", []), quality)
        if not codec_name:
            raise Exception(f"No AAX codec available for quality '{quality}'")

        domain = self.auth.locale.domain
        params = urlencode({"asin": asin, "codec": codec_name})
        url = f"https://www.audible.{domain}/library/download?{params}"

        output_path = output_dir / f"{filename}-{codec_name}.aax"
        self.download_file(
            client,
            url,
            output_path,
            expected_content_types=["audio/aax", "audio/vnd.audible.aax", "audio/audible"],
        )
        self._raise_if_cancelled()
        return output_path

    def _store_decrypted_keys(self, client: audible.Client, asin: str, license_payload: Any) -> None:
        """Swap the device-locked license_response for its key/iv.

        The encrypted form can only be read by the sign-in it was issued to,
        so a later Audible re-sign-in would strand the downloaded file.
        """
        if not isinstance(license_payload, dict):
            return
        content_license = license_payload.get('content_license') or {}
        encrypted = content_license.get('license_response')
        if not isinstance(encrypted, str):
            return
        try:
            content_license['license_response'] = decrypt_license_response(client.auth, asin, encrypted)
        except Exception as exc:
            self.logger.warning("Could not decrypt voucher keys; keeping the encrypted voucher",
                                extra={"asin": asin, "error": str(exc)})

    def download_aaxc(
        self,
        client: audible.Client,
        asin: str,
        output_dir: Path,
        filename: str,
        quality: str = "best",
        save_voucher: bool = True
    ) -> tuple:
        """Download a book in AAXC format (with voucher) using an authenticated client."""
        self._raise_if_cancelled()

        quality_setting = "High" if quality in ("best", "high") else "Normal"
        body = {
            "supported_drm_types": ["Mpeg", "Adrm"],
            "quality": quality_setting,
            "consumption_type": "Download",
            "response_groups": "last_position_heard, pdf_url, content_reference",
        }
        headers = {
            "X-Amzn-RequestId": secrets.token_hex(20).upper(),
            "X-ADP-SW": "37801821",
            "X-ADP-Transport": "WIFI",
            "X-ADP-LTO": "120",
            "X-Device-Type-Id": "A2CZJZGLK2JJVM",
            "device_idiom": "phone",
        }

        license_response = self._call_with_retries(
            lambda: client.post(
                f"content/{asin}/licenserequest",
                body=body,
                headers=headers,
            ),
            label=f"license request for {asin}",
        )
        self._raise_if_cancelled()
        self.logger.info("AAXC license obtained", extra={"asin": asin})

        content_metadata = license_response["content_license"]["content_metadata"]
        url = content_metadata["content_url"]["offline_url"]
        codec = content_metadata["content_reference"]["content_format"]
        extension = "mp3" if codec.lower() == "mpeg" else "aaxc"

        audio_path = output_dir / f"{filename}-{codec}.{extension}"
        self.download_file(client, url, audio_path)
        self._raise_if_cancelled()

        voucher_path = None
        if save_voucher:
            voucher_path = audio_path.with_suffix(".voucher")
            license_payload = license_response
            if isinstance(license_payload, str):
                try:
                    license_payload = json.loads(license_payload)
                except json.JSONDecodeError:
                    self.logger.warning(
                        "Voucher payload returned as string but could not be parsed; saving raw text",
                        extra={"asin": asin}
                    )
            self._store_decrypted_keys(client, asin, license_payload)
            voucher_path.write_text(json.dumps(license_payload, indent=4))
            voucher_path.chmod(0o600)
            self.logger.info("Voucher saved", extra={"voucher_path": str(voucher_path)})

        return audio_path, voucher_path

    def download_book(
        self,
        asin: str,
        output_dir: Path,
        filename: str,
        format_preference: str = "aaxc",
        quality: str = "best",
        aax_fallback: bool = True
    ) -> Dict[str, Any]:
        """Download a book with format fallback and cooperative cancellation."""
        self._raise_if_cancelled()

        headers = {"User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"}
        with audible.Client(auth=self.auth, headers=headers, timeout=60) as client:
            try:
                self._raise_if_cancelled()

                if format_preference in ("aax", "aax-fallback"):
                    try:
                        audio_path = self.download_aax(client, asin, output_dir, filename, quality)
                        return {
                            "success": True,
                            "format": "aax",
                            "audio_file": str(audio_path),
                            "voucher_file": None,
                        }
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if aax_fallback or format_preference == "aax-fallback":
                            self.logger.info(
                                "AAX download failed; falling back to AAXC",
                                extra={"asin": asin, "error": str(exc)}
                            )
                        else:
                            raise

                audio_path, voucher_path = self.download_aaxc(
                    client,
                    asin,
                    output_dir,
                    filename,
                    quality,
                    save_voucher=True,
                )
                return {
                    "success": True,
                    "format": "aaxc",
                    "audio_file": str(audio_path),
                    "voucher_file": str(voucher_path) if voucher_path else None,
                }

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.error("Download failed", extra={"asin": asin, "error": str(exc)}, exc_info=True)
                return {
                    "success": False,
                    "error": str(exc),
                }
