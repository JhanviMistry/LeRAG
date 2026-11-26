# Secure Legal RAG

A security-first Retrieval-Augmented Generation (RAG) system for legal documents, built around **matter-level access control enforced by PostgreSQL Row-Level Security (RLS)**.

The system is designed to ensure that users can retrieve and generate answers only from documents they are authorised to access. Access restrictions are enforced at the database layer, not solely by application logic.

All demonstration data is synthetic.

## Architecture

```text
                  ┌──────────────────────┐
                  │       Client         │
                  └──────────┬───────────┘
                             │
                       Bearer token
                             │
                  ┌──────────▼───────────┐
                  │      FastAPI         │
                  │ Authentication       │
                  │ Authorisation        │
                  │ Request validation   │
                  └──────────┬───────────┘
                             │
                  Transaction-scoped
                     user identity
                             │
                  ┌──────────▼───────────┐
                  │     PostgreSQL       │
                  │       pgvector       │
                  │                      │
                  │  Row-Level Security  │
                  │  Matter isolation    │
                  │  Audit logging       │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │   Retrieval layer    │
                  │ Vector + keyword     │
                  │ Citation verification│
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │   Answer generation  │
                  │ Grounded responses   │
                  └──────────────────────┘
```

## Security model

Matter-level isolation is enforced inside PostgreSQL using RLS with `FORCE ROW LEVEL SECURITY`.

| User role | Matter content access |
|---|---|
| Partner / associate assigned to a matter | Standard and privileged documents |
| Paralegal assigned to a matter | Standard documents only |
| User behind an ethical wall | No access |
| Administrator | No matter content access |
| Auditor | Audit log access only |
| Missing or malformed identity | No access; fails closed |

### Database enforcement

- **RLS with FORCE:** Applied to matters, documents, chunks and audit logs.
- **Centralised authorisation:** Policies use a `SECURITY DEFINER` function, `app.can_read(matter, confidentiality)`.
- **Restricted metadata access:** Application roles do not receive direct privileges on membership or ethical-wall tables.
- **Separate database roles:** `app_user` is read-only on content; `ingest_svc` handles ingestion writes. Neither role can bypass RLS.
- **Denormalised chunk metadata:** `matter_id` and `confidentiality` are stored on chunks. A database trigger ensures they match the parent document, allowing retrieval policies to evaluate local metadata without an additional join.
- **Append-only audit trail:** Database privileges and triggers prevent UPDATE, DELETE and TRUNCATE operations on audit records.

## Authentication and API

The FastAPI application provides:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check |
| `POST /auth/login` | User authentication |
| `GET /matters` | List accessible matters |
| `POST /search` | Search authorised document content |
| `POST /ask` | Generate an answer using retrieved evidence |
| `POST /documents` | Document ingestion |

Authentication uses signed tokens and transaction-scoped identity. The API validates the token before setting the database identity for the transaction.

Additional controls include:

- Password authentication.
- Per-email and per-IP login throttling.
- Per-user query throttling.
- Upload permission checks in the application, backed by database restrictions.
- Parameterised database queries.
- Audit logging of relevant operations.

## Retrieval and answer generation

The retrieval pipeline combines vector similarity and keyword search to identify relevant document passages.

The answer pipeline is designed to:

1. Retrieve content within the authenticated user's access boundary.
2. Ground answers in retrieved passages.
3. Verify citations against the retrieved evidence.
4. Record relevant activity in the audit log.
5. Refuse to answer when sufficient supporting evidence is unavailable.

Retrieval and answer quality are evaluated separately from the security guarantees.

## Evaluation

The project includes a golden-question evaluation suite covering security, retrieval quality and answer quality.

The security evaluation independently checks retrieved passages against access rules.

Example evaluation:

| Evaluation area | Result |
|---|---|
| Security | PASS — 0 access-control violations |
| Retrieval | Exact-question and paraphrase retrieval evaluated separately |
| Answer quality | Citation correctness and unsupported-answer refusal evaluated |
| Regression coverage | Automated test suite |

Security passing does not imply that answer generation is production-ready. Retrieval quality, citation correctness and refusal behaviour remain separate evaluation dimensions.

Run the evaluation suite:

```bash
python evaluation/run_eval.py
```

## Run locally

### Prerequisites

- Python
- Docker and Docker Compose
- PostgreSQL client tools

### 1. Start PostgreSQL

```bash
docker compose up -d
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run tests

The test suite builds and seeds a throwaway database.

```bash
PG_SUPER_USER=postgres python -m pytest tests -v
```

### 4. Seed the development database

```bash
PG_ADMIN_DSN=postgresql://postgres:postgres_dev@localhost:5432/legalrag \
python scripts/seed.py
```

Configure the remaining application environment variables in your local `.env` file. Do not commit credentials, signing secrets or database passwords.

## Known security boundary

The API currently sets `app.user_id` within each transaction after verifying the user's signed token.

However, the database still trusts that transaction-scoped identity. A SQL-injection vulnerability in the application could potentially alter it.

Current mitigations include:

- Parameterised queries.
- No LLM-generated SQL.
- Restricted database roles.
- Database-enforced RLS.

**Planned hardening:** introduce signed-claim verification inside `app.current_user_id()` so PostgreSQL independently verifies the identity instead of trusting an application-supplied user ID.

This is an important distinction: database-enforced matter isolation protects against application-level filtering mistakes, but the current identity hand-off is not yet independently authenticated by the database.

## Roadmap

| Stage | Scope | Status |
|---|---|---|
| 1 | Database-enforced matter isolation | Implemented |
| 2 | Ingestion, hybrid retrieval and citations | Implemented |
| 3 | Answer generation, citation verification and audit | Implemented |
| 4 | Evaluation and red-team testing | Implemented; further hardening ongoing |
| 5 | AWS infrastructure deployment in London (`eu-west-2`) | Planned |
| 6 | User interface and demonstration users | Planned |
| 7 | Threat model and rollout documentation | Planned |

## Technology stack

- **Backend:** Python, FastAPI
- **Database:** PostgreSQL 16, pgvector
- **Security:** PostgreSQL RLS, `SECURITY DEFINER`, JWT authentication, role separation
- **Data access:** SQLAlchemy, psycopg
- **Infrastructure:** Docker, Docker Compose
- **Testing:** Pytest, custom evaluation harness
- **Cloud roadmap:** AWS, Terraform

## Design principles

1. **Security at the data layer:** Access control must not depend solely on application code.
2. **Fail closed:** Missing, malformed or unauthorised identities must never receive matter content.
3. **Least privilege:** Application, ingestion, administration and audit responsibilities are separated.
4. **Traceability:** Relevant operations are recorded in an append-only audit trail.
5. **Evidence-grounded answers:** Generated answers must be supported by retrieved legal document passages.
6. **Measurable quality:** Security, retrieval and answer quality are evaluated independently.

---

**Project status:** A locally runnable legal RAG implementation with database-enforced matter isolation, authentication, retrieval, citation verification, audit logging and automated evaluation. AWS deployment and further identity hardening are the next stages.

All documents and users used in the demonstration are synthetic.