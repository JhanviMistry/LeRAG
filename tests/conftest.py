import os
import pathlib
import sys
from contextlib import contextmanager

import psycopg
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import seed as seedmod  # noqa: E402

HOST = os.environ.get("PGHOST", "localhost")
PORT = os.environ.get("PGPORT", "5432")
SUPER = os.environ.get("PG_SUPER_USER", "postgres")
DB = "legalrag_test"

ADMIN_DSN = f"postgresql://{SUPER}@{HOST}:{PORT}/{DB}"
APP_DSN = f"postgresql://app_user:app_user_dev@{HOST}:{PORT}/{DB}"
INGEST_DSN = f"postgresql://ingest_svc:ingest_svc_dev@{HOST}:{PORT}/{DB}"


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(f"postgresql://{SUPER}@{HOST}:{PORT}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for f in sorted((ROOT / "db" / "init").glob("*.sql")):
            c.execute(f.read_text())
    with psycopg.connect(ADMIN_DSN) as c:
        seedmod.seed(c)
    yield


@contextmanager
def session(dsn, user=None, raw_user_id=None):
    """A transaction as the app would run it: identity set per-transaction, always rolled back."""
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        ident = raw_user_id if raw_user_id is not None else (seedmod.UID[user] if user else None)
        if ident is not None:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (ident,))
        try:
            yield cur
        finally:
            conn.rollback()


@pytest.fixture
def as_user():
    def _as(user):
        return session(APP_DSN, user)
    return _as
