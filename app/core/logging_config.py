# app/core/logging_config.py
from __future__ import annotations

import logging
from app.core.config import LOG_LEVEL

LOGGER_NAME = "academic-pdf-screener"

def _ensure_logger(name: str, level: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:  # avoid duplicate handlers on reload/tests
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level)
    return logger

logger: logging.Logger = _ensure_logger(LOGGER_NAME, LOG_LEVEL)
