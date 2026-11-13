-- schema.sql: roles, tables, indexes
-- Principle: the DATABASE enforces matter isolation. App code is not trusted to.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------- Roles ----------
-- Passwords are for the local/demo stack only; in AWS they come from Secrets Manager.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user LOGIN PASSWORD 'app_user_dev' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ingest_svc') THEN
    CREATE ROLE ingest_svc LOGIN PASSWORD 'ingest_svc_dev' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
  END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS app;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_user, ingest_svc;
GRANT USAGE ON SCHEMA app TO app_user, ingest_svc;

-- ---------- Identity and access tables ----------
CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text NOT NULL UNIQUE,
  display_name  text NOT NULL,
  -- firm-wide role. 'admin' manages users, NOT matter content. 'auditor' reads the audit log only.
  firm_role     text NOT NULL CHECK (firm_role IN ('partner','associate','paralegal','admin','auditor'))
);

CREATE TABLE matters (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  client_ref  text NOT NULL,
  status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed'))
);

CREATE TABLE matter_members (
  matter_id   uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
  matter_role text NOT NULL CHECK (matter_role IN ('partner','associate','paralegal')),
  PRIMARY KEY (matter_id, user_id)
);

-- An ethical wall overrides membership: a walled user sees nothing on the matter.
CREATE TABLE ethical_walls (
  matter_id  uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  user_id    uuid NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
  reason     text NOT NULL,
  PRIMARY KEY (matter_id, user_id)
);

-- ---------- Content tables ----------
CREATE TABLE documents (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  matter_id       uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  title           text NOT NULL,
  doc_type        text NOT NULL,
  confidentiality text NOT NULL DEFAULT 'standard' CHECK (confidentiality IN ('standard','privileged')),
  sha256          text NOT NULL,
  uploaded_by     uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- matter_id and confidentiality are denormalised onto chunks so the RLS policy
-- is a cheap local check and the retrieval query needs no join.
CREATE TABLE chunks (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  matter_id       uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  document_id     uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  confidentiality text NOT NULL CHECK (confidentiality IN ('standard','privileged')),
  page            int  NOT NULL,
  chunk_index     int  NOT NULL,
  content         text NOT NULL,
  content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  embedding       vector(1024) NOT NULL,
  UNIQUE (document_id, chunk_index)
);

-- Guard: a chunk must carry the same matter and confidentiality as its parent document.
-- SECURITY DEFINER because the ingest role cannot SELECT documents.
CREATE OR REPLACE FUNCTION app.chunk_matches_document() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE d_matter uuid; d_conf text;
BEGIN
  SELECT matter_id, confidentiality INTO d_matter, d_conf FROM documents WHERE id = NEW.document_id;
  IF d_matter IS NULL OR d_matter <> NEW.matter_id OR d_conf <> NEW.confidentiality THEN
    RAISE EXCEPTION 'chunk matter/confidentiality does not match its document';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER chunks_match_document BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION app.chunk_matches_document();

CREATE INDEX chunks_matter_idx ON chunks (matter_id);
CREATE INDEX chunks_tsv_idx    ON chunks USING gin (content_tsv);
CREATE INDEX chunks_vec_idx    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX documents_matter_idx ON documents (matter_id);

-- ---------- Audit log ----------
CREATE TABLE audit_log (
  id          bigserial PRIMARY KEY,
  ts          timestamptz NOT NULL DEFAULT now(),
  user_id     uuid,
  matter_id   uuid,
  action      text NOT NULL,          -- 'query', 'answer', 'denied', 'ingest'
  query_text  text,
  chunk_ids   uuid[],
  model_id    text,
  detail      jsonb
);
