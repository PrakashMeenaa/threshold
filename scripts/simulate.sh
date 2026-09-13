#!/usr/bin/env bash
set -euo pipefail

WEBHOOK_URL="${WEBHOOK_URL:-http://localhost:8000/webhook}"

send_webhook() {
    local body
    body=$(printf '{"phone_number_id":"%s","from":"%s","text":"%s","timestamp":%s}' "$1" "$2" "$3" "$4")
    local signature
    signature=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | sed 's/^.* //')
    curl -sS -X POST "$WEBHOOK_URL" -H "Content-Type: application/json" -H "X-Hub-Signature-256: sha256=$signature" -d "$body"
    echo
}

if [[ -z "${WEBHOOK_SECRET:-}" ]]; then
    echo "WEBHOOK_SECRET must be set" >&2
    exit 1
fi

send_webhook "sunrise-main" "+919876500001" "Hi, I would like to book an appointment" "$(date +%s)"
send_webhook "city-dental-main" "+919876500002" "Hello, do you have any openings this week?" "$(date +%s)"

DUPLICATE_TIMESTAMP="$(date +%s)"
send_webhook "sunrise-main" "+919876500003" "Testing idempotent redelivery" "$DUPLICATE_TIMESTAMP"
send_webhook "sunrise-main" "+919876500003" "Testing idempotent redelivery" "$DUPLICATE_TIMESTAMP"
