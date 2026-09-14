BEGIN;

ALTER TABLE consents ADD CONSTRAINT consents_consent_type_check CHECK (consent_type IN (
    'onboarding_data_processing'
));

COMMIT;
