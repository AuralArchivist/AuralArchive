"""
Module Name: progress_tracker.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Emits download progress to the UI over SocketIO.

Location:
    /services/acquisition/progress_tracker.py
"""

from typing import Optional, Dict, Any

from core.logging import get_module_logger
from core.convert import to_int


class ProgressTracker:
    """Tracks and reports download progress."""

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.DownloadManagement.Progress")
    
    def emit_progress(
        self,
        download_id: int,
        progress_percentage: Optional[float] = None,
        speed: Optional[int] = None,
        eta: Optional[int] = None,
        *,
        current_speed: Optional[int] = None,
        eta_seconds: Optional[int] = None,
        message: Optional[str] = None,
        downloaded_bytes: Optional[int] = None,
        total_bytes: Optional[int] = None,
        **extra: Any,
    ):
        """Emit progress update via SocketIO."""
        try:
            event_data = self._build_payload(
                download_id=download_id,
                progress_percentage=progress_percentage,
                speed=speed,
                eta=eta,
                current_speed=current_speed,
                eta_seconds=eta_seconds,
                message=message,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                extra=extra,
            )
            self._resolve_socketio().emit('download:progress', event_data)
        except Exception:
            self.logger.exception("Error emitting progress event")

    def _resolve_socketio(self):
        """Import SocketIO lazily to avoid circular imports."""
        from app import socketio
        return socketio

    def _build_payload(
        self,
        download_id: int,
        progress_percentage: Optional[float],
        speed: Optional[int],
        eta: Optional[int],
        current_speed: Optional[int],
        eta_seconds: Optional[int],
        message: Optional[str],
        downloaded_bytes: Optional[int],
        total_bytes: Optional[int],
        extra: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assemble a sanitized payload for the SocketIO event."""
        event_data: Dict[str, Any] = {'download_id': download_id}

        if progress_percentage is not None:
            try:
                event_data['progress'] = round(float(progress_percentage), 2)
            except (TypeError, ValueError):
                self.logger.debug("Invalid progress percentage '%s'", progress_percentage)

        speed_value = current_speed if current_speed is not None else speed
        speed_int = to_int(speed_value, None)
        if speed_int is not None:
            event_data['speed_bytes'] = max(0, speed_int)
            event_data['speed'] = self._format_speed(event_data['speed_bytes'])

        eta_value = eta_seconds if eta_seconds is not None else eta
        eta_int = to_int(eta_value, None)
        if eta_int is not None:
            event_data['eta_seconds'] = eta_int
            event_data['eta'] = self._format_eta(eta_int)

        if message:
            event_data['message'] = message

        downloaded_int = to_int(downloaded_bytes, None)
        if downloaded_int is not None:
            event_data['downloaded_bytes'] = max(0, downloaded_int)

        total_int = to_int(total_bytes, None)
        if total_int is not None:
            event_data['total_bytes'] = max(0, total_int)

        if extra:
            sanitized = {
                key: value for key, value in extra.items()
                if value is not None and key not in event_data
            }
            if sanitized:
                self.logger.debug("Including extra progress fields: %s", list(sanitized.keys()))
                event_data.update(sanitized)

        return event_data

    def _format_speed(self, bytes_per_sec: int) -> str:
        """Format speed for display.

        Returns:
            Formatted string (e.g., "1.5 MB/s")
        """
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        elif bytes_per_sec < 1024 * 1024 * 1024:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024 * 1024):.1f} GB/s"
    
    def _format_eta(self, seconds: int) -> str:
        """Format ETA for display.

        Returns:
            Formatted string (e.g., "5m 30s")
        """
        if seconds < 0:
            return "Unknown"
        elif seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
