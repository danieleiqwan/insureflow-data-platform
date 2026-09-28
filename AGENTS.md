# AGENTS.md — InsureFlow

Instructions for any AI coding agent working in this repository. Read this file fully before making changes.

## Project

**InsureFlow — Insurance Data Platform** is a portfolio Data Engineering project processing Malaysian healthcare data and synthetic insurance data through a medallion pipeline into a PostgreSQL warehouse and Power BI.

Target flow: Data Sources → Ingestion → Bronze → Silver → Data Quality → Gold → PostgreSQL → Power BI.

## Read first

1. `docs/architecture/ARCHITECTURE_ESSENTIAL.md` — quick orientation (start here)
2. `docs/PRD.md` — requirements, roadmap, definition of done
3. `docs/architecture/ARCHITECTURE.md` — full detail, only when you need it

## Current phase

> **CURRENT PHASE: 1 — Project Foundation**

### Phase rules (strict)

- Work **only** on the current phase.
- Do **not** implement Bronze/Silver/Gold pipelines, dbt, Airflow, Power BI, MinIO, Azure, or Databricks.
- Do **not** insert generated data into PostgreSQL. The CSV is enough for Phase 1.
- Do **not** start the next phase automatically. When the phase is complete, deliver the final report and **stop**.
- If a task seems to require future-phase work, stop and ask.
- Never describe planned work as implemented in the README or docs.

## Setup commands

```bash
# Python environment (Python 3.12+)
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Configuration
cp .env.example .env               # then set a local password; never commit .env

# Database
docker compose up -d
docker compose ps                  # wait for "healthy"
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"

# Reset database from scratch (destroys data)
docker compose down -v && docker compose up -d

# Generate data
python src/generation/generate_customers.py
```

## Repository map

| Path | Purpose |
|---|---|
| `src/generation/` | Synthetic data generators |
| `src/ingestion/`, `src/transformation/`, `src/quality/` | Empty until their phases; keep `.gitkeep` |
| `sql/` | From-scratch DDL scripts |
| `data/raw/` | Generated/ingested files (`customers.csv` is tracked) |
| `data/processed/`, `data/sample/` | Future outputs / small samples |
| `tests/` | Automated checks |
| `docs/` | PRD and architecture docs |

## Code standards

- Clean, readable Python 3.12+. Small functions, not one giant script.
- Type hints where they help. Docstrings on important functions.
- Clear variable names. No unnecessary abstraction, classes, or frameworks.
- Entry points use `if __name__ == "__main__":` and are runnable from the repo root.
- Paths built with `pathlib`, resolved relative to the repo, never hardcoded absolute paths.
- Dependencies limited to: `pandas`, `Faker`, `python-dotenv`, `psycopg2-binary` (plus `pytest` only if tests are added). **Ask before adding anything else.** Pin versions in `requirements.txt`.

## Data generation rules

- Seed **both** `random` and Faker with one fixed seed.
- **No wall-clock time** in generated fields (no `now()`, `datetime.today()`). Derive `created_at` from the seed and a fixed reference date.
- Same seed must give a byte-identical CSV. Verify by running twice and comparing checksums.
- Customer IDs: `C000001` … `C001000`, unique, sequential.
- Use curated lists for Malay/Chinese/Indian names and ~30 Malaysian occupations. Do not use Faker's default `job()`.
- States: 13 states + 3 Federal Territories. Age 18–65 at a fixed reference date. Gender: `Male` / `Female`.
- Data must be clearly synthetic. Never use real people's information.

## Database rules

- Money: `NUMERIC(12,2)`. Never floats.
- `created_at`: `TIMESTAMPTZ NOT NULL DEFAULT now()`.
- `NOT NULL` where the value must exist; `CHECK` constraints for enumerated values.
- `CHECK (end_date >= start_date)` on policies; `CHECK (approved_amount <= claim_amount)` on claims.
- Index every foreign-key column.
- Name constraints explicitly. Use lowercase snake_case.
- Scripts in `sql/` must run cleanly on an empty database and be safe to re-run from scratch.
- `claims.facility_id` is a plain column for now (no FK).

## Docker rules

- Pin the Postgres image to a specific major version. Never `latest`.
- Container name `insureflow-postgres`. Named volume. `restart: unless-stopped`. `pg_isready` health check.
- Bind the port to `127.0.0.1:${POSTGRES_PORT}:5432`.
- All credentials come from environment variables. No defaults containing real-looking secrets.

## Security and Git

- Never commit `.env`, credentials, or real personal data.
- `.env.example` contains placeholders only (e.g. `change_me`).
- `.gitignore` must cover `.venv/`, `venv/`, `.env`, `__pycache__/`, `.pytest_cache/`, `.DS_Store`, IDE files, and large generated data, while **keeping** `data/raw/customers.csv` via a negation rule.
- Small, focused commits with clear messages. Do not push unless asked.

## Verification (do not just claim; run and show output)

Before declaring Phase 1 complete, run and show evidence for:

1. `docker compose up -d` succeeds; container is healthy.
2. PostgreSQL is reachable.
3. All four tables exist with correct keys and constraints (`\d customers` etc.).
4. Virtual environment works; dependencies install.
5. Generator runs successfully.
6. `customers.csv` has exactly 1,000 data rows.
7. Customer IDs are unique.
8. Generator run twice yields identical checksums.
9. `git ls-files` contains no `.env` or credentials.
10. Directory tree matches the specification.

If something fails, **fix it**. Do not just report the failure.

## Final report format (end of every phase)

1. Concise summary of what was implemented
2. Final project structure
3. Files created/modified
4. Commands the user must run manually
5. Verification results
6. Issues and assumptions

Then **stop** and wait for instructions.

## Docs maintenance

If a change alters the architecture, schema, conventions, or phase status, update `ARCHITECTURE.md`, `ARCHITECTURE_ESSENTIAL.md`, and this file in the same change.

## When uncertain

Ask, or state the assumption explicitly in the final report. Do not guess silently, and do not expand scope to be helpful.
