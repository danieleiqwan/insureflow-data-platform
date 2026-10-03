# InsureFlow — Insurance Data Platform: Product Requirements Document

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Owner** | Daniel |
| **Current phase** | Phase 4B — Data Quality (Defect Injection + DQ Rules) |
| **Last updated** | 2026-10-03 |

> Status legend used across the docs: **[Implemented]** exists in the repo, **[Planned]** agreed direction but not built, **[Tentative]** idea, may change.

---

## 1. Overview

InsureFlow is a portfolio-grade, end-to-end **Data Engineering** project. It simulates a Malaysian health-insurance data platform by combining **public Malaysian healthcare data** with **synthetic insurance transactions** (customers, policies, claims, payments), and processes them through a medallion-style pipeline into a PostgreSQL warehouse and a Power BI dashboard.

The project is built **phase by phase**. Each phase ends with a stop-and-review gate before the next begins.

## 2. Problem & Motivation

Most student data-engineering portfolios are notebooks that clean one CSV. They do not show the things employers actually hire for: layered pipelines, data quality enforcement, reproducibility, orchestration, warehouse modelling, and clean engineering habits.

InsureFlow exists to demonstrate those skills on a domain (insurance + healthcare) that has natural data-quality problems, relational structure, and analytical questions.

## 3. Goals

1. Demonstrate **data ingestion**, **transformation**, **data quality**, **data warehousing**, **orchestration**, and **analytics** in one coherent project.
2. Be **reproducible**: anyone can clone the repo, run a few commands, and get identical data and schema.
3. Follow **professional engineering practice**: clean Python, no hardcoded secrets, sensible Git hygiene, documented decisions.
4. Stay **simple and maintainable**; add complexity only when a phase needs it.
5. Produce a **presentable portfolio artifact** (README, architecture docs, dashboard) for recruiters and hiring managers.

## 4. Non-Goals

- Not a production system; no real customers, no real PII, no SLAs.
- Not a big-data project; volumes stay laptop-scale unless a later phase deliberately scales up.
- Not a showcase of every tool. Tools are added only when they solve a real problem in the pipeline.
- No real personal data, ever. All customer/insurance data is synthetic and must be clearly synthetic.

## 5. Audience

| Audience | What they need |
|---|---|
| Recruiters / hiring managers | Clear README, architecture diagram, evidence of engineering discipline |
| Technical interviewers | Readable code, sensible design decisions, ability to explain trade-offs |
| The author (learning) | A structure that grows incrementally without rewrites |
| AI coding agents | Unambiguous scope, conventions, and phase boundaries (see `AGENTS.md`) |

## 6. Roadmap

Phase order after Phase 1 is **[Tentative]** and can be re-prioritised. Only Phase 1 is committed.

| Phase | Name | Key deliverables | Status |
|---|---|---|---|
| 1 | Project Foundation | Repo structure, venv, Postgres in Docker, base schema, 1,000-customer generator, README | **[Implemented]** |
| 2A | Facility Data Source Acquisition | Download & profile real Malaysian healthcare facility data (MOH/data.gov.my); evaluate candidate datasets for facility IDs | **[Implemented]** |
| 2B | Synthetic Insurance Data Expansion | Policy, claim, payment generators; link claims to selected facility reference | **[Implemented]** |
| 3 | Ingestion + Bronze | Load raw sources as-is with ingestion metadata into `bronze` schema; COPY runner & audit log | **[Implemented]** |
| 4A | Silver Layer | Typed Silver schema, full-refresh transform, `silver.rejected_rows`, public.* retirement | **[Implemented]** |
| 4B | Data Quality Rule Framework | DQ rules, thresholds, aggregated DQ reports, quarantine CSVs, recall/FP metrics; defect manifest stored as `defect_manifest.csv` | **[Implemented]** |
| 5 | Gold + Warehouse | Dimensional model in PostgreSQL | Tentative |
| 6 | Analytics | Power BI dashboards on Gold | Tentative |
| 7 | Transformation Framework + Orchestration | dbt models/tests, Airflow DAGs | Tentative |
| 8 | Object Storage + Incremental Processing | MinIO/S3-compatible storage, incremental loads | Tentative |
| 9 | Monitoring + CI | Pipeline monitoring, alerts, automated tests in CI | Tentative |
| 10 | Cloud Concepts (optional) | Azure / Databricks equivalents | Tentative |

