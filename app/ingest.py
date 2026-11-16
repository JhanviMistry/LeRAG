"""Ingestion: pages of text in, searchable rows out.

Flow:  validate -> chunk -> embed -> (one transaction: document, chunks, audit row)

Runs as the write-only ingest_svc role. It can insert but can never read anything back,
so even a bug here cannot be used to pull documents out of the database.
"""
import hashlib
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.chunker import chunk_document
from app.db import INGEST_DSN, user_session
from app.embedder import get_embedder, to_pgvector

CONFIDENTIALITY_LEVELS = {"standard", "privileged"}


def ingest_document(*, matter_id, title, doc_type, confidentiality, pages, uploaded_by, embedder=None) -> str:
    """Store one document. Returns the new document's id.

    pages: list of strings, pages[0] is page 1.
    uploaded_by: the user the audit log will name. They must be the person actually uploading.
    """
    # 1. Validate everything before touching the database or paying for embeddings.
    if confidentiality not in CONFIDENTIALITY_LEVELS:
        raise ValueError(f"confidentiality must be one of {sorted(CONFIDENTIALITY_LEVELS)}, got {confidentiality!r}")
    if not title.strip():
        raise ValueError("title is required")
    matter_id = str(UUID(str(matter_id)))
    uploaded_by = str(UUID(str(uploaded_by)))

    # 2. Chunk and embed. This is the slow part, so it happens outside the transaction.
    embedder = embedder or get_embedder()
    chunks = chunk_document(pages)
    if not chunks:
        raise ValueError("document contains no text")
    vectors = embedder.embed([c.text for c in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError("embedder returned a different number of vectors than chunks")

    sha256 = hashlib.sha256("\n".join(pages).encode("utf-8")).hexdigest()  # fingerprint, useful for dedup later
    doc_id = str(uuid4())  # generated here because RETURNING would need read permission

    # 3. One transaction: everything is saved, or nothing is.
    with user_session(uploaded_by, dsn=INGEST_DSN) as conn:
        conn.execute(
            "INSERT INTO documents (id, matter_id, title, doc_type, confidentiality, sha256, uploaded_by)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (doc_id, matter_id, title, doc_type, confidentiality, sha256, uploaded_by),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (matter_id, document_id, confidentiality, page, chunk_index, content, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s::vector)",
                [
                    (matter_id, doc_id, confidentiality, c.page, c.chunk_index, c.text, to_pgvector(v))
                    for c, v in zip(chunks, vectors)
                ],
            )
        conn.execute(
            "INSERT INTO audit_log (user_id, matter_id, action, detail) VALUES (%s, %s, 'ingest', %s)",
            (uploaded_by, matter_id, Jsonb({
                "document_id": doc_id,
                "title": title,
                "chunks": len(chunks),
                "embedder": embedder.model_id,   # records which model made the vectors
            })),
        )
    return doc_id