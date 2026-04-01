"""Password hashing utilities using stdlib hashlib.scrypt.

Format: ``scrypt:<salt_hex>:<hash_hex>``

No external dependencies — uses only the Python standard library.
"""

from __future__ import annotations

import hashlib
import hmac
import os

# scrypt parameters (OWASP recommended minimums)
_N = 16384  # CPU/memory cost
_R = 8      # block size
_P = 1      # parallelization
_DKLEN = 32 # derived key length


def hash_password(password: str) -> str:
    """Hash *password* and return a storable string ``scrypt:<salt>:<hash>``."""
    salt = os.urandom(16)
    h = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN,
    )
    return f"scrypt:{salt.hex()}:{h.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify *password* against a stored hash. Timing-safe."""
    parts = password_hash.split(":")
    if len(parts) != 3 or parts[0] != "scrypt":
        return False
    try:
        salt = bytes.fromhex(parts[1])
        expected = bytes.fromhex(parts[2])
    except ValueError:
        return False
    h = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN,
    )
    return hmac.compare_digest(h, expected)
