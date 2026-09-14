# threshold: Build Plan & Status

This document serves as the single source of truth for execution state. Order is by dependency: tenant isolation and safety gates are blocking requirements for all subsequent phases.

### Status Tracker
- [x] **Phase 0:** Repo + environment skeleton
- [x] **Phase 1:** Data model, RLS, tenant seeding
- [x] **Phase 2:** Webhook contract + signature verification
- [x] **Phase 3:** Safety gate
- [x] **Phase 4:** Onboarding agent
- [ ] **Phase 5:** Appointment agent
- [ ] **Phase 6:** Clinic dashboard
- [ ] **Phase 7:** Evals + replay harness
- [ ] **Phase 8:** Docker Compose, CI, README, secret scanning
- [ ] **Phase 9:** Writeups + log curation (Ongoing)

---

## Phase 0 — Repo + environment skeleton
**State:** Completed.
**Deliverables:** `apps/` monorepo layout (`apps/api` FastAPI, `apps/dashboard` Next.js); Postgres via Docker Compose (host port 5433, remapped from the default 5432 due to a local conflict); `.env.example`; `CLAUDE.md` guardrails, including an instruction to read `PLAN.md` at the start of every session; `.claude/settings.json` permission policy (default mode requires approval outside an explicit allowlist of read/test commands); `gitleaks` installed with a local pre-commit hook, verified against a non-placeholder fake secret. CI-enforced secret scanning is deferred to Phase 8.

## Phase 1 — Data model, RLS, tenant seeding
**Deliverables:**
- Migrations for: `clinics`, `departments`, `doctors`, `availability_slots`, `patients`, `appointments`, `consents`, `conversation_state`, `gate_log`, `staff_users`.
- Strict RLS policies per clinic-scoped table using `current_setting('app.current_clinic_id', true)`.
- Two-tier database roles: a bootstrap/migration role that owns the schema, and a separate unprivileged runtime role (`NOSUPERUSER`, `NOBYPASSRLS`) that the application actually connects as.
- Python seed script for Sunrise Multi-Speciality and City Dental Care, sourced from `clinics/*.yaml`.
**Constraints:** No fallback permissive policies. Default-deny on missing session variables. Isolation must be proven by an automated test against the runtime role, not the bootstrap role.

## Phase 2 — Webhook contract + signature verification
**Deliverables:** `POST /webhook`, timing-safe HMAC-SHA256 verification on the raw request body, routing by `phone_number_id`, `scripts/simulate.sh`.
**Constraints:** Must handle idempotent redelivery (duplicate webhook payloads must not double-process).

## Phase 3 — Safety gate
**Deliverables:** Deterministic pattern matching for emergency/medical-advice routing, evaluated before any agent dispatch.
**Constraints:** Biased toward over-triggering on ambiguous language. Gate events are logged without retaining phone numbers or message text.

## Phase 4 — Onboarding agent
**Deliverables:** Stateful intent/slot extraction (name → language → consent → department), persisted per clinic+patient.
**Constraints:** Consent recorded with the exact text shown to the patient plus an explicit timestamp. Duplicate webhook deliveries must not double-advance the state machine.

## Phase 5 — Appointment agent
**Deliverables:** Book, reschedule, and cancel operations against real `availability_slots` rows; natural-language date parsing.
**Constraints:** Concurrent booking requests for the same slot must be resolved at the database level (e.g. `SELECT ... FOR UPDATE` or a uniqueness constraint), not by an application-level check-then-write.

## Phase 6 — Clinic dashboard
**Deliverables:** Next.js views for patients, appointments, and consent status, scoped through the same RLS-protected queries used by the agents.
**Constraints:** Must demonstrate IDOR protection at the API layer, in addition to RLS at the database layer.

## Phase 7 — Evals + replay harness
**Deliverables:** 12 total golden-conversation cases — the 5 seeded in the brief, plus 7 authored for this project, at least 4 of which are failure/edge cases rather than additional happy paths — plus a replay harness that runs the full set against the live app and prints a pass/fail report.

## Phase 8 — Docker Compose, CI, README, secret scanning
**Deliverables:** CI pipeline (tests, tenant-isolation proof, and a `gitleaks` scan on every push); a `docker-compose`-based deploy that a stranger can run from the README alone; finished `README.md`.
**Acceptance bar:** a fresh clone gets to a working `simulate.sh` conversation in under 15 minutes, timed on a clean checkout.

## Phase 9 — Writeups + log curation (Ongoing)
**Deliverables:** `docs/architecture.md` and `docs/claude-code-setup.md`, written incrementally as decisions are made rather than reconstructed at the end; `agent-logs/INDEX.md` annotating 3–5 reviewer-relevant moments from the raw session transcripts, including at least one moment where the agent was wrong and it was caught and corrected.