# Claude Code Setup & Governance

## Planning Process

Initial architecture dependency graphing was conducted prior to CLI execution to isolate the two blocking auto-fail conditions — tenant isolation and the safety gate — and sequence every other phase around them. These constraints were formalized into `PLAN.md`, which functions as the agent's persistent source of truth across sessions.

[TODO: Note the outcome of Phase 1's initial CLI planning here.]

## Guardrails Strategy (`CLAUDE.md`)

The core architectural principle enforced is deterministic boundary design: the LLM is restricted strictly to intent and slot extraction, and never dictates control flow or executes state mutations directly. Database writes are contained within standard, testable Python functions.

The Row-Level Security bypass risk (a superuser or table-owner role silently ignoring RLS policies) is documented directly in the agent's context, rather than assumed to be common knowledge, since it's the single failure mode most likely to auto-fail the project if missed. A "stop and flag" requirement applies specifically to applied migrations and RLS policy definitions — anything the agent could otherwise "fix" silently in a way that's hard to notice later.

## Permissions & Governance

Agent autonomy is governed by `.claude/settings.json`, using `defaultMode: "default"` — every tool call outside an explicit allowlist requires manual approval.

- **Allowlisted (read/test):** `uv *`, `pytest *`, `git status`, `git diff`.
- **Gated (write/mutate):** database migrations, state-mutating scripts, and git pushes all require manual authorization.

Before committing this configuration, an earlier draft (produced with help from a second AI tool used to sanity-check the approach) specified blocking the shell tool outright rather than gating it. That would have prevented the agent from running migrations, tests, or `uv`/`docker` commands at all — a full block, not an approval gate. I verified the actual configuration options against the official Claude Code documentation before applying anything, and used the scoped-allowlist approach above instead.

## Model Selection

Claude Code runs on Claude Sonnet 5 under the Pro plan by default, confirmed via the CLI banner at startup. No manual model override has been made at this stage; if a task later needs a different model, that choice and the reasoning behind it will be recorded here when it happens.

## Context Management

State is centralized in `PLAN.md`. `CLAUDE.md` instructs the agent to read `PLAN.md` at the start of every session, so orientation on current phase, status, and constraints doesn't depend on volatile conversation history surviving a `/clear` or a new session.

## Verification Protocol

All generated SQL — specifically RLS `USING` and `WITH CHECK` clauses — is reviewed manually before being applied. A passing test suite is not treated as sufficient proof of tenant isolation unless the tests run against the unprivileged runtime role, not the bootstrap/superuser role.

The same "don't trust the first result" discipline applies to tooling outside the agent itself. Two consecutive pre-commit hook verification attempts produced false negatives: first with AWS's well-known documentation-placeholder access key (`AKIAIOSFODNN7EXAMPLE`), then with a structurally-correct but sequential fake GitHub token. Rather than assume the hook was broken, I isolated the variable — testing both values with `gitleaks detect --no-git` outside any git context, and cross-checking against an older gitleaks release (8.16.0), which caught both values without issue. The consistent negative was specific to the newer version's own default ruleset, which deprioritizes sequential or otherwise synthetic-looking strings to cut false-positive noise — both of my test values happened to fall into that category by coincidence, not because the hook was misconfigured. Verified working using a cryptographically random fake token instead, which is now my standard for any future scanner test.

No subagents or custom slash commands have been used at this stage — a flat, single-session workflow has been sufficient for scaffolding. This will be revisited if a later phase benefits from parallelizing independent work.

[TODO: Document the first major correction caught during manual code review in Phase 1.] [TODO: Document CI/CD interactions and test-driven agent behavior in Phase 8.]
