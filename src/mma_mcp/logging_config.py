"""Structured logging with per-request tracing for mma-mcp.

Each MCP tool invocation gets a unique ``request_id`` injected via
``contextvars`` so all log lines within a request can be correlated.
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Any

# Per-request ID (set in _safe_wrapper before calling the tool function)
request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="",
)


class RequestIdFilter(logging.Filter):
    """Inject ``request_id`` into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()  # type: ignore[attr-defined]
        return True


def setup_logging(level: int = logging.INFO, json_format: bool = False) -> None:
    """Configure the root logger with request-id support.

    Args:
        level: Log level (default INFO).
        json_format: If True, emit JSON lines; otherwise use a human-readable
                     format with request_id when present.
    """
    import sys

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicate output
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())

    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s"
            "%(request_id_bracket)s: %(message)s"
        ))
        # Add a filter that creates the bracket-wrapped request_id
        handler.addFilter(_BracketFilter())

    root.addHandler(handler)


class _BracketFilter(logging.Filter):
    """Add ``request_id_bracket`` field: `` [req:abc123]`` or empty."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = getattr(record, "request_id", "")
        record.request_id_bracket = f" [req:{rid[:8]}]" if rid else ""  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        obj: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", "")
        if rid:
            obj["request_id"] = rid
        if record.exc_info and record.exc_info[1]:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def new_request_id() -> str:
    """Generate a short unique request ID."""
    return uuid.uuid4().hex[:12]
