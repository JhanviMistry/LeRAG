"""Seed fully synthetic legal data. No real client material, ever.

Run as the DB owner/admin (bypasses RLS):  python scripts/seed.py
Deterministic UUIDs and embeddings so tests and demos are reproducible.
"""
import hashlib
import math
import os
import random
import uuid

import psycopg

ADMIN_DSN = os.environ.get("PG_ADMIN_DSN", "postgresql://postgres@localhost:5432/legalrag")
EMBED_DIM = 1024


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"legal-rag-demo/{name}"))


def fake_embedding(text: str) -> list[float]:
    """Deterministic unit vector. Placeholder until real embeddings (Bedrock Titan v2, 1024-d)."""
    rng = random.Random(hashlib.sha256(text.encode()).digest())
    v = [rng.gauss(0, 1) for _ in range(EMBED_DIM)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


# ---- Users ----
USERS = {
    "alice": ("alice@demo-firm.test", "Alice Okafor", "associate"),
    "ben":   ("ben@demo-firm.test",   "Ben Larsen",   "associate"),
    "chloe": ("chloe@demo-firm.test", "Chloe Mendes", "paralegal"),
    "dan":   ("dan@demo-firm.test",   "Dan Whitfield", "partner"),
    "eve":   ("eve@demo-firm.test",   "Eve Takahashi", "associate"),   # walled off Matter A
    "frank": ("frank@demo-firm.test", "Frank Osei",   "admin"),
    "grace": ("grace@demo-firm.test", "Grace Lindqvist", "auditor"),
}
UID = {k: uid(f"user/{k}") for k in USERS}

# ---- Matters ----
MATTERS = {
    "A": ("Harlow Holdings v Brightwater Logistics", "CL-2026-0147"),
    "B": ("Estate of Margaret Whitcombe", "PR-2026-0032"),
}
MID = {k: uid(f"matter/{k}") for k in MATTERS}

MEMBERS = [  # (matter, user, matter_role)
    ("A", "alice", "associate"), ("A", "chloe", "paralegal"), ("A", "dan", "partner"), ("A", "eve", "associate"),
    ("B", "ben", "associate"), ("B", "dan", "partner"),
]
WALLS = [("A", "eve", "Previously acted for Brightwater Logistics at another firm")]

# ---- Documents: key -> (matter, title, type, confidentiality, [pages of text]) ----
DOCS = {
    "A-contract": ("A", "Master Services Agreement, Harlow / Brightwater", "contract", "standard", [
        "Clause 12.1: Brightwater shall deliver all pallets to the Harlow distribution centre within 48 hours of dispatch notice.",
        "Clause 14.2: Liability for delayed delivery is capped at 150,000 GBP in aggregate per contract year.",
        "Clause 20.4: Either party may terminate on 30 days written notice following an uncured material breach.",
    ]),
    "A-memo": ("A", "Advice memo: prospects on delay damages", "advice", "privileged", [
        "Counsel's view: the liability cap in clause 14.2 is likely enforceable, but a gross negligence carve-out may be arguable.",
        "Settlement range recommended between 90,000 and 120,000 GBP. Do not disclose reserve figure to opposing side.",
    ]),
    "A-emails": ("A", "Correspondence bundle, March 2026", "correspondence", "standard", [
        "Email 3 March: Harlow notes that three consecutive shipments arrived more than 72 hours after dispatch.",
        "Email 11 March: Brightwater attributes the delays to port congestion and invokes force majeure.",
    ]),
    "B-will": ("B", "Last will and testament of Margaret Whitcombe", "will", "standard", [
        "Clause 3: I give my residuary estate in equal shares to my two children, Thomas and Priya.",
        "Clause 7: I appoint my solicitor as executor and trustee of this will.",
    ]),
    "B-note": ("B", "Counsel's note: contested capacity", "advice", "privileged", [
        "Note: medical records suggest capacity at signing is defensible; recommend obtaining the GP attendance note.",
    ]),
}
DID = {k: uid(f"doc/{k}") for k in DOCS}


def seed(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE chunks, documents, ethical_walls, matter_members, matters, users CASCADE")
        for k, (email, name, role) in USERS.items():
            cur.execute("INSERT INTO users (id, email, display_name, firm_role) VALUES (%s,%s,%s,%s)",
                        (UID[k], email, name, role))
        for k, (name, ref) in MATTERS.items():
            cur.execute("INSERT INTO matters (id, name, client_ref) VALUES (%s,%s,%s)", (MID[k], name, ref))
        for m, u, r in MEMBERS:
            cur.execute("INSERT INTO matter_members VALUES (%s,%s,%s)", (MID[m], UID[u], r))
        for m, u, reason in WALLS:
            cur.execute("INSERT INTO ethical_walls VALUES (%s,%s,%s)", (MID[m], UID[u], reason))
        for k, (m, title, dtype, conf, pages) in DOCS.items():
            sha = hashlib.sha256("".join(pages).encode()).hexdigest()
            cur.execute(
                "INSERT INTO documents (id, matter_id, title, doc_type, confidentiality, sha256) VALUES (%s,%s,%s,%s,%s,%s)",
                (DID[k], MID[m], title, dtype, conf, sha))
            for i, text in enumerate(pages):
                emb = "[" + ",".join(f"{x:.6f}" for x in fake_embedding(text)) + "]"
                cur.execute(
                    "INSERT INTO chunks (id, matter_id, document_id, confidentiality, page, chunk_index, content, embedding)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                    (uid(f"chunk/{k}/{i}"), MID[m], DID[k], conf, i + 1, i, text, emb))
    conn.commit()


if __name__ == "__main__":
    with psycopg.connect(ADMIN_DSN) as c:
        seed(c)
    print("seeded:", len(USERS), "users,", len(MATTERS), "matters,", len(DOCS), "documents")
