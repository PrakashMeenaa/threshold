# threshold — Claude Code project context

## What this is
Multi-tenant clinic conversational-agent platform. Patients message a simulated
WhatsApp webhook, hit a deterministic safety gate, then one of two stateful agents
(onboarding, appointment booking), backed by Postgres. Staff use a Next.js
dashboard scoped by clinic via Postgres RLS.

## Structural rule that overrides convenience
LLM calls are read-only with respect to control flow — they extract intent/slots
from text. They never decide what happens next and never touch the database
directly. State transitions and every DB write happen in plain, testable Python
functions. If a change would let an LLM's output directly branch application
logic or issue a query, stop and flag it before writing it.

## Tenant isolation
Every tenant-scoped table carries clinic_id + an RLS policy gated on
current_setting('app.current_clinic_id', true). The app's DB role must never be
superuser or table owner — that silently bypasses RLS and the failure won't show
up until someone actually tests cross-tenant access. Confirm this explicitly if
the DB user is ever regenerated.

## Safety gate
Deterministic pattern-matching module, called before agent dispatch — never
inside a prompt. Default to over-triggering on ambiguous chest-pain-adjacent
language: false positives are the acceptable failure mode here, false negatives
are not.

## Touch only with an explicit flag first
- Applied migrations (write a new one, never edit history)
- RLS policies (any weakening needs a flagged conversation, not a silent edit)
- clinics/*.yaml shape (schema changes ripple into seeding logic)

## Testing
A change without a test in the same commit isn't done.
