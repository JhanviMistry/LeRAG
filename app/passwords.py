"""Password hashing with scrypt (memory-hard, in Python's standard library).

Stored format:  scrypt$N$r$p$<salt hex>$<hash hex>
The cost parameters travel with each hash, so they can be raised later without breaking old hashes."""
import hashlib
import hmac
import os

N, R, P, DKLEN = 2**14, 8, 1, 32
MIN_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"password must be at least {MIN_LENGTH} characters")
    salt = os.urandom(16)   # a fresh random salt, so equal passwords still get different hashes
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """True only for a well-formed hash that matches. Anything malformed is simply False, never an error."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        if len(expected) < 16:          # an empty or tiny stored hash must never be matchable
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)   # constant-time comparison