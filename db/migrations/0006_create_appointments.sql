CREATE TABLE appointments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES clinics (id),
    patient_id UUID NOT NULL REFERENCES patients (id),
    doctor_id UUID NOT NULL REFERENCES doctors (id),
    slot_id UUID NOT NULL REFERENCES availability_slots (id),
    status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'cancelled', 'completed', 'no_show')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
