"""Install (or rotate) the identity-signing secret inside the database. Run as the database owner.

    IDENTITY_SECRET=<at least 32 characters> python scripts/install_identity_secret.py

Generate a good one with:  python -c "import secrets; print(secrets.token_hex(32))"
The API process must be given the SAME value. Changing it on both sides is how you rotate the key.
"""
import os
import sys

import psycopg

ADMIN_DSN = os.environ.get("PG_ADMIN_DSN", "postgresql://postgres@localhost:5432/legalrag")


def install_secret(conn: psycopg.Connection, secret: str) -> None:
    if len(secret) < 32:
        raise ValueError("the identity secret must be at least 32 characters")
    conn.execute(
        "INSERT INTO app_secrets (name, value) VALUES ('identity_secret', %s)"
        " ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value", (secret,))
    conn.commit()


if __name__ == "__main__":
    secret = os.environ.get("IDENTITY_SECRET")
    if not secret:
        sys.exit("Set IDENTITY_SECRET first.")
    with psycopg.connect(ADMIN_DSN) as c:
        install_secret(c, secret)
    print("identity secret installed")