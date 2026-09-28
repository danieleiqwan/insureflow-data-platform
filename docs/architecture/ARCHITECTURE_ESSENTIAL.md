# InsureFlow — Architecture (Essential)

One-page summary for quick orientation. Full detail: [`ARCHITECTURE.md`](./ARCHITECTURE.md).

**Current phase: 1 — Project Foundation.** Only Phase 1 items are implemented. Everything else is planned.

## What it is
Portfolio Data Engineering project (`insureflow-data-platform`, path: `C:\Users\User\Projects\insureflow-data-platform`): Malaysian healthcare data + synthetic insurance data → medallion pipeline → PostgreSQL warehouse → Power BI.

## Target flow
Data Sources → Ingestion → Bronze → Silver → Data Quality → Gold → PostgreSQL → Power BI

| Stage | Status |
|---|---|
| Sources (customer generator) | Phase 1 |
| Postgres container + base schema | Phase 1 |
| Ingestion, Bronze, Silver, DQ, Gold, Power BI | Planned |
| dbt, Airflow, MinIO, incremental, monitoring, cloud | Planned / tentative |

## Phase 1 stack
Python 3.12+ (`venv`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`) · pandas · Faker · python-dotenv · psycopg2-binary · pytest · PostgreSQL 16 (pinned, Docker Compose, default host port 5433) · Git

## Phase 1 data model
`customers` → `policies` → `claims` → `payments` (each links to the previous via FK with `ON DELETE RESTRICT`, see ADR-009).
- IDs are prefixed strings: `C000001`, `P0000001`, `CL0000001`, `PM0000001`.
- Money is `NUMERIC(12,2)`. `created_at` is `TIMESTAMPTZ DEFAULT now()`.
- `CHECK` constraints on gender/status/type columns; `end_date >= start_date`; non-negative amounts and `approved_amount <= claim_amount`.
- Foreign keys use `ON DELETE RESTRICT` to prevent accidental cascading deletion of parent records.
- `claims.facility_id` is a plain column until real facility data arrives.
- Policies, claims, payments are **empty tables** in Phase 1.

## Synthetic data rules
- One fixed seed for `random` and Faker; **no wall-clock fields**; same seed → byte-identical CSV.
- Curated Malay/Chinese/Indian name lists, ~30 Malaysian occupations, 13 states + 3 Federal Territories, age 18–65.
- Output: `data/raw/customers.csv` (1,000 rows, tracked in Git).

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
