BEGIN;

CREATE TABLE processed_webhooks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES clinics (id),
    payload_hash TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (clinic_id, payload_hash)
);

GRANT SELECT, INSERT ON processed_webhooks TO threshold_api_user;

ALTER TABLE processed_webhooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_webhooks FORCE ROW LEVEL SECURITY;
CREATE POLICY processed_webhooks_clinic_isolation ON processed_webhooks TO threshold_api_user
    USING (clinic_id = current_setting('app.current_clinic_id', true)::uuid)
    WITH CHECK (clinic_id = current_setting('app.current_clinic_id', true)::uuid);

COMMIT;
