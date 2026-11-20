"""Give every seeded demo user a password. DEMO ONLY: never share one password across real users.

    DEMO_PASSWORD='at-least-12-characters' python scripts/set_demo_passwords.py

Each user still gets their own random salt. Re-run after any reseed: seeding wipes users,
and their credentials go with them."""
import os
import pathlib
import sys

import psycopg

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.passwords import hash_password  # noqa: E402

ADMIN_DSN = os.environ.get("PG_ADMIN_DSN", "postgresql://postgres@localhost:5432/legalrag")

if __name__ == "__main__":
    password = os.environ.get("DEMO_PASSWORD")
    if not password:
        sys.exit("Set DEMO_PASSWORD first (at least 12 characters).")
    with psycopg.connect(ADMIN_DSN) as conn:
        users = conn.execute("SELECT id, email FROM users ORDER BY email").fetchall()
        for user_id, _email in users:
            conn.execute(
                "INSERT INTO credentials (user_id, password_hash) VALUES (%s, %s)"
                " ON CONFLICT (user_id) DO UPDATE SET password_hash = EXCLUDED.password_hash",
                (user_id, hash_password(password)))
    print(f"passwords set for {len(users)} users")