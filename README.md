# threshold

A multi-tenant clinic conversational-agent platform. Patients message a simulated
WhatsApp webhook; every message crosses a deterministic safety gate before either
of two stateful LLM-backed agents (onboarding, appointment booking) touches it.
State lives in Postgres behind Row-Level Security, and clinic staff get a
Next.js dashboard scoped to their own clinic by the same RLS policies.

- **`apps/api`** — FastAPI webhook + onboarding/appointment agents (Python, [uv](https://docs.astral.sh/uv/))
- **`apps/dashboard`** — Next.js staff dashboard (TypeScript, App Router)
- **`db/migrations`** — forward-only SQL migrations (schema, RLS policies, roles)
- **`clinics/*.yaml`** — per-clinic configuration, seeded by `scripts/seed.py`
- **`evals/`** — 12 golden-conversation cases + an offline replay harness (see [`docs/architecture.md`](docs/architecture.md))
- **`docs/architecture.md`** — how the system actually works, phase by phase

For the full reasoning behind these decisions — including two rounds of
adversarial self-review per phase and the bugs each one caught — see
[`docs/architecture.md`](docs/architecture.md) and [`PLAN.md`](PLAN.md).

## Prerequisites

- [Docker](https://www.docker.com/) with Compose v2 (`docker compose version`)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.12 is pinned via `.python-version`; uv installs it automatically)
- Node.js 22+ and npm (only needed if you want to run the dashboard outside Docker, or its test suite)
- `psql` (the PostgreSQL client) — used once to apply migrations
- `openssl` (used by `scripts/simulate.sh` to sign webhook payloads, and to generate secrets below)
- An [Anthropic API key](https://console.anthropic.com/) — required for the agents to actually respond; without one, requests still flow correctly end-to-end but agent replies fall back to a generic "I had trouble understanding that" message

## Quickstart

```bash
git clone <this-repo-url> threshold
cd threshold
```

Generate `.env` — this fills in real random secrets for everything except your
Anthropic key, which you fill in by hand afterward:

```bash
cat > .env <<EOF
ANTHROPIC_API_KEY=sk-ant-your-real-key-here
THRESHOLD_API_PASSWORD=$(openssl rand -hex 32)
WEBHOOK_SECRET=$(openssl rand -hex 32)
SESSION_SECRET=$(openssl rand -hex 32)
STAFF_SEED_PASSWORD=$(openssl rand -hex 16)
EOF
```

Then edit `.env` and replace `ANTHROPIC_API_KEY` with a real key from
https://console.anthropic.com/ (`.env.example` documents the full set of
variables if you'd rather fill it in manually).

Then bring the whole stack up:

```bash
docker compose up -d db --wait          # Postgres, waits for healthcheck

cd apps/api && uv sync && cd ../..      # installs Python deps (needed for scripts/seed.py)

set -a; source .env; set +a             # export the values you just filled in
./scripts/migrate.sh                    # applies all migrations, sets the runtime role's password
uv run --project apps/api python scripts/seed.py   # seeds the two demo clinics

docker compose up -d --build api dashboard   # builds and starts the API (:8000) and dashboard (:3000)

./scripts/simulate.sh                   # sends a few real webhook conversations
```

That's the whole path from a clean clone to a working conversation — comfortably
under 15 minutes even including image builds. `simulate.sh` prints the raw JSON
replies from the API; the last two calls are identical on purpose, to demonstrate
idempotent webhook redelivery (`{"status":"ok","detail":"already_processed"}` on
the second one).

Open the dashboard at **http://localhost:3000/login** and sign in with one of the
seeded staff emails from `clinics/*.yaml` (e.g. `staff@sunrise-multi-speciality.example`)
and the `STAFF_SEED_PASSWORD` value from your `.env`.

### Running without Docker for the API/dashboard

`docker compose up -d db --wait` is enough on its own — you can run the API and
dashboard directly on the host instead of building images, which is faster for
active development:

```bash
# API
cd apps/api && uv run uvicorn main:app --reload --port 8000

# Dashboard (in another terminal, from the repo root)
cd apps/dashboard && npm ci && npm run dev
```

Both read the same `.env` values; `PGHOST`/`PGPORT` default to `localhost:5433`
(the port Postgres is mapped to on the host) when not running inside the Compose
network.

Use `npm ci`, not `npm install`, for this: `ci` installs exactly what
`package-lock.json` already pins and never rewrites it, while `install` can
silently regenerate the lockfile differently depending on your local Node/npm
version — `apps/dashboard/package.json` pins Node 22.x via `engines`
(`apps/dashboard/.nvmrc` too, if you use `nvm`) specifically because this
already happened once during development and broke `npm ci` inside the
Dockerfile/CI, which both target `node:22-slim`.

## Running the tests

```bash
# Python: unit + integration tests, including the RLS/tenant-isolation proof
# run against the unprivileged runtime role, not the bootstrap role
uv run --project apps/api pytest tests/ -q

# The offline eval harness — 12 golden-conversation cases replayed against the
# real state machines and a real (transaction-rolled-back) Postgres connection,
# with a mocked LLM extractor so it needs no API key and runs in seconds
uv run --project apps/api python evals/run_evals.py

# Dashboard: unit tests + the IDOR/tenant-isolation proof
cd apps/dashboard && npm run test
```

All three require `docker compose up -d db --wait` and a migrated, seeded
database (see Quickstart above). CI (`.github/workflows/ci.yml`) runs all of
this, plus a `gitleaks` secret scan, on every push and pull request.

## Security notes

- The API and dashboard both connect as `threshold_api_user`, a Postgres role
  created with `NOSUPERUSER NOBYPASSRLS` — never the bootstrap/migration role.
  Every tenant-scoped table enforces Row-Level Security keyed on
  `current_setting('app.current_clinic_id', true)`, with no permissive fallback
  policy; a missing session variable means zero rows, not an error and not
  every clinic's rows.
- IDOR protection is enforced at the application layer too, not just RLS — every
  fetch-by-ID query explicitly filters on the caller's own `clinic_id`.
- `gitleaks` runs as a local pre-commit hook (`.git/hooks/pre-commit`) and again
  in CI on every push, scanning full git history.
- LLM output never directly drives control flow or a database query: every
  extracted value is validated in plain Python against a real, closed set
  (configured languages/departments, or a schema-constrained enum) before it's
  trusted. See `CLAUDE.md` and `docs/architecture.md` for the specifics.

## Project layout

```
apps/api/             FastAPI webhook, safety gate, onboarding + appointment agents
apps/dashboard/       Next.js staff dashboard
db/migrations/        Forward-only SQL migrations
clinics/*.yaml        Per-clinic config (departments, doctors, copy)
evals/                Golden-conversation cases + offline replay harness
scripts/              migrate.sh, seed.py, simulate.sh
tests/                Python test suite (pytest)
docs/architecture.md  How the system works, phase by phase
PLAN.md               Phase-by-phase execution status and constraints
```
