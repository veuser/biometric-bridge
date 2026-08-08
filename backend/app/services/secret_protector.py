"""Storage protection for secrets (HRMS API key, device comm keys).

**Encryption at rest is currently OFF** by customer decision (PLAN.md §8). The
shipped implementation is :class:`NullSecretProtector`, which stores the value as
plain UTF-8 bytes. Anyone with read access to the SQLite file or a backup of it can
recover the HRMS credential; directory ACLs are the only control.

This interface exists so that turning protection on later is a configuration change
rather than a rewrite:

* Every call site already goes through ``protect()`` / ``reveal()``.
* Columns are ``LargeBinary``, so ciphertext needs no schema migration.
* Each stored blob carries a scheme tag, so a future ``DpapiSecretProtector`` can
  read existing pass-through rows and re-wrap them in place with no downtime and no
  ambiguity about which rows are already protected.

The tag is what makes the upgrade safe. Without it there is no way to tell a
plaintext blob from a ciphertext blob after the fact.
"""

from __future__ import annotations

from typing import Protocol

_SEPARATOR = b":"
_SCHEME_NULL = b"null"

#: Number of trailing characters kept for the masked display value shown in the UI.
MASK_VISIBLE_CHARS = 4


class SecretProtector(Protocol):
    """Wraps and unwraps a secret for storage."""

    scheme: str

    def protect(self, value: str) -> bytes: ...

    def reveal(self, blob: bytes) -> str: ...


class NullSecretProtector:
    """Pass-through. Stores the secret as tagged plaintext.

    Named for what it does. A no-op protector that pretended to be encryption would
    be worse than none at all.
    """

    scheme = "null"

    def protect(self, value: str) -> bytes:
        if not isinstance(value, str):
            raise TypeError("secret must be a str")
        return _SCHEME_NULL + _SEPARATOR + value.encode("utf-8")

    def reveal(self, blob: bytes) -> str:
        scheme, payload = split_scheme(blob)
        if scheme != _SCHEME_NULL:
            raise ValueError(
                f"cannot reveal a {scheme.decode()!r} secret with the null protector"
            )
        return payload.decode("utf-8")


def split_scheme(blob: bytes) -> tuple[bytes, bytes]:
    """Split a stored blob into ``(scheme, payload)``.

    Blobs written before tagging existed have no separator; they are treated as
    untagged plaintext so an older database still opens.
    """
    if not isinstance(blob, bytes | bytearray | memoryview):
        raise TypeError("stored secret must be bytes")
    blob = bytes(blob)
    scheme, separator, payload = blob.partition(_SEPARATOR)
    if not separator:
        return _SCHEME_NULL, blob
    return scheme, payload


def mask(value: str | None) -> str:
    """Render a secret for display: only the last few characters survive.

    Short secrets are masked entirely rather than leaking a meaningful fraction of
    themselves.
    """
    if not value:
        return ""
    if len(value) <= MASK_VISIBLE_CHARS:
        return "•" * 8
    return "•" * 8 + value[-MASK_VISIBLE_CHARS:]


_default_protector: SecretProtector = NullSecretProtector()


def get_secret_protector() -> SecretProtector:
    """The protector the application uses.

    A single accessor so that enabling DPAPI later is one edit here, not a search
    across the codebase.
    """
    return _default_protector
