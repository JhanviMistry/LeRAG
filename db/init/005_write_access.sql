-- 005_write_access.sql: who may ADD documents to a matter, enforced by the database itself.
-- Only partners and associates, on that matter, not behind an ethical wall. Paralegals, admins,
-- auditors and non-members cannot write, whatever the application tells the database.

CREATE OR REPLACE FUNCTION app.can_write(p_matter uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT EXISTS (
    SELECT 1
    FROM matter_members mm
    JOIN users u ON u.id = mm.user_id
    WHERE mm.matter_id = p_matter
      AND mm.user_id   = app.current_user_id()
      AND u.firm_role  IN ('partner', 'associate')
      AND mm.matter_role IN ('partner', 'associate')
      AND NOT EXISTS (SELECT 1 FROM ethical_walls w
                      WHERE w.matter_id = p_matter AND w.user_id = mm.user_id)
  )
$$;

REVOKE ALL ON FUNCTION app.can_write(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.can_write(uuid) TO app_user, ingest_svc;

-- Replace the old "anyone may insert" policies from step 1 with a real check.
DROP POLICY IF EXISTS documents_ingest ON documents;
DROP POLICY IF EXISTS chunks_ingest ON chunks;
CREATE POLICY documents_ingest ON documents FOR INSERT TO ingest_svc WITH CHECK (app.can_write(matter_id));
CREATE POLICY chunks_ingest    ON chunks    FOR INSERT TO ingest_svc WITH CHECK (app.can_write(matter_id));