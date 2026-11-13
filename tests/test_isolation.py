"""Matter isolation, ethical walls, privilege, and audit-log guarantees.

These tests run as the *application* DB role, with the same per-transaction
identity mechanism the API will use. They are the proof behind the claim
"a bug in the app cannot leak across matters".
"""
import psycopg
import pytest

import seed as S
from conftest import ADMIN_DSN, APP_DSN, INGEST_DSN, session


def chunk_docs(cur):
    cur.execute("SELECT d.title FROM chunks c JOIN documents d ON d.id = c.document_id")
    return cur.fetchall()


def matters_seen(cur):
    cur.execute("SELECT DISTINCT matter_id::text FROM chunks")
    return {r[0] for r in cur.fetchall()}


# ---------- Role hygiene ----------
def test_app_roles_cannot_bypass_rls():
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN ('app_user','ingest_svc')"
        ).fetchall()
    assert len(rows) == 2
    assert all(not sup and not byp for _, sup, byp in rows)


def test_rls_forced_on_all_content_tables():
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('matters','documents','chunks','audit_log')"
        ).fetchall()
    assert len(rows) == 4
    assert all(enabled and forced for _, enabled, forced in rows)


# ---------- Fail closed ----------
@pytest.mark.parametrize("raw", [None, "", "not-a-uuid", "' OR 1=1 --", "00000000-0000-0000-0000-000000000000"])
def test_missing_or_garbage_identity_sees_nothing(raw):
    with session(APP_DSN, raw_user_id=raw) as cur:
        assert chunk_docs(cur) == []
        cur.execute("SELECT count(*) FROM documents"); assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM matters");   assert cur.fetchone()[0] == 0


# ---------- Matter isolation ----------
def test_associate_sees_only_their_matter(as_user):
    with as_user("alice") as cur:
        assert matters_seen(cur) == {S.MID["A"]}
    with as_user("ben") as cur:
        assert matters_seen(cur) == {S.MID["B"]}


def test_partner_on_both_matters_sees_both(as_user):
    with as_user("dan") as cur:
        assert matters_seen(cur) == {S.MID["A"], S.MID["B"]}


def test_select_star_without_where_cannot_leak(as_user):
    """The 'forgot the WHERE clause' bug: the database still holds the line."""
    with as_user("alice") as cur:
        cur.execute("SELECT matter_id::text FROM chunks")
        assert {r[0] for r in cur.fetchall()} == {S.MID["A"]}


def test_exact_match_vector_search_cannot_reach_other_matter(as_user):
    """Query with the exact embedding of a Matter B chunk. Alice must not get it back."""
    target = S.DOCS["B-will"][4][0]
    emb = "[" + ",".join(f"{x:.6f}" for x in S.fake_embedding(target)) + "]"
    with as_user("alice") as cur:
        cur.execute("SELECT content FROM chunks ORDER BY embedding <=> %s::vector LIMIT 20", (emb,))
        returned = [r[0] for r in cur.fetchall()]
    assert returned, "Alice should still get her own matter's chunks"
    assert target not in returned


def test_full_text_search_cannot_reach_other_matter(as_user):
    with as_user("alice") as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE content_tsv @@ plainto_tsquery('english', 'residuary estate')")
        assert cur.fetchone()[0] == 0
    with as_user("ben") as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE content_tsv @@ plainto_tsquery('english', 'residuary estate')")
        assert cur.fetchone()[0] == 1


# ---------- Privilege ----------
def test_associate_sees_privileged_on_own_matter(as_user):
    with as_user("alice") as cur:
        titles = {t for (t,) in chunk_docs(cur)}
    assert S.DOCS["A-memo"][1] in titles


def test_paralegal_cannot_see_privileged(as_user):
    with as_user("chloe") as cur:
        titles = {t for (t,) in chunk_docs(cur)}
        cur.execute("SELECT count(*) FROM documents WHERE confidentiality = 'privileged'")
        assert cur.fetchone()[0] == 0
    assert S.DOCS["A-contract"][1] in titles
    assert S.DOCS["A-memo"][1] not in titles


# ---------- Ethical wall ----------
def test_ethical_wall_overrides_membership(as_user):
    with as_user("eve") as cur:
        assert chunk_docs(cur) == []
        cur.execute("SELECT count(*) FROM matters"); assert cur.fetchone()[0] == 0


# ---------- Non-lawyer roles ----------
@pytest.mark.parametrize("user", ["frank", "grace"])
def test_admin_and_auditor_cannot_read_content(as_user, user):
    with as_user(user) as cur:
        assert chunk_docs(cur) == []
        cur.execute("SELECT count(*) FROM matters"); assert cur.fetchone()[0] == 0


# ---------- Least privilege ----------
@pytest.mark.parametrize("table", ["users", "matter_members", "ethical_walls"])
def test_app_user_cannot_enumerate_access_tables(as_user, table):
    with as_user("alice") as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(f"SELECT * FROM {table}")


def test_app_user_cannot_write_content(as_user):
    with as_user("dan") as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM chunks")


def test_ingest_service_is_write_only():
    with session(INGEST_DSN, "dan") as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("SELECT * FROM chunks")


def test_ingest_cannot_attach_chunk_to_wrong_matter():
    emb = "[" + ",".join("0.0" for _ in range(S.EMBED_DIM)) + "]"
    with session(INGEST_DSN) as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "INSERT INTO chunks (matter_id, document_id, confidentiality, page, chunk_index, content, embedding)"
                " VALUES (%s,%s,'standard',9,99,'x',%s::vector)",
                (S.MID["B"], S.DID["A-contract"], emb))


# ---------- Audit log ----------
def test_user_can_log_for_self_but_not_for_others(as_user):
    with as_user("alice") as cur:
        cur.execute("INSERT INTO audit_log (user_id, action, query_text) VALUES (%s,'query','q')", (S.UID["alice"],))
    with as_user("alice") as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # RLS WITH CHECK violation
            cur.execute("INSERT INTO audit_log (user_id, action) VALUES (%s,'query')", (S.UID["ben"],))


def test_audit_log_is_append_only_even_for_superuser():
    with psycopg.connect(ADMIN_DSN) as c:
        c.execute("INSERT INTO audit_log (user_id, action) VALUES (%s,'ingest')", (S.UID["alice"],))
        c.commit()  # row must exist, or the row-level triggers have nothing to fire on
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE audit_log SET action = 'tampered'")
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("DELETE FROM audit_log")
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("TRUNCATE audit_log")
        c.rollback()


def test_app_user_cannot_modify_audit_log(as_user):
    with as_user("alice") as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("UPDATE audit_log SET action = 'x'")


def test_audit_visibility_own_rows_vs_auditor():
    with psycopg.connect(ADMIN_DSN) as c:
        c.execute("INSERT INTO audit_log (user_id, action) VALUES (%s,'query'), (%s,'query')",
                  (S.UID["alice"], S.UID["ben"]))
        c.commit()
    with session(APP_DSN, "alice") as cur:
        cur.execute("SELECT DISTINCT user_id::text FROM audit_log")
        assert {r[0] for r in cur.fetchall()} == {S.UID["alice"]}
    with session(APP_DSN, "grace") as cur:
        cur.execute("SELECT DISTINCT user_id::text FROM audit_log")
        assert {S.UID["alice"], S.UID["ben"]} <= {r[0] for r in cur.fetchall()}
