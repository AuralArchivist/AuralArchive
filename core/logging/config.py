"""
Module Name: config.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Sets up Loguru sinks, logging interception, and naming conventions for
    application loggers. Bridges standard logging to Loguru handlers.

Location:
    /core/logging/config.py
"""

import logging
import sys
from pathlib import Path
from typing import Optional, Union

from loguru import logger

_session_log_path: Optional[Path] = None
_session_start_position: int = 0
_app_logger_name: str = "AuralArchive"


def get_session_log_info() -> tuple:
    """Return (log_file_path_str_or_None, byte_offset) for the current session."""
    return str(_session_log_path) if _session_log_path else None, _session_start_position


def _standardize_name(raw_name: Union[str, int]) -> str:
    """Normalize logger names to dotted, title-cased segments (Service.Audible.CatalogCover)."""
    if not raw_name:
        return "AuralArchive"
    if isinstance(raw_name, int):
        return str(raw_name)

    normalized = str(raw_name).replace("\\", ".").replace("/", ".").replace("_", ".").replace(" ", ".")
    parts = [segment for segment in normalized.split(".") if segment]
    return ".".join(part[:1].upper() + part[1:] for part in parts)


class InterceptHandler(logging.Handler):
    """Route standard logging records into Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        if record.name in {"httpx", "httpcore"} and record.levelno == logging.INFO:
            level = "DEBUG"

        frame = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_name == "emit":
            frame = frame.f_back
            depth += 1

        logger_name = _standardize_name(record.name)

        logger.bind(logger_name=logger_name).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _coerce_level(level: Union[str, int]) -> Union[str, int]:
    if isinstance(level, str):
        return level.upper()
    if isinstance(level, int):
        return level
    try:
        return int(level)
    except (TypeError, ValueError):
        return "INFO"


CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
    "| <level>{level: <8}</level> "
    "| <cyan>{extra[logger_name]}</cyan> - "
    "<level>{message}</level>"
)
FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} - {level} - {extra[logger_name]} - {message}"


def _add_sinks(level: Union[str, int], log_path: Path) -> None:
    """Attach the color console sink and the rotating file sink at ``level``."""
    logger.add(
        sys.stdout,
        level=level,
        format=CONSOLE_FORMAT,
        enqueue=False,
        backtrace=False,
        diagnose=False,
        colorize=True,
    )

    logger.add(
        log_path,
        level=level,
        format=FILE_FORMAT,
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        enqueue=False,
        backtrace=False,
        diagnose=False,
    )


def setup_loguru(log_level: Union[str, int] = "INFO", log_file: str = "auralarchive_web.log", logger_name: str = "AuralArchive"):
    """Configure Loguru sinks and hook standard logging into Loguru."""
    global _session_log_path, _session_start_position, _app_logger_name

    try:
        logger.level("SUCCESS")
    except ValueError:
        logger.level("SUCCESS", no=25, color="<green>")

    level = _coerce_level(log_level)
    from core.paths import get_path_resolver
    log_dir = Path(get_path_resolver().get_logs_dir())
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_file

    _session_log_path = log_path
    _session_start_position = log_path.stat().st_size if log_path.exists() else 0

    logger.remove()

    _add_sinks(level, log_path)

    logging.basicConfig(handlers=[InterceptHandler()], level=logging.NOTSET, force=True)
    logging.getLogger().setLevel(logging.NOTSET)

    for noisy in ("audible", "audible.auth"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    for http_logger in ("httpx", "httpcore"):
        logging.getLogger(http_logger).setLevel(logging.DEBUG)

    standardized_root = _standardize_name(logger_name)
    _app_logger_name = standardized_root

    logger.configure(extra={"logger_name": standardized_root})

    return logger


def set_log_level(level: Union[str, int]) -> Union[str, int]:
    """Re-apply the console/file sink level at runtime.

    Loguru sinks cannot have their level changed in place, so the sinks are
    removed and re-added at the new level. The session snapshot used by the
    log window (``_session_start_position``) is deliberately left untouched so
    the live log view is not disrupted or cleared when the level changes.
    Returns the coerced level that was applied.
    """
    coerced = _coerce_level(level)

    if _session_log_path is None:
        setup_loguru(log_level=coerced)
        return coerced

    logger.remove()
    _add_sinks(coerced, _session_log_path)

    numeric = coerced if isinstance(coerced, int) else logging.getLevelName(coerced)
    if isinstance(numeric, int):
        logging.getLogger(_app_logger_name).setLevel(numeric)

    return coerced
