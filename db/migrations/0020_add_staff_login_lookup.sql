BEGIN;

GRANT SELECT (id, clinic_id, email, password_hash, role) ON staff_users TO threshold_rls_bypass;

CREATE FUNCTION resolve_staff_by_email(email_input text)
RETURNS TABLE(id uuid, clinic_id uuid, password_hash text, role text)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT id, clinic_id, password_hash, role FROM staff_users WHERE email = email_input;
$$;

ALTER FUNCTION resolve_staff_by_email(text) OWNER TO threshold_rls_bypass;

REVOKE EXECUTE ON FUNCTION resolve_staff_by_email(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_staff_by_email(text) TO threshold_api_user;

COMMIT;
