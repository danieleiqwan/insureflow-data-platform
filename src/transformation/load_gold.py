"""Gold Layer Load Script for InsureFlow (Phase 5).

Reads clean data from silver.* and loads a dimensional (star schema) Gold layer
into gold.* in PostgreSQL. Trusts Silver's data contract — no re-validation.

Tables loaded (in FK-safe order):
  Dimensions: gold.dim_date, gold.dim_customer, gold.dim_policy, gold.dim_facility
  Facts:      gold.fact_claims, gold.fact_payments

Characteristics:
  - Full refresh: TRUNCATE all gold tables (in reverse FK order) then reload
    in a single transaction.
  - dim_date range is computed dynamically from the actual min/max dates across
    silver.policies (start_date, end_date), silver.claims (claim_date), and
    silver.payments (payment_date).
  - age in dim_customer is computed at load time relative to REFERENCE_DATE.
  - No new Python dependencies; uses psycopg2-binary (already in requirements.txt).

Usage:
    python src/transformation/load_gold.py
    python -m src.transformation.load_gold
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

# Ensure repository root is on sys.path when invoked directly
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Age is computed relative to this fixed reference date (same as Phase 4B DQ rules)
REFERENCE_DATE = date(2026, 1, 1)

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


# ─── Database Connection ──────────────────────────────────────────────────────

def get_db_connection() -> PgConnection:
    """Build a psycopg2 connection from environment variables."""
    load_dotenv(_REPO_ROOT / ".env")
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        dbname=os.getenv("POSTGRES_DB", "insureflow"),
        user=os.getenv("POSTGRES_USER", "insureflow_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )


# ─── Pure Helper Functions (unit-testable, no DB) ────────────────────────────

def compute_age(dob: date, reference: date = REFERENCE_DATE) -> int:
    """Compute age in full years at the reference date."""
    return reference.year - dob.year - (
        (reference.month, reference.day) < (dob.month, dob.day)
    )


def generate_date_dimension(start: date, end: date) -> List[Dict[str, Any]]:
    """Generate one dict per calendar day in [start, end] inclusive."""
    rows: List[Dict[str, Any]] = []
    current = start
    while current <= end:
        # isoweekday(): Monday=1 … Sunday=7; convert to Sunday=0 … Saturday=6
        iso_dow = current.isoweekday()   # Mon=1 … Sun=7
        dow = iso_dow % 7                # Sun=0, Mon=1, … Sat=6
        rows.append({
            "date_key":    current,
            "full_date":   current,
            "year":        current.year,
            "quarter":     (current.month - 1) // 3 + 1,
            "month":       current.month,
            "month_name":  MONTH_NAMES[current.month],
            "day":         current.day,
            "day_of_week": dow,
            "day_name":    DAY_NAMES[dow],
            "is_weekend":  dow in (0, 6),
        })
        current += timedelta(days=1)
    return rows


def compute_date_range(cur: Any, silver_schema: str = "silver") -> Tuple[date, date]:
    """Query Silver to find the actual min/max dates across all date columns."""
    cur.execute(f"""
        SELECT
            LEAST(
                MIN(p.start_date), MIN(p.end_date),
                MIN(c.claim_date),
                MIN(py.payment_date)
            ) AS min_date,
            GREATEST(
                MAX(p.start_date), MAX(p.end_date),
                MAX(c.claim_date),
                MAX(py.payment_date)
            ) AS max_date
        FROM {silver_schema}.policies p
        CROSS JOIN {silver_schema}.claims c
        CROSS JOIN {silver_schema}.payments py
    """)
    row = cur.fetchone()
    if not row or row[0] is None:
        raise ValueError("Silver tables are empty; cannot compute date range for dim_date.")
    return row[0], row[1]


# ─── Truncate Helpers ─────────────────────────────────────────────────────────

def truncate_gold_tables(cur: Any, gold_schema: str = "gold") -> None:
    """Truncate all gold tables in reverse FK dependency order."""
    # Reverse load order: facts first, then dims (most-dependent dims last)
    tables_in_reverse = [
        "fact_payments",
        "fact_claims",
        "dim_facility",
        "dim_policy",
        "dim_date",
        "dim_customer",
    ]
    for tbl in tables_in_reverse:
        cur.execute(f"TRUNCATE {gold_schema}.{tbl} CASCADE")
        logger.info("Truncated %s.%s", gold_schema, tbl)


# ─── Load Functions ───────────────────────────────────────────────────────────

def load_dim_date(cur: Any, rows: List[Dict[str, Any]], gold_schema: str = "gold") -> int:
    """Bulk-insert date dimension rows. Returns rows inserted."""
    if not rows:
        return 0
    data = [
        (
            r["date_key"], r["full_date"], r["year"], r["quarter"],
            r["month"], r["month_name"], r["day"], r["day_of_week"],
            r["day_name"], r["is_weekend"],
        )
        for r in rows
    ]
    execute_values(
        cur,
        f"""
        INSERT INTO {gold_schema}.dim_date
            (date_key, full_date, year, quarter, month, month_name,
             day, day_of_week, day_name, is_weekend)
        VALUES %s
        """,
        data,
        page_size=500,
    )
    return len(data)


def load_dim_customer(cur: Any, gold_schema: str = "gold", silver_schema: str = "silver") -> int:
    """Load dim_customer from silver.customers, computing age at REFERENCE_DATE."""
    cur.execute(f"""
        INSERT INTO {gold_schema}.dim_customer
            (customer_id, first_name, last_name, gender, date_of_birth, age, state, occupation)
        SELECT
            customer_id,
            first_name,
            last_name,
            gender,
            date_of_birth,
            -- age in full years at reference date
            DATE_PART('year', AGE(DATE '{REFERENCE_DATE.isoformat()}', date_of_birth))::SMALLINT,
            state,
            occupation
        FROM {silver_schema}.customers
    """)
    return cur.rowcount


def load_dim_policy(cur: Any, gold_schema: str = "gold", silver_schema: str = "silver") -> int:
    """Load dim_policy from silver.policies."""
    cur.execute(f"""
        INSERT INTO {gold_schema}.dim_policy
            (policy_id, customer_id, policy_type, start_date, end_date, premium, status)
        SELECT
            policy_id, customer_id, policy_type, start_date, end_date, premium, status
        FROM {silver_schema}.policies
    """)
    return cur.rowcount


def load_dim_facility(cur: Any, gold_schema: str = "gold", silver_schema: str = "silver") -> int:
    """Load dim_facility from silver.facilities (subset of columns needed for BI)."""
    cur.execute(f"""
        INSERT INTO {gold_schema}.dim_facility
            (facility_id, facility_name, facility_category, facility_type, state, district)
        SELECT
            facility_id, facility_name, facility_category, facility_type, state, district
        FROM {silver_schema}.facilities
    """)
    return cur.rowcount


def load_fact_claims(cur: Any, gold_schema: str = "gold", silver_schema: str = "silver") -> int:
    """Load fact_claims from silver.claims, denormalizing customer_id from policies."""
    cur.execute(f"""
        INSERT INTO {gold_schema}.fact_claims
            (claim_id, policy_id, customer_id, facility_id,
             claim_date_key, claim_type, claim_amount, approved_amount, status)
        SELECT
            c.claim_id,
            c.policy_id,
            p.customer_id,
            c.facility_id,
            c.claim_date,
            c.claim_type,
            c.claim_amount,
            c.approved_amount,
            c.status
        FROM {silver_schema}.claims c
        JOIN {silver_schema}.policies p ON p.policy_id = c.policy_id
    """)
    return cur.rowcount


def load_fact_payments(cur: Any, gold_schema: str = "gold", silver_schema: str = "silver") -> int:
    """Load fact_payments from silver.payments."""
    cur.execute(f"""
        INSERT INTO {gold_schema}.fact_payments
            (payment_id, claim_id, payment_date_key, amount, payment_method, status)
        SELECT
            payment_id, claim_id, payment_date, amount, payment_method, status
        FROM {silver_schema}.payments
    """)
    return cur.rowcount


# ─── Orchestrator ─────────────────────────────────────────────────────────────

def load_gold(
    conn: PgConnection,
    gold_schema: str = "gold",
    silver_schema: str = "silver",
) -> Dict[str, int]:
    """Execute the complete Gold load inside a single transaction.

    Returns a dict mapping table name -> rows loaded.
    """
    cur = conn.cursor()

    # 1. Truncate gold in reverse FK order
    truncate_gold_tables(cur, gold_schema)

    # 2. Compute dynamic date range from Silver
    min_date, max_date = compute_date_range(cur, silver_schema)
    logger.info("Date range from Silver: %s to %s", min_date, max_date)

    # 3. Generate date dimension rows (pure Python, then bulk insert)
    date_rows = generate_date_dimension(min_date, max_date)

    # 4. Load in FK-safe order: dims before facts
    counts: Dict[str, int] = {}
    counts["dim_date"]     = load_dim_date(cur, date_rows, gold_schema)
    counts["dim_customer"] = load_dim_customer(cur, gold_schema, silver_schema)
    counts["dim_facility"] = load_dim_facility(cur, gold_schema, silver_schema)
    counts["dim_policy"]   = load_dim_policy(cur, gold_schema, silver_schema)
    counts["fact_claims"]  = load_fact_claims(cur, gold_schema, silver_schema)
    counts["fact_payments"]= load_fact_payments(cur, gold_schema, silver_schema)

    cur.close()
    return counts


# ─── Summary Printer ──────────────────────────────────────────────────────────

def print_summary(counts: Dict[str, int]) -> None:
    """Print a clean Gold load summary to stdout."""
    print("\n" + "=" * 55)
    print("InsureFlow Gold Layer Load Summary (Phase 5)")
    print("=" * 55)
    print(f"  {'Table':<25} {'Rows Loaded':>10}")
    print("  " + "-" * 37)
    for tbl, n in counts.items():
        print(f"  {tbl:<25} {n:>10,d}")
    print("  " + "-" * 37)
    print(f"  {'TOTAL':<25} {sum(counts.values()):>10,d}")
    print("=" * 55 + "\n")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main() -> None:
    """CLI entry point for Gold load."""
    conn = get_db_connection()
    conn.autocommit = False
    try:
        counts = load_gold(conn)
        conn.commit()
        print_summary(counts)
    except Exception as exc:
        conn.rollback()
        logger.error("Gold load failed and was rolled back: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
