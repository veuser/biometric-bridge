"""Structured logging with credential redaction.

Two things matter here:

1. **Redaction.** The HRMS API key is stored unencrypted (PLAN.md §8), so keeping it
   out of the log file is one of the few controls left on it. Every registered secret
   is scrubbed from the message, the format arguments *and* the exception text before
   a record is emitted. Registration happens whenever a key is loaded or saved.

2. **Structure.** The file handler writes one JSON object per line so support can grep
   and machine-read it; the console handler stays human-readable for development.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from typing import Any

REDACTED = "***REDACTED***"

#: Shortest secret worth redacting. Below this, substring matching produces
#: false positives that mangle ordinary log lines.
_MIN_SECRET_LENGTH = 6

_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Add a value to be scrubbed from all future log output.

    Safe to call repeatedly. Values shorter than :data:`_MIN_SECRET_LENGTH` are
    ignored rather than silently corrupting unrelated log lines.
    """
    if value and len(value) >= _MIN_SECRET_LENGTH:
        _secrets.add(value)


def unregister_secret(value: str | None) -> None:
    """Stop scrubbing a value — used when a credential is rotated or removed."""
    if value:
        _secrets.discard(value)


def clear_secrets() -> None:
    """Drop every registered secret. Test-support only."""
    _secrets.clear()


def redact(text: str) -> str:
    for secret in _secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


class SecretRedactingFilter(logging.Filter):
    """Scrubs registered secrets from a record before any handler formats it.

    Applied to the *handlers* rather than a logger, so records propagating up from
    third-party libraries (httpx logging a URL with a key in the query string, for
    instance) are covered too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not _secrets:
            return True

        if isinstance(record.msg, str):
            record.msg = redact(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: redact(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    redact(arg) if isinstance(arg, str) else arg for arg in record.args
                )

        # exc_info is rendered later by the formatter, so pre-render and scrub it
        # here; otherwise a credential inside an exception message survives.
        if record.exc_info and record.exc_info[1] is not None:
            rendered = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact(rendered)
            record.exc_info = None

        return True


_RESERVED_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "taskName", "thread", "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with any ``extra=`` fields merged in."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    *,
    level: str = "INFO",
    log_file: Any = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
    to_stdout: bool = True,
) -> None:
    """Install handlers on the root logger. Idempotent — replaces existing handlers."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    redaction_filter = SecretRedactingFilter()

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(redaction_filter)
        root.addHandler(file_handler)

    if to_stdout:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)-28s  %(message)s")
        )
        console.addFilter(redaction_filter)
        root.addHandler(console)

    # Access logs duplicate what the app already records and are noisy at INFO.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
