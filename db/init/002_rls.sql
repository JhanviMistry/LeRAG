
-- rls.sql
-- Legal RAG: database-enforced matter-level access control.
-- Fail closed: missing or invalid identity grants no content access.
--
-- IMPORTANT:
-- app.user_id is currently a transaction-local identity supplied by the
-- application. UUID validation does NOT authenticate that identity.
-- Signed identity verification is a separate security-hardening step.

CREATE SCHEMA IF NOT EXISTS app;

-- ============================================================
-- 1. TRANSACTION IDENTITY
-- ============================================================

CREATE OR REPLACE FUNCTION app.current_user_id()
RETURNS uuid
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $$
    SELECT CASE
        WHEN NULLIF(current_setting('app.user_id', true), '') ~*
          '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        THEN current_setting('app.user_id', true)::uuid
        ELSE NULL
    END
$$;


-- ============================================================
-- 2. CONTENT READ AUTHORISATION
-- ============================================================

-- SECURITY DEFINER allows the function to inspect membership and
-- ethical-wall tables without granting those tables to app_user.
--
-- The function owner must have sufficient privileges to read these
-- tables despite their security configuration.

CREATE OR REPLACE FUNCTION app.can_read(
    p_matter uuid,
    p_conf text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.matter_members mm
        JOIN public.users u ON u.id = mm.user_id
        WHERE mm.matter_id = p_matter
          AND mm.user_id = app.current_user_id()

          -- Only these firm roles can read matter content.
          AND u.firm_role IN (
              'partner',
              'associate',
              'paralegal'
          )

          -- Ethical walls override matter membership.
          AND NOT EXISTS (
              SELECT 1
              FROM public.ethical_walls w
              WHERE w.matter_id = p_matter
                AND w.user_id = mm.user_id
          )

          -- Standard documents: all authorised matter members.
          -- Privileged documents: partner/associate at BOTH levels.
          AND (
              p_conf = 'standard'
              OR (
                  p_conf = 'privileged'
                  AND u.firm_role IN ('partner', 'associate')
                  AND mm.matter_role IN ('partner', 'associate')
              )
          )
    )
$$;


-- ============================================================
-- 3. INGESTION AUTHORISATION
-- ============================================================

-- Ingestion must be associated with an actual matter member.
-- The uploading user's identity must match the transaction identity.
--
-- This is defence in depth. The ingestion service itself must also
-- authenticate the caller and validate the upload before insertion.

CREATE OR REPLACE FUNCTION app.can_ingest(
    p_matter uuid,
    p_uploader uuid,
    p_conf text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.matter_members mm
        JOIN public.users u ON u.id = mm.user_id
        WHERE mm.matter_id = p_matter
          AND mm.user_id = p_uploader
          AND p_uploader = app.current_user_id()

          AND u.firm_role IN (
              'partner',
              'associate',
              'paralegal'
          )

          AND NOT EXISTS (
              SELECT 1
              FROM public.ethical_walls w
              WHERE w.matter_id = p_matter
                AND w.user_id = mm.user_id
          )

          AND (
              p_conf = 'standard'
              OR (
                  p_conf = 'privileged'
                  AND u.firm_role IN ('partner', 'associate')
                  AND mm.matter_role IN ('partner', 'associate')
              )
          )
    )
$$;


-- ============================================================
-- 4. AUDITOR AUTHORISATION
-- ============================================================

-- Auditors can read audit records but never matter content.

CREATE OR REPLACE FUNCTION app.is_auditor()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.users u
        WHERE u.id = app.current_user_id()
          AND u.firm_role = 'auditor'
    )
$$;


-- ============================================================
-- 5. FUNCTION EXECUTION PRIVILEGES
-- ============================================================

