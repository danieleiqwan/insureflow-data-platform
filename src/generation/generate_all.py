"""generate_all.py -- Full Phase 2B synthetic data pipeline.

Runs the complete generation pipeline in dependency order:
  1. generate_policies  (reads customers.csv)
  2. generate_claims    (reads policies.csv + facilities_master.csv)
  3. generate_payments  (reads claims.csv)

Does NOT regenerate customers.csv (customer generation is Phase 1 and
must remain byte-identical; its SHA256 is fixed).

Usage:
    python src/generation/generate_all.py
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

# Ensure repository root is on sys.path when invoked directly as a script
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd

from src.generation.common import (
    REFERENCE_DATE,
    load_facilities,
    write_csv,
)
from src.generation.generate_policies import (
    COLUMNS as POLICY_COLUMNS,
    build_policies,
    validate_policies,
)
from src.generation.generate_claims import (
    COLUMNS as CLAIM_COLUMNS,
    build_claims,
    validate_claims,
)
from src.generation.generate_payments import (
    COLUMNS as PAYMENT_COLUMNS,
    build_payments,
    validate_payments,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    raw_dir   = repo_root / "data" / "raw"
    t0 = time.monotonic()

    print("=" * 60)
    print("InsureFlow Phase 2B -- Full Generation Pipeline")
    print(f"Reference date : {REFERENCE_DATE}")
    print("=" * 60)

    # -- Customers (read-only; must not be regenerated) ----------------------
    customers_path = raw_dir / "customers.csv"
    print(f"\n[0/3] Customers (existing)  ->  {customers_path}")
    customers_df = pd.read_csv(customers_path, dtype=str)
    print(f"      Loaded {len(customers_df):,} customer rows")

    # -- Facilities (read-only) -----------------------------------------------
    print("\n[  ] Loading facilities_master.csv...")
    facilities = load_facilities(raw_dir)
    print(f"      Loaded {len(facilities):,} facility rows")

    # -- Step 1: Policies ----------------------------------------------------
    print("\n[1/3] Generating policies...")
    policy_records = build_policies(customers_df)
    validate_policies(policy_records, set(customers_df["customer_id"]))
    policy_path = raw_dir / "policies.csv"
    write_csv(policy_records, POLICY_COLUMNS, policy_path)
    print(f"      {len(policy_records):,} policies  ->  {policy_path}")

    # -- Step 2: Claims ------------------------------------------------------
    print("\n[2/3] Generating claims...")
    policies_df = pd.read_csv(policy_path, dtype=str)
    claim_records = build_claims(policies_df, customers_df, facilities)
    facility_ids = {f["facility_id"] for f in facilities}
    validate_claims(
        claim_records,
        set(policies_df["policy_id"]),
        policies_df,
        facility_ids=facility_ids,
    )
    claim_path = raw_dir / "claims.csv"
    write_csv(claim_records, CLAIM_COLUMNS, claim_path)
    print(f"      {len(claim_records):,} claims    ->  {claim_path}")

    # -- Step 3: Payments -----------------------------------------------------
    print("\n[3/3] Generating payments...")
    claims_df = pd.read_csv(claim_path, dtype=str)
    payment_records = build_payments(claims_df)
    validate_payments(payment_records, claims_df)
    payment_path = raw_dir / "payments.csv"
    write_csv(payment_records, PAYMENT_COLUMNS, payment_path)
    print(f"      {len(payment_records):,} payments  ->  {payment_path}")

    # -- Summary --------------------------------------------------------------
    elapsed = time.monotonic() - t0
    print("\n" + "=" * 60)
    print("GENERATION COMPLETE")
    print("=" * 60)

    files = [
        ("customers.csv",  customers_path),
        ("policies.csv",   policy_path),
        ("claims.csv",     claim_path),
        ("payments.csv",   payment_path),
    ]
    print(f"\n{'File':<20} {'Rows':>8}  {'SHA256 (first 16)':<18}  Path")
    print("-" * 80)
    rows_map = {
        "customers.csv":  len(customers_df),
        "policies.csv":   len(policy_records),
        "claims.csv":     len(claim_records),
        "payments.csv":   len(payment_records),
    }
    for name, path in files:
        sha = _sha256(path)
        print(f"{name:<20} {rows_map[name]:>8}  {sha[:16]}  {path}")

    print(f"\nElapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
