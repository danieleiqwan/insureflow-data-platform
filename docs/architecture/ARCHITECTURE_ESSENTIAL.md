# InsureFlow — Architecture (Essential)

One-page summary for quick orientation. Full detail: [`ARCHITECTURE.md`](./ARCHITECTURE.md).

**Current phase: 3 — Ingestion + Bronze.** Phase 1, Phase 2A, Phase 2B, and Phase 3 are implemented. Everything else is planned.

## What it is
Portfolio Data Engineering project (`insureflow-data-platform`, path: `C:\Users\User\Projects\insureflow-data-platform`): Malaysian healthcare data + synthetic insurance data → medallion pipeline → PostgreSQL warehouse → Power BI.

## Target flow
Data Sources → Ingestion → Bronze → Silver → Data Quality → Gold → PostgreSQL → Power BI

| Stage | Status |
|---|---|
| Sources (customer, policy, claim, payment generators; MOH facility data) | Implemented (Phase 1, 2A, 2B) |
| Ingestion & Bronze layer (`bronze` schema, audit logging, COPY transactions) | Implemented (Phase 3) |
| Postgres container + relational schema (5 public tables) | Implemented (Phase 1 & 2B) |
| Silver, DQ, Gold, Power BI | Planned |
| dbt, Airflow, MinIO, incremental, monitoring, cloud | Planned / tentative |

## Stack
Python 3.12+ (`venv`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`) · pandas · Faker · python-dotenv · psycopg2-binary · pytest · PostgreSQL 16 (pinned, Docker Compose, default host port 5433) · Git

## Data model & Schemas
- **Warehouse Schemas:** PostgreSQL uses separate schemas for medallion layers. `bronze` is implemented in Phase 3; `silver` and `gold` will be added in Phases 4 and 5 (see ADR-013).
- **Bronze schema (`bronze.*`):** Tables `customers`, `policies`, `claims`, `payments`, `facilities_master`. All source business fields are `TEXT`. Metadata: `_batch_id` (UUID), `_source_file` (TEXT), `_source_row_number` (INT), `_ingested_at` (TIMESTAMPTZ NOT NULL DEFAULT now()). Audit log: `bronze.ingestion_log`. Append-only (see ADR-012).
- **Public relational schema (`public.*`):** Source relational baseline (`facilities`, `customers`, `policies`, `claims`, `payments`). PKs, FKs with `ON DELETE RESTRICT` (ADR-009, ADR-010), `NUMERIC(12,2)` money, `CHECK` constraints. Left unpopulated in Phase 3 as candidate Silver contract for Phase 4.

## Synthetic data rules
- Fixed base seed + generator offsets for independent `random.Random` instances; **no wall-clock fields**; byte-identical CSVs.
- Curated Malay/Chinese/Indian name lists, ~30 Malaysian occupations, 13 states + 3 Federal Territories, age 18–65.
- Real public facilities sampled from `data/raw/facilities_master.csv` with state preference.
- Outputs in `data/raw/`: `customers.csv` (1,000), `policies.csv` (1,379), `claims.csv` (423), `payments.csv` (364), `facilities_master.csv` (5,160).

## Where things go
| Path | Purpose |
|---|---|
| `src/generation/` | Synthetic data generators (Phase 1 & 2B) |
| `src/ingestion/` | Source acquisition (`download_sources.py`) and Bronze COPY loader (`ingest_bronze.py`) |
| `src/transformation/`, `quality/` | Empty until their phases |
| `sql/` | From-scratch DDL (`bronze.sql`, `init.sql`), mounted into Postgres init dir |
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
