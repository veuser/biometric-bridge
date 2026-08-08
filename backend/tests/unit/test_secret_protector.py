"""SecretProtector seam.

Encryption is off today, so these tests mostly guard the *upgrade path*: the scheme
tag has to be present and honest, so a future DPAPI protector can tell an already-
protected row from a pass-through one and re-wrap in place.
"""

from __future__ import annotations

import pytest

from app.services.secret_protector import (
    MASK_VISIBLE_CHARS,
    NullSecretProtector,
    get_secret_protector,
    mask,
    split_scheme,
)

SECRET = "hrms-live-key-abc123456"


def test_round_trip() -> None:
    protector = NullSecretProtector()
    assert protector.reveal(protector.protect(SECRET)) == SECRET


def test_stored_blob_is_scheme_tagged() -> None:
    """Without the tag there is no way to identify unprotected rows later."""
    blob = NullSecretProtector().protect(SECRET)
    scheme, payload = split_scheme(blob)
    assert scheme == b"null"
    assert payload == SECRET.encode()


def test_null_protector_does_not_pretend_to_encrypt() -> None:
    """Documents the current posture: the secret is recoverable from the file."""
    assert SECRET.encode() in NullSecretProtector().protect(SECRET)


def test_untagged_legacy_blob_is_readable() -> None:
    scheme, payload = split_scheme(b"plain-old-value")
    assert scheme == b"null"
    assert payload == b"plain-old-value"


def test_reveal_rejects_a_foreign_scheme() -> None:
    with pytest.raises(ValueError, match="dpapi"):
        NullSecretProtector().reveal(b"dpapi:" + b"\x00\x01ciphertext")


def test_protect_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        NullSecretProtector().protect(b"bytes")  # type: ignore[arg-type]


def test_split_scheme_rejects_non_bytes() -> None:
    with pytest.raises(TypeError):
        split_scheme("a string")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("abcd", "•" * 8),
        ("abc", "•" * 8),
    ],
)
def test_mask_hides_short_values_entirely(value: str | None, expected: str) -> None:
    assert mask(value) == expected


def test_mask_reveals_only_the_tail() -> None:
    masked = mask(SECRET)
    assert masked.endswith(SECRET[-MASK_VISIBLE_CHARS:])
    assert SECRET[:-MASK_VISIBLE_CHARS] not in masked


def test_default_protector_is_the_null_one() -> None:
    assert get_secret_protector().scheme == "null"