## 7. Phase 1 — Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-1.1 | Repository follows the agreed directory structure; empty directories are tracked with `.gitkeep`. | Must |
| FR-1.2 | Python 3.12+ virtual environment (`.venv`) with only `pandas`, `Faker`, `python-dotenv`, `psycopg2-binary`. Versions pinned in `requirements.txt`. | Must |
| FR-1.3 | PostgreSQL runs via Docker Compose: pinned image, env-var configuration, named volume, `restart: unless-stopped`, health check, sensible container name, port bound to `127.0.0.1`. | Must |
| FR-1.4 | `.env.example` documents all variables with placeholder values. Real `.env` is git-ignored. | Must |
| FR-1.5 | SQL init script creates `customers`, `policies`, `claims`, `payments` with PKs, FKs, and constraints; schema is recreatable from scratch. | Must |
| FR-1.6 | `src/generation/generate_customers.py` generates exactly 1,000 synthetic Malaysian customers, deterministically, and writes `data/raw/customers.csv`. | Must |
| FR-1.7 | Generator is runnable via `python src/generation/generate_customers.py` and prints a summary (count, output path, sample rows). | Must |
| FR-1.8 | README covers project, objective, current phase, stack, Mermaid architecture, current dataset, future sources. Future work is not claimed as done. | Must |
| FR-1.9 | `.gitignore` covers venvs, `.env`, caches, OS/IDE files, large generated data, but keeps `customers.csv`. | Must |
| FR-1.10 | Initial Git repository with one clean initial commit (no push). | Should |
| FR-1.11 | Small automated checks for the generator (row count, ID uniqueness, determinism). Requires adding `pytest` as a dev dependency. | Should |

## 8. Data Requirements (Phase 1)

### 8.1 Customer dataset

| Field | Type | Rule |
|---|---|---|
| `customer_id` | text | `C000001` … `C001000`, unique, sequential |
| `first_name`, `last_name` | text | Plausible Malaysian names across Malay, Chinese, Indian communities; clearly synthetic |
| `gender` | text | `Male` or `Female` |
| `date_of_birth` | date | Valid date; customer age 18–65 at reference date |
| `state` | text | One of 13 states + 3 Federal Territories |
| `occupation` | text | From a curated list of realistic Malaysian occupations (not Faker's default `job()`) |
| `created_at` | timestamp | Derived from the seed, not from wall-clock time |

### 8.2 Determinism

Two runs with the same seed must produce **byte-identical** `customers.csv`. Both Python's `random` and Faker are seeded, and no field depends on the current time.

### 8.3 Other tables

`policies`, `claims`, `payments` exist as **empty tables** in Phase 1. Their generators arrive in a later phase.

## 9. Non-Functional Requirements

| Area | Requirement |
|---|---|
| Reproducibility | Fresh clone → working environment in under 10 minutes using documented commands |
| Security | No credentials in Git; Postgres not exposed beyond localhost |
| Maintainability | Small functions, type hints where useful, docstrings on important functions, no premature abstraction |
| Portability | Works on Windows, macOS, Linux (commands documented for each shell where they differ) |
| Simplicity | Minimal dependencies; no frameworks beyond what Phase 1 requires |

## 10. Success Metrics (Phase 1)

- 1,000 customers generated, 1,000 unique IDs.
- Generator run twice produces identical checksums.
- `docker compose up -d` reaches `healthy`; all four tables exist with correct constraints.
- Zero secrets or `.env` files in Git history.
- Directory tree matches the specification.

## 11. Definition of Done — Phase 1

- [ ] `docker compose up -d` succeeds and the container reports healthy
- [ ] PostgreSQL is reachable on the configured port
- [ ] All four tables are created with the specified keys and constraints
- [ ] Virtual environment created and dependencies install cleanly
- [ ] Generator runs successfully from the command line
- [ ] Exactly 1,000 customers generated
- [ ] Customer IDs are unique
- [ ] `data/raw/customers.csv` exists
- [ ] Generator output is reproducible (checksum identical across two runs)
- [ ] No credentials committed
- [ ] Project structure matches the specification
- [ ] Final report delivered (summary, structure, files, commands, verification, issues), then **STOP**

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Loose schema decisions forced into rework later | Migration pain in Phase 2+ | Decide types and constraints now; record in `ARCHITECTURE.md` |
| Unrealistic synthetic data | Portfolio looks weak | Curated name and occupation lists, weighted distributions |
| Hidden non-determinism (timestamps, unseeded RNG) | Breaks reproducibility | Determinism test in Phase 1 checks |
| Scope creep from AI agents | Later phases built prematurely | Explicit phase gate in `AGENTS.md` / `CLAUDE.md` |
| Real data licensing/format surprises (data.gov.my, MOH) | Delays Phase 2 | Inspect sources early in Phase 2; document licence terms |

## 13. Assumptions & Open Questions

**Assumptions (change if wrong)**

- Docker and local tooling are already installed on the author's machine.
- Development is driven partly through AI coding agents (e.g. Google Antigravity, Claude Code).
- Ethnic mix for names is approximately Malay-majority with Chinese and Indian minorities, reflecting Malaysia's demographics. Exact weights are an implementation detail.

**Open questions**

- Should warehouse layers use separate Postgres schemas (`bronze`, `silver`, `gold`) or separate databases? Decide in the phase that introduces Bronze.
- Final enum values for policy/claim/payment statuses and types (proposed in `ARCHITECTURE.md`).
- Whether Phase 1 includes a `Makefile` for convenience commands.
