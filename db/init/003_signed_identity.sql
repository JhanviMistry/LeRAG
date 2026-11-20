-- 003_signed_identity.sql: the database verifies WHO is asking, instead of trusting the application.
--
-- The API sends:  app.identity = "<user uuid>.<expiry epoch seconds>.<hmac-sha256 hex>"
-- The database recomputes the HMAC with a secret that app roles cannot read. Anything that does not
-- verify (forged, tampered, expired, malformed, or no secret installed) means "nobody": zero rows.
-- The old app.user_id setting is no longer read at all.

CREATE TABLE IF NOT EXISTS app_secrets (
  name  text PRIMARY KEY,
  value text NOT NULL
);
REVOKE ALL ON app_secrets FROM PUBLIC, app_user, ingest_svc;   -- readable only by the owner and the function below

CREATE OR REPLACE FUNCTION app.current_user_id() RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  ident    text := current_setting('app.identity', true);
  parts    text[];
  secret   text;
  expected text;
BEGIN
  IF ident IS NULL THEN RETURN NULL; END IF;

  parts := string_to_array(ident, '.');
  IF array_length(parts, 1) IS DISTINCT FROM 3 THEN RETURN NULL; END IF;

  -- Check the shape before casting anything, so garbage can never raise an error.
  IF parts[1] !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     OR parts[2] !~ '^[0-9]{1,12}$'
     OR parts[3] !~ '^[0-9a-f]{64}$' THEN
    RETURN NULL;
  END IF;

  IF parts[2]::bigint < extract(epoch FROM now())::bigint THEN RETURN NULL; END IF;   -- expired

  SELECT value INTO secret FROM app_secrets WHERE name = 'identity_secret';
  IF secret IS NULL THEN RETURN NULL; END IF;                                          -- no secret: fail closed

  expected := encode(hmac(parts[1] || '.' || parts[2], secret, 'sha256'), 'hex');
  -- Compare fingerprints of the two signatures, not the signatures themselves, so how long the
  -- comparison takes reveals nothing about how many leading characters were right.
  IF hmac(parts[3], secret, 'sha256') <> hmac(expected, secret, 'sha256') THEN RETURN NULL; END IF;

  RETURN parts[1]::uuid;
END $$;