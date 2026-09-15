# threshold: Build Plan & Status

This document serves as the single source of truth for execution state. Order is by dependency: tenant isolation and safety gates are blocking requirements for all subsequent phases.

### Status Tracker
- [x] **Phase 0:** Repo + environment skeleton
- [x] **Phase 1:** Data model, RLS, tenant seeding
- [x] **Phase 2:** Webhook contract + signature verification
- [x] **Phase 3:** Safety gate
- [x] **Phase 4:** Onboarding agent
- [x] **Phase 5:** Appointment agent
- [x] **Phase 6:** Clinic dashboard
- [x] **Phase 7:** Evals + replay harness
- [x] **Phase 8:** Docker Compose, CI, README, secret scanning
- [x] **Phase 9:** Writeups + log curation (Ongoing)

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
**State:** Completed.
**Deliverables:** `evals/cases.json` — 12 golden-conversation cases (5 seeded from the brief: happy-path booking, emergency gate, medical-advice gate, abandon/resume, relative-date booking; 7 authored here, 4 of them failure cases: exhausted consent retries, cross-tenant department rejection, out-of-bounds requested time, booking race loss — plus 3 edge cases: explicit consent decline, ambiguous cancel disambiguation, Hindi-language extraction). `evals/run_evals.py` — a standalone async CLI harness that drives `advance_onboarding`/`advance_appointment` directly against a real (transaction-rolled-back) Postgres connection using `MockSlotExtractor`/`MockAppointmentExtractor` in place of the Anthropic client, so it runs fully offline with no LLM calls, and the deterministic `safety_gate.evaluate` directly for the two gate cases. Prints a pass/fail report; verified 12/12 passing with zero DB residue (row counts diffed before/after).
**Constraints:** Each case's DB setup/teardown is isolated in its own transaction that is always rolled back, so repeated runs never accumulate rows or affect the seeded clinic data. `_run_case` catches any unexpected exception per case (not just expected-vs-actual mismatches) and reports it as a `FAIL`, so one broken case can't take down the report for the other 11 — verified with a deliberately-broken 13th case, then reverted. Full design and audit trail in `docs/architecture.md` (tracked) and `DECISIONS.md` (Step 7.1/7.2 — a private working document, not included in this repository).

## Phase 8 — Docker Compose, CI, README, secret scanning
**State:** Completed.
**Deliverables:** `docker-compose.yml` extended with `api`/`dashboard` services (`apps/api/Dockerfile`, `apps/dashboard/Dockerfile`) alongside the existing `db` service, healthcheck-gated startup ordering. `.github/workflows/ci.yml` — three jobs on every push/PR: `gitleaks` (full-history secret scan), `api-tests` (migrate, seed, full pytest suite including the RLS/tenant-isolation proof, offline eval harness), `dashboard-tests` (migrate, seed, `next build`, `vitest`). Finished root `README.md` with a verified quickstart.
**Acceptance bar:** a fresh clone gets to a working `simulate.sh` conversation in under 15 minutes, timed on a clean checkout. **Measured, not assumed:** a genuine clean-room run (fresh directory, fresh Docker volumes, every local container torn down first, README's own commands run verbatim) completed in 21 seconds end to end, excluding first-time base-image pulls. Two real bugs were caught only by this process, not by review. Full account in `docs/architecture.md` (tracked) and `DECISIONS.md` (Step 8.1/8.2/8.3 — a private working document, not included in this repository).

## Phase 9 — Writeups + log curation (Ongoing)
**State:** Deliverables current as of Phase 8. Genuinely ongoing — this phase gets revisited any time a later decision needs recording, not reopened as a discrete unit of work.
**Deliverables:** `docs/architecture.md` and `docs/claude-code-setup.md`, written incrementally as decisions are made rather than reconstructed at the end (`claude-code-setup.md`'s three outstanding TODOs — the Phase 1 planning outcome, the first manual-review correction, and Phase 8's CI/CD account — were closed out here, along with correcting a stale claim that no subagents had been used); `agent-logs/INDEX.md` annotating 5 reviewer-relevant moments from the raw session transcripts (`.claude/projects/.../f9df6fce-*.jsonl`), including two moments where the agent was wrong and it was caught and corrected, plus two distinct prompt-injection attempts and how they were each handled.