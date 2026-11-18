"""Retrieval: a question goes in, ranked and citable passages come out.

Hybrid search = keyword (Postgres full-text) + vector (pgvector), merged with
Reciprocal Rank Fusion. Runs as the asking user, so row-level security decides
what is visible. There is deliberately no permission logic in this file.
"""
import re
from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db import user_session
from app.embedder import get_embedder, to_pgvector

RRF_K = 60  # standard RRF constant

WORD = re.compile(r"[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*")  # plain words; keeps things like "14.2" whole


def _or_query(text: str) -> str:
    """Turn a question into 'word or word or word' for the keyword search.

    websearch_to_tsquery joins words with AND by default, so one word missing from a passage
    ('gets' vs 'give') means zero keyword matches. With OR, any matching word counts, and the
    ranking puts passages matching more words first. The regex keeps only plain words, so user
    input can never inject search operators. The word 'or' itself is dropped because it is the operator."""
    words = [w for w in WORD.findall(text) if w.lower() != "or"]
    return " or ".join(words)

SEARCH_SQL = """
WITH vector_hits AS (
  SELECT id, dist, row_number() OVER (ORDER BY dist, id) AS rank
  FROM (
    SELECT id, embedding <=> %(qvec)s::vector AS dist
    FROM chunks
    WHERE (%(matter)s::uuid IS NULL OR matter_id = %(matter)s::uuid)
    ORDER BY embedding <=> %(qvec)s::vector
    LIMIT %(pool)s
  ) nearest
),
keyword_hits AS (
  SELECT id, row_number() OVER (ORDER BY score DESC, dist, id) AS rank
  FROM (
    SELECT c.id, ts_rank_cd(c.content_tsv, q) AS score, c.embedding <=> %(qvec)s::vector AS dist
    FROM chunks c, websearch_to_tsquery('english', %(qtext)s) AS q
    WHERE c.content_tsv @@ q
      AND (%(matter)s::uuid IS NULL OR c.matter_id = %(matter)s::uuid)
    ORDER BY score DESC, dist, c.id
    LIMIT %(pool)s
  ) matches
),
fused AS (
  SELECT COALESCE(v.id, k.id) AS id,
         1 - v.dist AS similarity,
         v.rank AS vector_rank,
         k.rank AS keyword_rank,
         COALESCE(1.0 / (%(rrf_k)s + v.rank), 0) + COALESCE(1.0 / (%(rrf_k)s + k.rank), 0) AS score
  FROM vector_hits v
  FULL OUTER JOIN keyword_hits k ON k.id = v.id
)
SELECT c.id, c.document_id, d.title, d.doc_type, c.page, c.content,
       f.score, f.similarity, f.vector_rank, f.keyword_rank
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN documents d ON d.id = c.document_id
ORDER BY f.score DESC, f.similarity DESC NULLS LAST, c.id
LIMIT %(top_k)s
"""


@dataclass
class Hit:
    chunk_id: UUID
    document_id: UUID
    title: str
    doc_type: str
    page: int
    text: str
    score: float                  # fused RRF score: ~0.033 = ranked #1 by BOTH searches, ~0.016 = by one only
    similarity: float | None      # cosine similarity from vector search (None if only the keyword search found it)
    vector_rank: int | None
    keyword_rank: int | None

    @property
    def citation(self) -> str:
        return f"{self.title}, p. {self.page}"


def search(user_id, question: str, *, matter_id=None, top_k: int = 5, embedder=None) -> list[Hit]:
    question = question.strip()
    if not question:
        raise ValueError("question is empty")
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")
    
    keyword_query = _or_query(question)
    if not keyword_query:
      raise ValueError("question has no searchable words")

    embedder = embedder or get_embedder()
    matter = str(UUID(str(matter_id))) if matter_id else None
    params = {
        "qvec": to_pgvector(embedder.embed([question])[0]),
        "qtext": keyword_query,
        "matter": matter,
        "pool": max(top_k * 4, 20),   # candidates gathered per search before merging
        "rrf_k": RRF_K,
        "top_k": top_k,
    }

    with user_session(user_id) as conn:
        rows = conn.execute(SEARCH_SQL, params).fetchall()
        hits = [
            Hit(chunk_id=r[0], document_id=r[1], title=r[2], doc_type=r[3], page=r[4], text=r[5],
                score=float(r[6]), similarity=None if r[7] is None else float(r[7]),
                vector_rank=r[8], keyword_rank=r[9])
            for r in rows
        ]
        # Audit in the same transaction: who asked what, and exactly which chunks came back.
        conn.execute(
            "INSERT INTO audit_log (user_id, matter_id, action, query_text, chunk_ids, detail)"
            " VALUES (%s, %s, 'query', %s, %s::uuid[], %s)",
            (str(UUID(str(user_id))), matter, question, [str(h.chunk_id) for h in hits],
             Jsonb({"embedder": embedder.model_id, "top_k": top_k, "returned": len(hits)})),
        )
    return hits