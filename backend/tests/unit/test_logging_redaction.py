"""The redaction filter is load-bearing.

With encryption at rest turned off (PLAN.md §8), keeping the HRMS API key out of the
log file is one of the few remaining controls on it. These tests cover every route a
secret can take into a log record: the message, %-args, dict args, and an exception's
text.
"""

from __future__ import annotations

import logging

import pytest

from app.core.logging import (
    REDACTED,
    JsonFormatter,
    SecretRedactingFilter,
    clear_secrets,
    redact,
    register_secret,
    unregister_secret,
)

SECRET = "hrms-live-key-abc123456"


@pytest.fixture(autouse=True)
def _clean() -> None:
    clear_secrets()


def _record(msg: str, args: object = None, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=exc_info,  # type: ignore[arg-type]
    )


def test_secret_is_removed_from_message() -> None:
    register_secret(SECRET)
    record = _record(f"calling HRMS with key={SECRET}")
    SecretRedactingFilter().filter(record)
    assert SECRET not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_secret_is_removed_from_positional_args() -> None:
    register_secret(SECRET)
    record = _record("auth header was %s", (SECRET,))
    SecretRedactingFilter().filter(record)
    assert SECRET not in record.getMessage()


def test_secret_is_removed_from_dict_args() -> None:
    register_secret(SECRET)
    # LogRecord takes mapping-style args wrapped in a 1-tuple; it unwraps them
    # itself. Passing the bare dict is what logging's own docs warn against.
    record = _record("auth header was %(key)s", ({"key": SECRET},))
    SecretRedactingFilter().filter(record)
    assert isinstance(record.args, dict)
    assert SECRET not in record.getMessage()


def test_secret_is_removed_from_exception_text() -> None:
    """An exception message is the sneakiest path a credential takes into a log."""
    register_secret(SECRET)
    try:
        raise ValueError(f"bad key {SECRET}")
    except ValueError:
        import sys

        record = _record("request failed", exc_info=sys.exc_info())
    SecretRedactingFilter().filter(record)
    assert record.exc_text is not None
    assert SECRET not in record.exc_text
    assert REDACTED in record.exc_text


def test_json_formatter_output_contains_no_secret() -> None:
    register_secret(SECRET)
    record = _record(f"key={SECRET}")
    SecretRedactingFilter().filter(record)
    assert SECRET not in JsonFormatter().format(record)


def test_short_values_are_not_registered() -> None:
    """Redacting a 3-character value would mangle unrelated log lines."""
    register_secret("abc")
    assert redact("abcdef ghi abc") == "abcdef ghi abc"


def test_unregister_stops_redaction() -> None:
    register_secret(SECRET)
    assert REDACTED in redact(SECRET)
    unregister_secret(SECRET)
    assert redact(SECRET) == SECRET


def test_none_and_empty_are_ignored() -> None:
    register_secret(None)
    register_secret("")
    assert redact("nothing to do here") == "nothing to do here"


def test_filter_is_a_noop_when_no_secrets_registered() -> None:
    record = _record("plain message")
    assert SecretRedactingFilter().filter(record) is True
    assert record.getMessage() == "plain message"


class TestExtraKeySafety:
    """A log statement must never be able to turn a working request into a 500."""

    def test_reserved_extra_keys_are_renamed_not_fatal(self) -> None:
        from app.core.logging import sanitize_extra

        cleaned = sanitize_extra({"name": "Entry", "device_id": 1, "module": "x"})
        assert cleaned == {"ctx_name": "Entry", "device_id": 1, "ctx_module": "x"}

    def test_logging_with_a_reserved_key_does_not_raise(self) -> None:
        from app.core.logging import get_logger

        # Plain logging.Logger raises KeyError here; the adapter must not.
        get_logger("test").info("device saved", extra={"name": "Entry", "filename": "x"})

    def test_non_reserved_keys_pass_through_untouched(self) -> None:
        from app.core.logging import sanitize_extra

        assert sanitize_extra({"device_id": 7, "buffer_records": 900}) == {
            "device_id": 7,
            "buffer_records": 900,
        }
