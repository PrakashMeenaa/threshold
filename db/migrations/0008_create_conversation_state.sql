CREATE TABLE conversation_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES clinics (id),
    patient_id UUID NOT NULL REFERENCES patients (id),
    agent TEXT NOT NULL,
    state JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (clinic_id, patient_id, agent)
);
