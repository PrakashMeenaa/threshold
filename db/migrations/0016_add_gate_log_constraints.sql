BEGIN;

ALTER TABLE gate_log ADD CONSTRAINT gate_log_category_check CHECK (category IN (
    'chest_pain_cardiac',
    'breathing_distress',
    'stroke_symptoms',
    'severe_bleeding_trauma',
    'self_harm_crisis',
    'medical_advice_request'
));

ALTER TABLE gate_log ADD CONSTRAINT gate_log_action_check CHECK (action IN (
    'escalated',
    'deflected'
));

COMMIT;
