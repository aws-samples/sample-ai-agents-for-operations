"""Structured JSON logger for MIO Agent — no PII logging."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Any


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as JSON lines, stripping PII fields."""

    # Fields that must never appear in logs
    _PII_FIELDS = frozenset({"email", "phone", "name", "address", "ssn", "password", "token"})

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add structured context fields from extra dict
        if hasattr(record, "account_id"):
            log_entry["account_id"] = record.account_id  # type: ignore[attr-defined]
        if hasattr(record, "assessment_id"):
            log_entry["assessment_id"] = record.assessment_id  # type: ignore[attr-defined]
        if hasattr(record, "trigger_type"):
            log_entry["trigger_type"] = record.trigger_type  # type: ignore[attr-defined]
        if hasattr(record, "agent"):
            log_entry["agent"] = record.agent  # type: ignore[attr-defined]

        # Scrub any PII that slipped in
        for pii_field in self._PII_FIELDS:
            log_entry.pop(pii_field, None)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Get a structured JSON logger.

    Args:
        name: Logger name, typically __name__ of the calling module.
        level: Logging level (default INFO).

    Returns:
        Configured logger with JSON formatter.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)

    logger.setLevel(level)
    logger.propagate = False
    return logger
