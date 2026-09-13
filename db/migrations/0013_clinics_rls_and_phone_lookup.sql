BEGIN;

ALTER TABLE clinics ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinics FORCE ROW LEVEL SECURITY;
CREATE POLICY clinics_tenant_isolation ON clinics TO threshold_api_user
    USING (id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (id = current_setting('app.current_clinic_id', true)::uuid);

CREATE ROLE threshold_rls_bypass NOLOGIN NOSUPERUSER BYPASSRLS;
GRANT SELECT (id, whatsapp_phone_number_id) ON clinics TO threshold_rls_bypass;
GRANT threshold_rls_bypass TO threshold_app;

CREATE FUNCTION resolve_clinic_by_phone(phone_number_id text)
RETURNS uuid
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT id FROM clinics WHERE whatsapp_phone_number_id = phone_number_id;
$$;

ALTER FUNCTION resolve_clinic_by_phone(text) OWNER TO threshold_rls_bypass;

REVOKE EXECUTE ON FUNCTION resolve_clinic_by_phone(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_clinic_by_phone(text) TO threshold_api_user;

COMMIT;