REVOKE ALL ON FUNCTION app.current_user_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION app.can_read(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.can_ingest(uuid, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.is_auditor() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION app.current_user_id()
    TO app_user, ingest_svc;

GRANT EXECUTE ON FUNCTION app.can_read(uuid, text)
    TO app_user;

GRANT EXECUTE ON FUNCTION app.can_ingest(uuid, uuid, text)
    TO ingest_svc;

GRANT EXECUTE ON FUNCTION app.is_auditor()
    TO app_user;


-- ============================================================
-- 6. ENABLE AND FORCE ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE public.matters
    ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.matters
    FORCE ROW LEVEL SECURITY;

ALTER TABLE public.documents
    ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.documents
    FORCE ROW LEVEL SECURITY;

ALTER TABLE public.chunks
    ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.chunks
    FORCE ROW LEVEL SECURITY;

ALTER TABLE public.audit_log
    ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.audit_log
    FORCE ROW LEVEL SECURITY;


-- ============================================================
-- 7. REMOVE EXISTING POLICIES BEFORE RECREATING THEM
-- ============================================================

DROP POLICY IF EXISTS matters_read ON public.matters;

DROP POLICY IF EXISTS documents_read ON public.documents;
DROP POLICY IF EXISTS documents_ingest ON public.documents;

DROP POLICY IF EXISTS chunks_read ON public.chunks;
DROP POLICY IF EXISTS chunks_ingest ON public.chunks;

DROP POLICY IF EXISTS audit_insert ON public.audit_log;
DROP POLICY IF EXISTS audit_read ON public.audit_log;


-- ============================================================
-- 8. MATTER POLICIES
-- ============================================================

-- A user can only discover matters they are authorised to read.

CREATE POLICY matters_read
ON public.matters
FOR SELECT
TO app_user
USING (
    app.can_read(id, 'standard')
);


-- ============================================================
-- 9. DOCUMENT POLICIES
-- ============================================================

-- Application users can read only authorised documents.

CREATE POLICY documents_read
ON public.documents
FOR SELECT
TO app_user
USING (
    app.can_read(matter_id, confidentiality)
);


-- Ingestion service can insert only documents associated with
-- an authorised uploader and matter.

CREATE POLICY documents_ingest
ON public.documents
FOR INSERT
TO ingest_svc
WITH CHECK (
    app.can_ingest(
        matter_id,
        uploaded_by,
        confidentiality
    )
);


-- ============================================================
-- 10. CHUNK POLICIES
-- ============================================================

-- Retrieval cannot access chunks outside the authorised matter.

CREATE POLICY chunks_read
ON public.chunks
FOR SELECT
TO app_user
USING (
    app.can_read(matter_id, confidentiality)
);


-- Ingestion can add chunks only for an authorised matter.
-- The schema.sql trigger additionally verifies that the chunk
-- matches its parent document's matter and confidentiality.

CREATE POLICY chunks_ingest
ON public.chunks
FOR INSERT
TO ingest_svc
WITH CHECK (
    app.can_ingest(
        matter_id,
        app.current_user_id(),
        confidentiality
    )
);


-- ============================================================
-- 11. AUDIT LOG POLICIES
-- ============================================================

-- Application and ingestion services may insert audit events
-- only under their current transaction identity.

CREATE POLICY audit_insert
ON public.audit_log
FOR INSERT
TO app_user, ingest_svc
WITH CHECK (
    user_id IS NOT DISTINCT FROM app.current_user_id()
);


-- Users can read their own audit records.
-- Auditors can read all audit records.

CREATE POLICY audit_read
ON public.audit_log
FOR SELECT
TO app_user
USING (
    user_id = app.current_user_id()
    OR app.is_auditor()
);


-- ============================================================
-- 12. TABLE PRIVILEGES
-- ============================================================

-- Remove direct access first.

REVOKE ALL ON ALL TABLES IN SCHEMA public
FROM PUBLIC, app_user, ingest_svc;


-- Application: read-only access to authorised content.

GRANT SELECT
ON public.matters, public.documents, public.chunks
TO app_user;


-- Ingestion: insert-only access.

GRANT INSERT
ON public.documents, public.chunks
TO ingest_svc;


-- Audit: application users can insert and read authorised records.

GRANT INSERT, SELECT
ON public.audit_log
TO app_user;

GRANT INSERT
ON public.audit_log
TO ingest_svc;


-- Sequence permission required for audit_log inserts.

GRANT USAGE
ON SEQUENCE public.audit_log_id_seq
TO app_user, ingest_svc;


-- No direct access to identity or membership tables.
-- app_user and ingest_svc must not enumerate:
-- users, matter_members, ethical_walls.


-- ============================================================
-- 13. APPEND-ONLY AUDIT LOG
-- ============================================================

CREATE OR REPLACE FUNCTION app.audit_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only: UPDATE, DELETE and TRUNCATE are forbidden';
END;
$$;

REVOKE ALL ON FUNCTION app.audit_immutable() FROM PUBLIC;


DROP TRIGGER IF EXISTS audit_no_update
ON public.audit_log;

CREATE TRIGGER audit_no_update
BEFORE UPDATE OR DELETE
ON public.audit_log
FOR EACH ROW
EXECUTE FUNCTION app.audit_immutable();


DROP TRIGGER IF EXISTS audit_no_truncate
ON public.audit_log;

CREATE TRIGGER audit_no_truncate
BEFORE TRUNCATE
ON public.audit_log
FOR EACH STATEMENT
EXECUTE FUNCTION app.audit_immutable();


-- END OF RLS CONFIGURATION
