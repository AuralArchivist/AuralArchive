"""
Module Name: logger.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Configures Loguru-backed logging and provides standardized module logger
    helpers for the application. Centralizes logger naming and setup.

Location:
    /core/logging/logger.py
"""

import logging

from core.logging.config import _standardize_name, setup_loguru


_LOGGER_INITIALIZED = False

SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")


def success(self, message, *args, **kws):
    if self.isEnabledFor(SUCCESS_LEVEL):
        self._log(SUCCESS_LEVEL, message, args, **kws)


logging.Logger.success = success


def setup_logger(name: str = "AuralArchive", log_file: str = "auralarchive_web.log", level: int = logging.INFO):
    """Initialize logging via Loguru while preserving the existing API."""
    global _LOGGER_INITIALIZED

    standardized_name = _standardize_name(name)

    if not _LOGGER_INITIALIZED:
        setup_loguru(log_level=level, log_file=log_file, logger_name=standardized_name)
        _LOGGER_INITIALIZED = True

    logger = logging.getLogger(standardized_name)
    logger.setLevel(level)
    return logger


def get_module_logger(module_name: str):
    """Return a standard logger; Loguru intercepts its output."""
    if not _LOGGER_INITIALIZED:
        setup_logger()

    standardized = _standardize_name(module_name)
    logger = logging.getLogger(standardized)
    logger.success = logging.Logger.success.__get__(logger, logger.__class__)
    logger.debug("Initialized module logger", extra={"logger_name": standardized})
    return logger

