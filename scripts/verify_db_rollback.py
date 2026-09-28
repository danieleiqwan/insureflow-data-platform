"""Verification script: load all CSVs inside a single DB transaction then ROLLBACK.

Proves all data satisfies every constraint (PK, FK, CHECK, NOT NULL) without
leaving anything persistent. Prints real psql-style counts before and after.

Usage:
    python scripts/verify_db_rollback.py
"""

from __future__ import annotations

import csv
import os
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RAW_DIR = _REPO_ROOT / "data" / "raw"

TABLES = ["facilities", "customers", "policies", "claims", "payments"]


def connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        dbname=os.getenv("POSTGRES_DB", "insureflow"),
        user=os.getenv("POSTGRES_USER", "insureflow_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


def count_all(cur) -> dict[str, int]:
    counts = {}
    for tbl in TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        counts[tbl] = cur.fetchone()[0]
    return counts


def load_facilities(cur, path: Path) -> int:
    rows = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Map CSV columns to DDL columns
            cur.execute(
                """
                INSERT INTO facilities
                    (facility_id, facility_name, facility_category, facility_type,
                     subsector, state, district, postcode, latitude, longitude)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["KOD_FASILITI"].strip(),
                    row["NAMA"].strip().replace("\n", " "),
                    row["KATEGORI_FASILITI"].strip(),
                    row["JENIS_FASILITI"].strip(),
                    row["SUBSEKTOR"].strip(),
                    row["NEGERI"].strip(),
                    row["DAERAH"].strip(),
                    row["POSKOD"].strip() or None,
                    Decimal(row["LATITUD"].strip()),
                    Decimal(row["LONGITUD"].strip()),
                ),
            )
            rows += 1
    return rows


def load_csv_generic(cur, path: Path, table: str, columns: list[str]) -> int:
    """Load a CSV into a table, mapping empty strings to NULL."""
    rows = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        placeholders = ", ".join(["%s"] * len(columns))
        col_str = ", ".join(columns)
        sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})"
        for row in reader:
            values = [row[c] if row[c] != "" else None for c in columns]
            cur.execute(sql, values)
            rows += 1
    return rows


def main() -> None:
    print("=" * 60)
    print("InsureFlow Phase 2B — DB Constraint Rollback Check")
    print("=" * 60)

    conn = connect()
    conn.autocommit = False

    try:
        cur = conn.cursor()

        # ── Before counts ────────────────────────────────────────────
        print("\n[Before] Row counts (expect all zeros):")
        before = count_all(cur)
        for tbl, n in before.items():
            print(f"  {tbl:15s}: {n:6d}")
        assert all(n == 0 for n in before.values()), \
            "Tables not empty before test — did a previous run not roll back?"

        # ── BEGIN transaction ─────────────────────────────────────────
        print("\n[BEGIN] Loading data inside transaction...")

        # 1. Facilities
        fac_path = _RAW_DIR / "facilities_master.csv"
        n_fac = load_facilities(cur, fac_path)
        print(f"  Inserted {n_fac:,} rows into facilities")

        # 2. Customers
        n_cust = load_csv_generic(
            cur, _RAW_DIR / "customers.csv", "customers",
            ["customer_id", "first_name", "last_name", "gender",
             "date_of_birth", "state", "occupation", "created_at"],
        )
        print(f"  Inserted {n_cust:,} rows into customers")

        # 3. Policies
        n_pol = load_csv_generic(
            cur, _RAW_DIR / "policies.csv", "policies",
            ["policy_id", "customer_id", "policy_type",
             "start_date", "end_date", "premium", "status", "created_at"],
        )
        print(f"  Inserted {n_pol:,} rows into policies")

        # 4. Claims
        n_claim = load_csv_generic(
            cur, _RAW_DIR / "claims.csv", "claims",
            ["claim_id", "policy_id", "facility_id", "claim_date",
             "claim_type", "claim_amount", "approved_amount", "status", "created_at"],
        )
        print(f"  Inserted {n_claim:,} rows into claims")

        # 5. Payments
        n_pay = load_csv_generic(
            cur, _RAW_DIR / "payments.csv", "payments",
            ["payment_id", "claim_id", "payment_date",
             "amount", "payment_method", "status", "created_at"],
        )
        print(f"  Inserted {n_pay:,} rows into payments")

        # ── Counts inside transaction ────────────────────────────────
        print("\n[Inside TX] Row counts (within transaction, before ROLLBACK):")
        inside = count_all(cur)
        for tbl, n in inside.items():
            print(f"  {tbl:15s}: {n:6d}")
        assert inside["facilities"] == n_fac,  "facilities count mismatch"
        assert inside["customers"]  == n_cust, "customers count mismatch"
        assert inside["policies"]   == n_pol,  "policies count mismatch"
        assert inside["claims"]     == n_claim, "claims count mismatch"
        assert inside["payments"]   == n_pay,  "payments count mismatch"

        # ── ROLLBACK ─────────────────────────────────────────────────
        conn.rollback()
        print("\n[ROLLBACK] Transaction rolled back.")

        # ── After counts ─────────────────────────────────────────────
        print("\n[After] Row counts (expect all zeros):")
        after = count_all(cur)
        for tbl, n in after.items():
            print(f"  {tbl:15s}: {n:6d}")
        assert all(n == 0 for n in after.values()), \
            "Tables not empty after rollback — something persisted unexpectedly"

        print("\n" + "=" * 60)
        print("ROLLBACK CHECK PASSED — all constraints satisfied, nothing persisted.")
        print("=" * 60)

    except Exception as exc:
        conn.rollback()
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
