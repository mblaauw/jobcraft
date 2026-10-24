"""Local authentication: credentials."""
from __future__ import annotations

import hashlib
import secrets
from base64 import b64decode, b64encode


def hash_password(password: str) -> str:
    """Salted scrypt hash, stdlib only: ``scrypt$n$r$p$salt$digest`` (base64)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    return "scrypt$16384$8$1${}${}".format(
        b64encode(salt).decode("ascii"), b64encode(digest).decode("ascii")
    )



def verify_password(password: str, stored: str | None) -> bool:
    if not password or not stored:
        return False
    try:
        scheme, n_raw, r_raw, p_raw, salt_raw, digest_raw = str(stored).split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
    except (AttributeError, ValueError):
        return False
    # Bound the parameters we are willing to compute so a tampered row cannot stall us.
    if not (2**12 <= n <= 2**17) or not (1 <= r <= 16) or not (1 <= p <= 4):
        return False
    try:
        salt = b64decode(salt_raw, validate=True)
        expected = b64decode(digest_raw, validate=True)
    except (ValueError, TypeError):
        return False
    if not 8 <= len(salt) <= 64 or len(expected) < 16:
        return False
    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=128 * 1024 * 1024,
        )
    except (ValueError, OverflowError):
        return False
    return secrets.compare_digest(candidate, expected)



_dummy_hash: str | None = None



def _verify_dummy_password(password: str) -> None:
    """Spend comparable time when the username is unknown (no user enumeration)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(24))
    verify_password(password, _dummy_hash)

