"""The single doorway to the database.

Every read goes through user_session(). It does three things:
  1. validates the user id is a real UUID (rejects junk early),
  2. opens a transaction,
  3. tells Postgres who is asking, for THIS transaction only.
Row-Level Security then filters every query automatically.
"""
import os
from contextlib import contextmanager
from uuid import UUID

import psycopg

APP_DSN = os.environ.get("APP_DSN", "postgresql://app_user:app_user_dev@localhost:5432/legalrag")
INGEST_DSN = os.environ.get("INGEST_DSN", "postgresql://ingest_svc:ingest_svc_dev@localhost:5432/legalrag")


@contextmanager
def user_session(user_id, dsn: str = APP_DSN):
    uid = str(UUID(str(user_id)))  # raises ValueError if it isn't a valid UUID
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            # The third argument, true, means "local": the setting disappears when the
            # transaction ends. This matters once we use a connection pool, because it
            # stops one user's identity leaking into the next user's request.
            conn.execute("SELECT set_config('app.user_id', %s, true)", (uid,))
            yield conn