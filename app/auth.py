"""Login and session tokens.

authenticate()          : email + password -> user id (or None). Audited either way.
issue_session_token()   : a signed JWT for a logged-in user, valid for one hour.
verify_session_token()  : token -> user id (or None). Fails closed.

A token proves WHO you are. It never carries roles or matters: the database decides what you may see.
In production, single sign-on (Entra ID / Cognito) replaces authenticate(); the rest stays the same.
"""
import os
import time
from uuid import UUID

import jwt  # PyJWT
import psycopg
from psycopg.types.json import Jsonb

from app.passwords import hash_password, verify_password

AUTH_DSN = os.environ.get("AUTH_DSN", "postgresql://auth_svc:auth_svc_dev@localhost:5432/legalrag")
SESSION_TTL_SECONDS = 3600
ISSUER = "legal-rag-demo"

# Checked against when the email is unknown, so unknown emails cost the same time as wrong passwords.
_DUMMY_HASH = hash_password("a-password-nobody-will-ever-use")


def _session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("SESSION_SECRET must be set to at least 32 characters")
    return secret


def authenticate(email: str, password: str) -> str | None:
    """Returns the user id on success, None on any failure. Never reveals which part was wrong."""
    email = (email or "").strip().lower()
    with psycopg.connect(AUTH_DSN) as conn:
        row = conn.execute(
            "SELECT u.id, c.password_hash FROM users u JOIN credentials c ON c.user_id = u.id"
            " WHERE lower(u.email) = %s", (email,)).fetchone()
        stored = row[1] if row else _DUMMY_HASH          # always do one full password check
        password_ok = verify_password(password or "", stored)
        success = row is not None and password_ok
        user_id = str(row[0]) if row else None
        conn.execute(
            "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, %s, %s)",
            (user_id, "login_ok" if success else "login_failed", Jsonb({"email": email[:200]})))
    return user_id if success else None


def issue_session_token(user_id) -> str:
    now = int(time.time())
    claims = {"sub": str(UUID(str(user_id))), "iss": ISSUER, "iat": now, "exp": now + SESSION_TTL_SECONDS}
    return jwt.encode(claims, _session_secret(), algorithm="HS256")


def verify_session_token(token: str) -> str | None:
    secret = _session_secret()    # outside the try: a missing secret is a deployment bug and must be loud
    try:
        claims = jwt.decode(
            token, secret,
            algorithms=["HS256"],                                   # pinned: never trust the token's own header
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]})
        return str(UUID(claims["sub"]))
    except Exception:             # any problem at all with the token means "not logged in"
        return None 