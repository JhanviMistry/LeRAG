"""Evaluation harness. Run from the project root:  python evaluation/run_eval.py

Builds a throwaway database (legalrag_eval), loads the synthetic corpus through the real ingestion
pipeline, asks every golden question through the real answering pipeline, and reports:
  1. SECURITY  : must be 100%. Any violation makes this script exit with an error.
  2. RETRIEVAL : is the right passage found, and how high does it rank?
  3. ANSWERS   : does it answer what it should and refuse what it should?
  4. THRESHOLD : how the confidence cut-off trades answers against refusals.
Uses whichever EMBEDDER and ANSWER_MODEL are set in the environment (defaults: local, fake).
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

HOST = os.environ.get("PGHOST", "localhost")
PORT = os.environ.get("PGPORT", "5432")
SUPER = os.environ.get("PG_SUPER_USER", "postgres")
DB = "legalrag_eval"
ADMIN_DSN = f"postgresql://{SUPER}@{HOST}:{PORT}/{DB}"
# Point the application at the throwaway evaluation database BEFORE importing any application code.
os.environ["APP_DSN"] = f"postgresql://app_user:app_user_dev@{HOST}:{PORT}/{DB}"
os.environ["INGEST_DSN"] = f"postgresql://ingest_svc:ingest_svc_dev@{HOST}:{PORT}/{DB}"
os.environ["IDENTITY_SECRET"] = "evaluation-only-identity-secret-not-for-real-use"

import psycopg  # noqa: E402
import seed as S  # noqa: E402
import app.answering as answering  # noqa: E402
from app.answering import answer_question  # noqa: E402
from app.ingest import ingest_document  # noqa: E402
from corpus import CORPUS  # noqa: E402
from install_identity_secret import install_secret  # noqa: E402
from golden import GOLDEN  # noqa: E402


# ---------- build the evaluation database ----------
def build_database():
    with psycopg.connect(f"postgresql://{SUPER}@{HOST}:{PORT}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for f in sorted((ROOT / "db" / "init").glob("*.sql")):
            c.execute(f.read_text())
        install_secret(c, os.environ["IDENTITY_SECRET"])
    with psycopg.connect(ADMIN_DSN) as c:
        S.seed(c)                                   # users, matters, members, ethical wall
        c.execute("DELETE FROM chunks")             # drop the placeholder demo documents
        c.execute("DELETE FROM documents")
        c.commit()
    uploader = {"A": "alice", "B": "ben"}
    for d in CORPUS:
        ingest_document(matter_id=S.MID[d.matter], title=d.title, doc_type=d.doc_type,
                        confidentiality=d.confidentiality, pages=d.pages, uploaded_by=S.UID[uploader[d.matter]])


# ---------- an independent second implementation of the access rules ----------
def allowed_titles(user: str) -> set[str]:
    """Who may see what, worked out in plain Python from the membership list, NOT from the database.
    If the database's row-level security ever disagreed with this, the security check below would fail."""
    allowed = set()
    for matter, member, role in S.MEMBERS:
        if member != user:
            continue
        if any(wm == matter and wu == user for wm, wu, _ in S.WALLS):
            continue                                # ethical wall overrides membership
        for d in CORPUS:
            if d.matter == matter and (d.confidentiality == "standard" or role in ("partner", "associate")):
                allowed.add(d.title)
    return allowed                                  # admins and auditors are never members: they get nothing


# ---------- helpers ----------
def title_of(claim) -> str:
    return claim.citation.rsplit(", p. ", 1)[0]


def first_rank(q, hits):
    for i, h in enumerate(hits, start=1):
        if (h.title, h.page) in q.expect:
            return i
    return None


def best_sim(answer) -> float:
    return max((h.similarity for h in answer.hits if h.similarity is not None), default=0.0)


def pct(n, d) -> str:
    return "n/a" if d == 0 else f"{100 * n / d:.0f}% ({n}/{d})"


# ---------- 1. security ----------
def security_failures(results):
    fails = []
    for q, a in results:
        allowed = allowed_titles(q.user)
        for h in a.hits:
            if h.title not in allowed:
                fails.append(f"{q.id}: {q.user} was shown '{h.title}', which the access rules forbid")
            if h.title in q.forbidden:
                fails.append(f"{q.id}: forbidden document '{h.title}' was retrieved")
        for c in a.claims:
            if title_of(c) in q.forbidden:
                fails.append(f"{q.id}: forbidden document '{title_of(c)}' was cited in the answer")
            for word in q.banned:
                if word.lower() in c.text.lower():
                    fails.append(f"{q.id}: answer contains banned word '{word}'")
    return fails


