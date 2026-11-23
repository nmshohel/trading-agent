"""Structured JSON logger with automatic secret redaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "worker_auth_token",
    "openai_api_key",
}


def redact_mapping(data: Any) -> Any:
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for k, v in data.items():
            if any(s in str(k).lower() for s in SENSITIVE_KEYS):
                result[k] = "[REDACTED]"
            else:
                result[k] = redact_mapping(v)
        return result
    if isinstance(data, list):
        return [redact_mapping(item) for item in data]
    return data


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": "trading-worker",
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_data", None)
        if isinstance(extra, dict):
            payload["data"] = redact_mapping(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str = "trading_worker") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
