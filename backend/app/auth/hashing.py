"""Phase 23 password hashing — stdlib only, no new dependency.

PBKDF2-HMAC-SHA256 with 600 000 iterations (OWASP 2023 recommendation
for PBKDF2-SHA256). Salt is 16 random bytes from `secrets.token_bytes`;
hash output is 32 bytes (SHA-256 native size).

Stored format mirrors the passlib `pbkdf2_sha256` convention so the
string is self-describing and parameters can rotate per-row:

    pbkdf2_sha256$<iterations>$<base64-salt>$<base64-hash>

Both `<base64-salt>` and `<base64-hash>` use URL-safe base64 with no
padding (`base64.urlsafe_b64encode(...).rstrip(b"=")`). Verification
re-computes with the stored iterations + salt and compares in constant
time via `hmac.compare_digest`.

This is dev-appropriate local auth. Production deployments should use
argon2 / scrypt / bcrypt via a dedicated library — documented as such
in backend/README.md.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_BYTES = 32  # SHA-256 native output size


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(encoded: str) -> bytes:
    # Re-pad to a multiple of 4 before decoding.
    pad = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + pad)


def hash_password(plain: str, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """Return a self-describing PBKDF2-SHA256 hash string.

    Raises ValueError on empty input — an empty password should never be
    quietly accepted at the persistence layer.
    """
    if not plain:
        raise ValueError("password must be non-empty")
    if iterations < 100_000:
        # Defense-in-depth: a future caller can't quietly weaken the
        # work factor below an OWASP-credible floor.
        raise ValueError(f"iterations {iterations} below minimum 100000")
    salt = secrets.token_bytes(_SALT_BYTES)
    raw_hash = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt, iterations, dklen=_HASH_BYTES
    )
    return f"{_ALGORITHM}${iterations}${_b64encode(salt)}${_b64encode(raw_hash)}"


def verify_password(plain: str, hashed: str | None) -> bool:
    """Constant-time verify. Returns False on any malformed input rather
    than raising — callers (e.g. login endpoint) should not distinguish
    "unknown user" from "bad hash format" in their error response."""
    if not plain or not hashed:
        return False
    try:
        algorithm, iterations_str, salt_b64, hash_b64 = hashed.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        iterations = int(iterations_str)
    except ValueError:
        return False
    if iterations < 1:
        return False
    try:
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(candidate, expected)