def main():
    print("Building evaluation database and ingesting corpus...")
    build_database()
    print(f"Asking {len(GOLDEN)} golden questions "
          f"(embedder={os.environ.get('EMBEDDER', 'local')}, model={os.environ.get('ANSWER_MODEL', 'fake')})...\n")
    results = []
    for q in GOLDEN:
        a = answer_question(S.UID[q.user], q.question, matter_id=S.MID[q.matter] if q.matter else None)
        results.append((q, a))

    # ===== 1. SECURITY =====
    fails = security_failures(results)
    inspected = sum(len(a.hits) for _, a in results)
    print("=" * 70)
    print("1. SECURITY (must be 100%)")
    print("=" * 70)
    print(f"{len(results)} questions, {inspected} retrieved passages checked against the independent access rules")
    print("RESULT:", "PASS, no violations" if not fails else f"FAIL, {len(fails)} violation(s)")
    for f in fails:
        print("  !!", f)

    # ===== 2. RETRIEVAL =====
    print("\n" + "=" * 70)
    print("2. RETRIEVAL QUALITY (answerable questions; top 5 passages)")
    print("=" * 70)
    for kind in ("exact", "paraphrase"):
        rows = [(q, a) for q, a in results if q.kind == kind]
        ranks = [first_rank(q, a.hits) for q, a in rows]
        mrr = sum(1 / r for r in ranks if r) / len(rows)
        print(f"{kind:<11} found in top 5: {pct(sum(r is not None for r in ranks), len(rows)):<12}"
              f" ranked #1: {pct(sum(r == 1 for r in ranks), len(rows)):<12} MRR: {mrr:.2f}")
        for (q, a), r in zip(rows, ranks):
            if r is None:
                print(f"    missed {q.id}: {q.question}")
            elif r > 1:
                print(f"    ranked #{r} {q.id}: {q.question}")

    # ===== 3. ANSWERS =====
    print("\n" + "=" * 70)
    print("3. ANSWER BEHAVIOUR")
    print("=" * 70)
    for kind in ("exact", "paraphrase"):
        rows = [(q, a) for q, a in results if q.kind == kind]
        answered = [(q, a) for q, a in rows if a.status == "answered"]
        cited_ok = [(q, a) for q, a in answered if any((title_of(c), c.page) in q.expect for c in a.claims)]
        print(f"{kind:<11} answered: {pct(len(answered), len(rows)):<12} answered with a correct citation: {pct(len(cited_ok), len(rows))}")
    rows = [(q, a) for q, a in results if q.kind == "unanswerable"]
    refused = [(q, a) for q, a in rows if a.status != "answered"]
    print(f"unanswerable correctly refused: {pct(len(refused), len(rows))}")
    for q, a in rows:
        if a.status == "answered":
            print(f"    WRONGLY ANSWERED {q.id}: {q.question}")
            print(f"      -> {a.claims[0].quote[:80]!r} [{a.claims[0].citation}]")

    # ===== 4. THRESHOLD SWEEP =====
    print("\n" + "=" * 70)
    print("4. CONFIDENCE THRESHOLD SWEEP (best similarity must reach the threshold to answer)")
    print("=" * 70)
    sims = {kind: [best_sim(a) for q, a in results if q.kind == kind] for kind in ("exact", "paraphrase", "unanswerable")}
    print(f"{'threshold':>9} | {'exact kept':>12} | {'paraphrase kept':>15} | {'unanswerable refused':>20}")
    for t in [x / 100 for x in range(5, 55, 5)]:
        kept_e = sum(s >= t for s in sims["exact"])
        kept_p = sum(s >= t for s in sims["paraphrase"])
        refused_u = sum(s < t for s in sims["unanswerable"])
        mark = "   <- current setting" if abs(t - answering.MIN_SIMILARITY) < 1e-9 else ""
        print(f"{t:>9.2f} | {pct(kept_e, len(sims['exact'])):>12} | {pct(kept_p, len(sims['paraphrase'])):>15} |"
              f" {pct(refused_u, len(sims['unanswerable'])):>20}{mark}")

    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()