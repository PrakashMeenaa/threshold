BEGIN;

CREATE ROLE threshold_api_user WITH LOGIN NOSUPERUSER NOBYPASSRLS;

GRANT USAGE ON SCHEMA public TO threshold_api_user;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    clinics,
    departments,
    doctors,
    availability_slots,
    patients,
    appointments,
    consents,
    conversation_state,
    gate_log,
    staff_users
TO threshold_api_user;

ALTER TABLE departments ENABLE ROW LEVEL SECURITY;
ALTER TABLE departments FORCE ROW LEVEL SECURITY;
CREATE POLICY departments_clinic_isolation ON departments TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

ALTER TABLE doctors ENABLE ROW LEVEL SECURITY;
ALTER TABLE doctors FORCE ROW LEVEL SECURITY;
CREATE POLICY doctors_clinic_isolation ON doctors TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

ALTER TABLE availability_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE availability_slots FORCE ROW LEVEL SECURITY;
CREATE POLICY availability_slots_clinic_isolation ON availability_slots TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

ALTER TABLE patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE patients FORCE ROW LEVEL SECURITY;
CREATE POLICY patients_clinic_isolation ON patients TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments FORCE ROW LEVEL SECURITY;
CREATE POLICY appointments_clinic_isolation ON appointments TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

ALTER TABLE consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE consents FORCE ROW LEVEL SECURITY;
CREATE POLICY consents_clinic_isolation ON consents TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

ALTER TABLE conversation_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_state FORCE ROW LEVEL SECURITY;
CREATE POLICY conversation_state_clinic_isolation ON conversation_state TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

ALTER TABLE gate_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE gate_log FORCE ROW LEVEL SECURITY;
CREATE POLICY gate_log_clinic_isolation ON gate_log TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

ALTER TABLE staff_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_users FORCE ROW LEVEL SECURITY;
CREATE POLICY staff_users_clinic_isolation ON staff_users TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

COMMIT;
