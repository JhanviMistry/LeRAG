# Secure legal RAG: step 1, the isolation layer

Matter-level isolation enforced **in the database** with Postgres Row-Level Security,
so an application bug cannot leak one matter's documents into another's answers.
All data is synthetic.

## Run it
    docker compose up -d
    pip install -r requirements.txt
    PG_SUPER_USER=postgres python -m pytest tests -v     # builds a throwaway DB, seeds, tests
    PG_ADMIN_DSN=postgresql://postgres:postgres_dev@localhost:5432/legalrag python scripts/seed.py

## Access model
| Who | Sees matter content? |
|---|---|
| partner / associate on the matter | yes, including privileged |
| paralegal on the matter | standard documents only |
| member behind an ethical wall | nothing on that matter |
| admin | nothing (manages users, not content) |
| auditor | nothing (reads the audit log only) |
| no identity / malformed identity | nothing (fails closed) |

## Design decisions
- **RLS with FORCE** on matters, documents, chunks, audit_log. Policies call one `SECURITY DEFINER`
  function, `app.can_read(matter, confidentiality)`, so app roles never get privileges on the
  membership or wall tables and cannot enumerate who is on what.
- **Separate roles:** `app_user` (read-only on content) and `ingest_svc` (write-only). Neither can bypass RLS.
- **Denormalised `matter_id` and `confidentiality` on chunks**, with a trigger guaranteeing they match the parent
  document, so the policy is a cheap local check and vector search needs no join.
- **Append-only audit log:** privileges plus triggers block UPDATE, DELETE and TRUNCATE, even for the owner.

## Known limit, and the next hardening step
The API sets `app.user_id` per transaction after verifying the user's token. A SQL-injection bug in the API
could therefore change it. Mitigations: parameterised queries only, no LLM-generated SQL, and (planned) a
signed-claim check inside `app.current_user_id()` so the database verifies the identity rather than trusting it.

## Roadmap
1. Isolation layer (this)  2. Ingestion + hybrid retrieval with citations  3. Generation with citation
verification + audit  4. Evaluation and red-team suite  5. IaC deployment (AWS, London)  6. UI + demo users  7. Threat model and rollout docs
