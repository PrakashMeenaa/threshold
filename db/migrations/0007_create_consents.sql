CREATE TABLE consents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES clinics (id),
    patient_id UUID NOT NULL REFERENCES patients (id),
    consent_type TEXT NOT NULL,
    granted BOOLEAN NOT NULL,
    presented_text TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
