# InsureFlow — Architecture (Essential)

One-page summary for quick orientation. Full detail: [`ARCHITECTURE.md`](./ARCHITECTURE.md).

**Current phase: 5 — Gold Layer (Dimensional Model).** Phase 1, Phase 2A, Phase 2B, Phase 3, Phase 4A, Phase 4B, and Phase 5 are implemented. Everything else is planned.

## What it is
Portfolio Data Engineering project (`insureflow-data-platform`, path: `C:\Users\User\Projects\insureflow-data-platform`): Malaysian healthcare data + synthetic insurance data → medallion pipeline → PostgreSQL warehouse → Power BI.

## Target flow
Data Sources → Ingestion → Bronze → Silver → Data Quality → Gold → PostgreSQL → Power BI

| Stage | Status |
|---|---|
| Sources (customer, policy, claim, payment generators; MOH facility data) | Implemented (Phase 1, 2A, 2B) |
| Ingestion & Bronze layer (`bronze` schema, audit logging, COPY transactions) | Implemented (Phase 3) |
| Silver layer (`silver` schema, typed transform, rejected_rows, public.* retired) | Implemented (Phase 4A) |
| Data Quality rule framework, quarantine, reporting | Implemented (Phase 4B) |
| Gold layer (`gold` schema, star schema, `load_gold.py`) | Implemented (Phase 5) |
| Power BI | Planned |
| dbt, Airflow, MinIO, incremental, monitoring, cloud | Planned / tentative |

## Stack
Python 3.12+ (`venv`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`) · pandas · Faker · python-dotenv · psycopg2-binary · pytest · PostgreSQL 16 (pinned, Docker Compose, default host port 5433) · Git

## Data model & Schemas
- **Warehouse Schemas:** PostgreSQL uses separate schemas for medallion layers. `bronze` (Phase 3), `silver` (Phase 4A), and `gold` (Phase 5) are implemented; the `public` schema has been retired (ADR-014, superseding ADR-006 and ADR-013).
- **Gold schema (`gold.*`):** Star schema dimensional model — `dim_date`, `dim_customer`, `dim_policy`, `dim_facility`, `fact_claims`, `fact_payments`. Natural keys from Silver used as PKs (ADR-016). `dim_date.date_key` is `DATE` type. `fact_claims.customer_id` is denormalized from policy for easier BI filtering. Loaded by `src/transformation/load_gold.py` (full refresh, single transaction). DDL in `sql/gold.sql`.
- **Bronze schema (`bronze.*`):** Tables `customers`, `policies`, `claims`, `payments`, `facilities_master`. All source business fields are `TEXT`. Metadata: `_batch_id` (UUID), `_source_file` (TEXT), `_source_row_number` (INT), `_ingested_at` (TIMESTAMPTZ NOT NULL DEFAULT now()). Audit log: `bronze.ingestion_log`. Append-only with pre-load header schema validation (see ADR-012).
- **Silver schema (`silver.*`):** Typed, constrained relational tables — `facilities`, `customers`, `policies`, `claims`, `payments` — and `rejected_rows`. PKs, FKs with `ON DELETE RESTRICT` (ADR-009, ADR-010), `NUMERIC(12,2)` money, `CHECK` constraints. Populated via `src/transformation/transform_silver.py` (full refresh, latest-batch-only, row-level reject logging). `silver.facilities.facility_category` and `subsector` are `NOT NULL` without hardcoded CHECK (ADR-011). DDL in `sql/silver.sql`.
- **rejected_rows:** `source_table TEXT`, `source_batch_id UUID`, `source_row_number INT`, `reject_reason TEXT`, `raw_row JSONB`, `rejected_at TIMESTAMPTZ`. Populated automatically by `transform_silver.py` for any row that fails type parsing or referential integrity.

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
| `src/transformation/` | Silver transform (`transform_silver.py`) and Gold load (`load_gold.py`) |
| `src/quality/` | Data quality defect injection (`inject_defects.py`), rules (`dq_rules.py`), and runner (`run_dq_checks.py`) (Phase 4B) |
| `sql/` | From-scratch DDL (`init.sql`, `bronze.sql`, `silver.sql`, `gold.sql`), mounted into Postgres init dir |
| `data/raw/` | Generated/ingested source files |
| `data/sample/` | Sample dirty datasets, quarantine files, and DQ reports (Phase 4B) |
| `tests/` | Automated unit/property checks (`pytest`) |
| `requirements.txt` | Runtime dependencies pinned |
| `requirements-dev.txt` | Dev/test dependencies (`pytest`) |
| `pytest.ini` | Pytest config (`pythonpath = .`) |
| `scripts/verify_db_rollback.py` | Constraint verification against `silver.*` using ROLLBACK |
| `docs/ADR-014.md` | ADR for Silver schema and public.* retirement |
| `docs/ADR-015.md` | ADR for DQ rule framework and quarantine strategy |
| `docs/ADR-016.md` | ADR for Gold natural keys and dim_date type choice |

## Docker / Postgres
Pinned image (`postgres:16-alpine`), container `insureflow-postgres`, named volume, `restart: unless-stopped`, `pg_isready` health check, host port bound to `127.0.0.1:5433:5432` (default port 5433 via `POSTGRES_PORT:-5433`). Init scripts run only on an empty volume; reset in PowerShell with `docker compose down -v; docker compose up -d`.

## Non-negotiables
1. No secrets in Git; config via `.env` (only `.env.example` committed).
2. No real personal data; all data clearly synthetic.
3. Minimal dependencies; no over-engineering.
4. Do not build future phases unless explicitly asked.
5. Update both architecture docs when the architecture changes.
