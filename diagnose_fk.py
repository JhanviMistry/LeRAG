"""Read-only diagnostic: what does the database say for three kinds of bad insert? Nothing is saved."""
import sys
sys.path.insert(0, "scripts")

import psycopg
import seed as S
from app.db import INGEST_DSN

NO_SUCH_UUID = "11111111-1111-1111-1111-111111111111"


def attempt(label, matter_id, uploaded_by):
    with psycopg.connect(INGEST_DSN) as conn:
        try:
            conn.execute("SELECT set_config('app.user_id', %s, true)", (S.UID["alice"],))
            conn.execute(
                "INSERT INTO documents (matter_id, title, doc_type, confidentiality, sha256, uploaded_by)"
                " VALUES (%s, 'diagnostic', 'x', 'standard', 'x', %s)", (matter_id, uploaded_by))
            print(f"{label}: accepted")
        except psycopg.Error as e:
            print(f"{label}: {type(e).__name__} | sqlstate {e.sqlstate} | {e.diag.message_primary}")
        finally:
            conn.rollback()


attempt("1. real matter, real uploader     ", S.MID["A"], S.UID["alice"])
attempt("2. NONEXISTENT matter, real uploader", NO_SUCH_UUID, S.UID["alice"])
attempt("3. real matter, NONEXISTENT uploader", S.MID["A"], NO_SUCH_UUID)