"""Unit and integration tests for the Gold Layer load (Phase 5).

Unit tests: pure helper logic — date dimension generation, age calculation.
Integration tests: skip cleanly if DB unreachable; use an isolated 'gold_test'
schema that is fully dropped in teardown, never touching real gold/silver data.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Generator, Dict

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

from src.transformation.load_gold import (
    REFERENCE_DATE,
    compute_age,
    generate_date_dimension,
)


# ─── Unit Tests: Pure Helpers (No Database) ───────────────────────────────────

class TestComputeAge:
    def test_exact_birthday(self) -> None:
        # Born exactly reference_date years before reference date
        dob = date(2000, 1, 1)
        assert compute_age(dob, reference=date(2026, 1, 1)) == 26

    def test_birthday_not_yet_this_year(self) -> None:
        # Born June 15; reference Jan 1 → hasn't had birthday yet this year
        dob = date(2000, 6, 15)
        assert compute_age(dob, reference=date(2026, 1, 1)) == 25

    def test_birthday_today(self) -> None:
        # Born exactly on reference date day/month
        dob = date(1990, 3, 15)
        ref = date(2026, 3, 15)
        assert compute_age(dob, reference=ref) == 36

    def test_leap_year_dob(self) -> None:
        dob = date(2000, 2, 29)
        assert compute_age(dob, reference=date(2026, 1, 1)) == 25  # hasn't reached Feb in 2026 yet


class TestGenerateDateDimension:
    def test_single_day(self) -> None:
        rows = generate_date_dimension(date(2025, 1, 1), date(2025, 1, 1))
        assert len(rows) == 1
        r = rows[0]
        assert r["date_key"] == date(2025, 1, 1)
        assert r["year"] == 2025
        assert r["month"] == 1
        assert r["quarter"] == 1
        assert r["day"] == 1
        assert r["month_name"] == "January"

    def test_span_length(self) -> None:
        rows = generate_date_dimension(date(2024, 1, 1), date(2024, 12, 31))
        assert len(rows) == 366  # 2024 is a leap year

    def test_weekend_flag(self) -> None:
        # 2025-01-04 is Saturday
        rows = generate_date_dimension(date(2025, 1, 4), date(2025, 1, 5))
        sat, sun = rows[0], rows[1]
        assert sat["is_weekend"] is True
        assert sat["day_name"] == "Saturday"
        assert sun["is_weekend"] is True
        assert sun["day_name"] == "Sunday"

    def test_weekday_not_weekend(self) -> None:
        # 2025-01-06 is Monday
        rows = generate_date_dimension(date(2025, 1, 6), date(2025, 1, 6))
        assert rows[0]["is_weekend"] is False
        assert rows[0]["day_name"] == "Monday"

    def test_quarter_assignment(self) -> None:
        dates = [date(2025, m, 1) for m in range(1, 13)]
        expected_quarters = [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]
        for d, q in zip(dates, expected_quarters):
            rows = generate_date_dimension(d, d)
            assert rows[0]["quarter"] == q, f"Failed for {d}: expected Q{q}"

    def test_all_fields_present(self) -> None:
        rows = generate_date_dimension(date(2025, 6, 15), date(2025, 6, 15))
        required = {"date_key", "full_date", "year", "quarter", "month", "month_name",
                    "day", "day_of_week", "day_name", "is_weekend"}
        assert required == set(rows[0].keys())

    def test_no_duplicates(self) -> None:
        rows = generate_date_dimension(date(2024, 1, 1), date(2024, 3, 31))
        keys = [r["date_key"] for r in rows]
        assert len(keys) == len(set(keys))


# ─── Integration Tests: Database (skip if unreachable) ────────────────────────

try:
    import psycopg2
    from dotenv import load_dotenv
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False


@pytest.fixture(scope="module")
def db_conn() -> Generator:
    """Provide a live DB connection; skip module if unreachable."""
    if not _PSYCOPG2_AVAILABLE:
        pytest.skip("psycopg2 not installed")
    load_dotenv(_REPO_ROOT / ".env")
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = int(os.getenv("POSTGRES_PORT", "5433"))
    dbname = os.getenv("POSTGRES_DB", "insureflow")
    user = os.getenv("POSTGRES_USER", "insureflow_user")
    password = os.getenv("POSTGRES_PASSWORD", "")
    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=dbname,
            user=user, password=password, connect_timeout=3,
        )
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable on {host}:{port} ({exc}); skipping integration tests.")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def gold_test_schema(db_conn) -> Generator[str, None, None]:
    """Create an isolated gold_test schema from sql/gold.sql; drop on teardown."""
    from src.transformation.load_gold import load_gold

    gold_schema = "gold_test"
    silver_schema = "silver"  # reads from real silver (read-only)

    gold_sql = (_REPO_ROOT / "sql" / "gold.sql").read_text(encoding="utf-8")
    # Replace 'gold' schema references with gold_test
    gold_sql_patched = gold_sql.replace(
        "CREATE SCHEMA IF NOT EXISTS gold;",
        f"CREATE SCHEMA IF NOT EXISTS {gold_schema};",
    ).replace(
        "gold.dim_", f"{gold_schema}.dim_"
    ).replace(
        "gold.fact_", f"{gold_schema}.fact_"
    )

    db_conn.autocommit = True
    cur = db_conn.cursor()

    try:
        cur.execute(f"DROP SCHEMA IF EXISTS {gold_schema} CASCADE")
        cur.execute(gold_sql_patched)
    finally:
        cur.close()

    db_conn.autocommit = False

    # Do the first load
    counts = load_gold(db_conn, gold_schema=gold_schema, silver_schema=silver_schema)
    db_conn.commit()

    yield gold_schema

    # Teardown: drop isolated schema
    try:
        db_conn.rollback()
    except Exception:
        pass
    db_conn.autocommit = True
    cur = db_conn.cursor()
    try:
        cur.execute(f"DROP SCHEMA IF EXISTS {gold_schema} CASCADE")
    finally:
        cur.close()
    db_conn.autocommit = False


class TestGoldIntegration:
    def test_row_counts_match_silver(self, db_conn, gold_test_schema: str) -> None:
        """Gold fact tables must have the same row count as Silver source tables."""
        cur = db_conn.cursor()
        g = gold_test_schema

        # fact_claims must match silver.claims
        cur.execute(f"SELECT COUNT(*) FROM {g}.fact_claims")
        gold_claims = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM silver.claims")
        silver_claims = cur.fetchone()[0]
        assert gold_claims == silver_claims, (
            f"fact_claims ({gold_claims}) != silver.claims ({silver_claims})"
        )

        # fact_payments must match silver.payments
        cur.execute(f"SELECT COUNT(*) FROM {g}.fact_payments")
        gold_payments = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM silver.payments")
        silver_payments = cur.fetchone()[0]
        assert gold_payments == silver_payments, (
            f"fact_payments ({gold_payments}) != silver.payments ({silver_payments})"
        )

        cur.close()

    def test_dim_date_range_matches_silver(self, db_conn, gold_test_schema: str) -> None:
        """dim_date must span at least the full min/max date range from Silver."""
        cur = db_conn.cursor()
        g = gold_test_schema

        cur.execute(f"SELECT MIN(date_key), MAX(date_key) FROM {g}.dim_date")
        gold_min, gold_max = cur.fetchone()

        cur.execute("""
            SELECT
                LEAST(MIN(p.start_date), MIN(p.end_date), MIN(c.claim_date), MIN(py.payment_date)),
                GREATEST(MAX(p.start_date), MAX(p.end_date), MAX(c.claim_date), MAX(py.payment_date))
            FROM silver.policies p
            CROSS JOIN silver.claims c
            CROSS JOIN silver.payments py
        """)
        silver_min, silver_max = cur.fetchone()

        assert gold_min <= silver_min, f"dim_date starts {gold_min} after silver min {silver_min}"
        assert gold_max >= silver_max, f"dim_date ends {gold_max} before silver max {silver_max}"
        cur.close()

    def test_full_refresh_is_idempotent(self, db_conn, gold_test_schema: str) -> None:
        """Running load_gold twice must produce identical row counts."""
        from src.transformation.load_gold import load_gold

        g = gold_test_schema
        cur = db_conn.cursor()

        # Capture counts after first load (already done in fixture)
        counts_before: Dict[str, int] = {}
        for tbl in ("dim_date", "dim_customer", "dim_facility", "dim_policy", "fact_claims", "fact_payments"):
            cur.execute(f"SELECT COUNT(*) FROM {g}.{tbl}")
            counts_before[tbl] = cur.fetchone()[0]
        cur.close()

        # Second load
        load_gold(db_conn, gold_schema=g, silver_schema="silver")
        db_conn.commit()

        cur = db_conn.cursor()
        counts_after: Dict[str, int] = {}
        for tbl in ("dim_date", "dim_customer", "dim_facility", "dim_policy", "fact_claims", "fact_payments"):
            cur.execute(f"SELECT COUNT(*) FROM {g}.{tbl}")
            counts_after[tbl] = cur.fetchone()[0]
        cur.close()

        assert counts_before == counts_after, (
            f"Idempotency failed. Before: {counts_before}, After: {counts_after}"
        )

    def test_dim_customer_count_matches_silver(self, db_conn, gold_test_schema: str) -> None:
        cur = db_conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {gold_test_schema}.dim_customer")
        gc = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM silver.customers")
        sc = cur.fetchone()[0]
        assert gc == sc
        cur.close()

    def test_dim_date_no_gaps(self, db_conn, gold_test_schema: str) -> None:
        """Verify dim_date has no missing dates in its range (no gaps)."""
        cur = db_conn.cursor()
        cur.execute(f"""
            SELECT MAX(date_key) - MIN(date_key) + 1 AS expected_days,
                   COUNT(*) AS actual_rows
            FROM {gold_test_schema}.dim_date
        """)
        expected_days, actual_rows = cur.fetchone()
        assert expected_days == actual_rows, (
            f"dim_date has gaps: expected {expected_days} days, got {actual_rows} rows"
        )
        cur.close()

    def test_fact_claims_customer_id_denormalized(self, db_conn, gold_test_schema: str) -> None:
        """Verify customer_id in fact_claims matches the policy's customer_id from Silver."""
        cur = db_conn.cursor()
        cur.execute(f"""
            SELECT COUNT(*)
            FROM {gold_test_schema}.fact_claims fc
            JOIN silver.claims sc ON sc.claim_id = fc.claim_id
            JOIN silver.policies sp ON sp.policy_id = sc.policy_id
            WHERE fc.customer_id != sp.customer_id
        """)
        mismatches = cur.fetchone()[0]
        assert mismatches == 0, f"{mismatches} customer_id mismatches in fact_claims denormalization"
        cur.close()
