# Claude Code Setup & Governance

## Planning Process

Initial architecture dependency graphing was conducted prior to CLI execution to isolate the two blocking auto-fail conditions — tenant isolation and the safety gate — and sequence every other phase around them. These constraints were formalized into `PLAN.md`, which functions as the agent's persistent source of truth across sessions.

Phase 1's opening prompt set the working pattern for the rest of the project: rather than asking for the full data model in one shot, it was split into small, independently reviewable steps — base schema migrations only for the first step, RLS policies explicitly deferred to a second step "so I can review the diff cleanly," and the seed script deferred further still. The agent was told to stop and show proposed SQL before writing anything to disk. This incremental-diff discipline (propose → review → write, one concern at a time) held for the rest of the project and is the reason `DECISIONS.md` reads as a sequence of individually-justified steps rather than a handful of large, hard-to-review dumps.

## Guardrails Strategy (`CLAUDE.md`)

The core architectural principle enforced is deterministic boundary design: the LLM is restricted strictly to intent and slot extraction, and never dictates control flow or executes state mutations directly. Database writes are contained within standard, testable Python functions.

The Row-Level Security bypass risk (a superuser or table-owner role silently ignoring RLS policies) is documented directly in the agent's context, rather than assumed to be common knowledge, since it's the single failure mode most likely to auto-fail the project if missed. A "stop and flag" requirement applies specifically to applied migrations and RLS policy definitions — anything the agent could otherwise "fix" silently in a way that's hard to notice later.

## Permissions & Governance

Agent autonomy is governed by `.claude/settings.json`, using `defaultMode: "default"` — every tool call outside an explicit allowlist requires manual approval.

- **Allowlisted (read/test):** `uv *`, `pytest *`, `git status`, `git diff`.
- **Gated (write/mutate):** database migrations, state-mutating scripts, and git pushes all require manual authorization.

Before committing this configuration, an earlier draft (produced with help from a second AI tool used to sanity-check the approach) specified blocking the shell tool outright rather than gating it. That would have prevented the agent from running migrations, tests, or `uv`/`docker` commands at all — a full block, not an approval gate. I verified the actual configuration options against the official Claude Code documentation before applying anything, and used the scoped-allowlist approach above instead.

## Model Selection

Claude Code ran on Claude Sonnet 5 under the Pro plan by default, confirmed via the CLI banner at startup, for the entire project through Phase 9. No manual model override was made at any point — if a future task needs a different model, that choice and the reasoning behind it belongs here when it happens.

## Context Management

State is centralized in `PLAN.md`. `CLAUDE.md` instructs the agent to read `PLAN.md` at the start of every session, so orientation on current phase, status, and constraints doesn't depend on volatile conversation history surviving a `/clear` or a new session.

## Verification Protocol

All generated SQL — specifically RLS `USING` and `WITH CHECK` clauses — is reviewed manually before being applied. A passing test suite is not treated as sufficient proof of tenant isolation unless the tests run against the unprivileged runtime role, not the bootstrap/superuser role.

The same "don't trust the first result" discipline applies to tooling outside the agent itself. Two consecutive pre-commit hook verification attempts produced false negatives: first with AWS's well-known documentation-placeholder access key (`AKIAIOSFODNN7EXAMPLE`), then with a structurally-correct but sequential fake GitHub token. Rather than assume the hook was broken, I isolated the variable — testing both values with `gitleaks detect --no-git` outside any git context, and cross-checking against an older gitleaks release (8.16.0), which caught both values without issue. The consistent negative was specific to the newer version's own default ruleset, which deprioritizes sequential or otherwise synthetic-looking strings to cut false-positive noise — both of my test values happened to fall into that category by coincidence, not because the hook was misconfigured. Verified working using a cryptographically random fake token instead, which is now my standard for any future scanner test.

No subagents or custom slash commands were used through Phase 6 — a flat, single-session workflow was sufficient while the agent was the one writing each piece of code and already had full context on it. That changed starting with the Phase 7 audit: reviewing six completed phases at once in a single thread would have meant reading most of the codebase a second time just to hold it in context for review, so the audit was split across parallel forked subagents (each inheriting the full conversation, each scoped to a different slice — repo skeleton/RLS/migrations; webhook/agents; the dashboard), with findings synthesized back in the main thread. The same pattern was used again for the Phase 8 audit. This was a genuine trade-off, not a default: forking cost more total tool calls than a single serial pass would have, in exchange for keeping the main thread's context free for synthesis rather than re-reading. One of those forks caught its own instruction wrong mid-task and corrected before reporting — see `agent-logs/INDEX.md`.

The first major correction caught during manual code review in Phase 1 came after RLS design was already underway: `UNIQUE (clinic_id, id)` plus per-table RLS policies scope a *session* to one clinic, but do nothing to stop a row's own foreign keys from pointing at a *different* clinic's parent — an `appointments` row with `clinic_id` Sunrise could still carry a `doctor_id` belonging to City Dental, and RLS would never catch it, since RLS filters which rows a session can see, not whether a row's own columns are internally consistent with each other. This wasn't caught by the agent; it was caught in manual review of the RLS design and fed back as an explicit instruction to add migration `0011_add_tenant_composite_fks.sql`, converting every cross-table reference into a composite foreign key `(child_id, clinic_id) REFERENCES parent (id, clinic_id)` so the database itself rejects a cross-tenant reference at insert time. It's a good example of why "the RLS tests pass" was never treated as equivalent to "tenant isolation is proven" for the rest of the project — RLS and referential integrity are different guarantees, and this gap sits exactly in the space between them.

CI/CD interactions in Phase 8 were driven by actually running things, not by writing configuration that looked plausible. Two real bugs surfaced only when a Docker build or a clean-room test was actually executed: `apps/dashboard/package-lock.json` had drifted out of sync with `package.json` in a way that passed local `npm install` but failed `npm ci` in the pinned `node:22-slim` image (traced to the lockfile having been regenerated on host Node versions that didn't match what Docker/CI actually target); and the API container's `uv run uvicorn ...` startup command was silently re-syncing and pulling dev dependencies over the network on every container start, caught by reading the container's own startup logs rather than trusting that a successful `docker compose up` meant startup was inert. Before pinning any GitHub Action version, each one was checked against its actual current release rather than written from training-data memory — `gitleaks/gitleaks-action@v2`, the version that would have been the default guess, stops working entirely the day after this work was done, because it still runs on a Node version GitHub was retiring. The Phase 8 acceptance bar itself (a fresh clone to a working conversation in under 15 minutes) was measured with a real clean-room run — a scratch copy of the tree, every local container torn down first, the README's own documented commands executed verbatim — rather than asserted as met by design.
