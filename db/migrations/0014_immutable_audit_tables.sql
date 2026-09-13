BEGIN;

REVOKE UPDATE, DELETE ON consents FROM threshold_api_user;
REVOKE UPDATE, DELETE ON gate_log FROM threshold_api_user;

COMMIT;
