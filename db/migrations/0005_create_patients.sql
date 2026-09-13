CREATE TABLE patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES clinics (id),
    whatsapp_id TEXT NOT NULL,
    full_name TEXT,
    preferred_language TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (clinic_id, whatsapp_id)
);
