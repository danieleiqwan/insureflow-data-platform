# InsureFlow — Architecture (Essential)

One-page summary for quick orientation. Full detail: [`ARCHITECTURE.md`](./ARCHITECTURE.md).

**Current phase: 2B — Synthetic Policies, Claims, and Payments.** Phase 1, Phase 2A, and Phase 2B are implemented. Everything else is planned.

## What it is
Portfolio Data Engineering project (`insureflow-data-platform`, path: `C:\Users\User\Projects\insureflow-data-platform`): Malaysian healthcare data + synthetic insurance data → medallion pipeline → PostgreSQL warehouse → Power BI.

## Target flow
Data Sources → Ingestion → Bronze → Silver → Data Quality → Gold → PostgreSQL → Power BI

| Stage | Status |
|---|---|
| Sources (customer, policy, claim, payment generators; MOH facility data) | Implemented (Phase 1, 2A, 2B) |
| Postgres container + relational schema (5 tables) | Implemented (Phase 1 & 2B) |
| Ingestion, Bronze, Silver, DQ, Gold, Power BI | Planned |
| dbt, Airflow, MinIO, incremental, monitoring, cloud | Planned / tentative |

## Stack
Python 3.12+ (`venv`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`) · pandas · Faker · python-dotenv · psycopg2-binary · pytest · PostgreSQL 16 (pinned, Docker Compose, default host port 5433) · Git

## Data model
- `facilities` (MOH `KOD_FASILITI` PK)
- `customers` → `policies` → `claims` → `payments` (each links via FK with `ON DELETE RESTRICT`, see ADR-009).
- `claims.facility_id` is a `NOT NULL` FK to `facilities.facility_id` (see ADR-010, supersedes ADR-007).
- IDs are prefixed strings: `C000001`, `P0000001`, `CL0000001`, `PM0000001`.
- Money is `NUMERIC(12,2)`. `created_at` is `TIMESTAMPTZ DEFAULT now()`.
- `CHECK` constraints on status/type columns; `end_date >= start_date`; non-negative amounts and `approved_amount <= claim_amount`.
- Foreign keys use `ON DELETE RESTRICT` and are indexed.
- Tables in PostgreSQL are created via `sql/init.sql`.

## Synthetic data rules
- Fixed base seed + generator offsets for independent `random.Random` instances; **no wall-clock fields**; byte-identical CSVs.
- Curated Malay/Chinese/Indian name lists, ~30 Malaysian occupations, 13 states + 3 Federal Territories, age 18–65.
- Real public facilities sampled from `data/raw/facilities_master.csv` with state preference.
- Outputs in `data/raw/`: `customers.csv` (1,000), `policies.csv` (1,379), `claims.csv` (1,059), `payments.csv` (814). Tracked in Git.

## Where things go
| Path | Purpose |
|---|---|
| `src/generation/` | Synthetic data generators (Phase 1 uses this only) |
| `src/ingestion/`, `transformation/`, `quality/` | Empty until their phases |
| `sql/` | From-scratch DDL (`init.sql`), mounted into Postgres init dir |
| `data/raw/` | Generated/ingested source files |
| `tests/` | Automated unit/property checks (`pytest`) |
| `requirements.txt` | Runtime dependencies pinned |
| `requirements-dev.txt` | Dev/test dependencies (`pytest`) |
| `pytest.ini` | Pytest config (`pythonpath = .`) |

## Docker / Postgres
Pinned image (`postgres:16-alpine`), container `insureflow-postgres`, named volume, `restart: unless-stopped`, `pg_isready` health check, host port bound to `127.0.0.1:5433:5432` (default port 5433 via `POSTGRES_PORT:-5433`). Init scripts run only on an empty volume; reset in PowerShell with `docker compose down -v; docker compose up -d`.

## Non-negotiables
1. No secrets in Git; config via `.env` (only `.env.example` committed).
2. No real personal data; all data clearly synthetic.
3. Minimal dependencies; no over-engineering.
4. Do not build future phases unless explicitly asked.
5. Update both architecture docs when the architecture changes.
