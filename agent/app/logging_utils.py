"""Process-wide logging setup used from ``main`` on import."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Idempotent-ish: ``basicConfig`` only if the root logger has no handlers yet."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )
    else:
        root.setLevel(numeric_level)


def get_logger(name: str) -> logging.Logger:
    """Namespaced logger (e.g. ``agent.graph``) for grep-friendly log lines."""
    return logging.getLogger(name)
