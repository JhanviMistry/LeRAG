-- 004_auth.sql: credentials, and a login role that can do almost nothing else.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'auth_svc') THEN
    CREATE ROLE auth_svc LOGIN PASSWORD 'auth_svc_dev' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS credentials (
  user_id        uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  password_hash  text NOT NULL
);
REVOKE ALL ON credentials FROM PUBLIC, app_user, ingest_svc;   -- the content roles never see password hashes

GRANT USAGE ON SCHEMA public TO auth_svc;
GRANT SELECT (id, email) ON users TO auth_svc;      -- column-level: it cannot even see firm_role
GRANT SELECT ON credentials TO auth_svc;
GRANT INSERT ON audit_log TO auth_svc;               -- append only; no SELECT, UPDATE or DELETE
GRANT USAGE ON SEQUENCE audit_log_id_seq TO auth_svc;

-- Row-level security still applies to audit_log: this role may append login events and nothing else.
CREATE POLICY audit_auth_insert ON audit_log FOR INSERT TO auth_svc
  WITH CHECK (action IN ('login_ok', 'login_failed'));