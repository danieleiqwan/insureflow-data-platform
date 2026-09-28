"""Synthetic policy generator for InsureFlow Phase 2B.

Reads customers.csv and generates policies.csv with deterministic output.
Seeded independently of the customer generator (BASE_SEED + POLICY_SEED_OFFSET).

Policy rules:
- ~10% of customers have no policy; others have 1-3.
- policy_type weights: MEDICAL 40%, HOSPITALIZATION 30%,
  CRITICAL_ILLNESS 15%, PERSONAL_ACCIDENT 15%.
- start_date on/after customer's created_at and customer >=18 at start_date.
- Term: 1 year (end_date = start_date + 1 year − 1 day).
- Status: EXPIRED if end_date < REFERENCE_DATE; otherwise ACTIVE.
  About 5% are CANCELLED and 3% LAPSED (sampled before date check).
- Premium: base rate by type, age-loaded, +/-10% noise. MYR, NUMERIC(12,2).
- created_at: seeded seconds after midnight UTC on start_date.
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Ensure repository root is on sys.path when invoked directly as a script
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd

from src.generation.common import (
    BASE_SEED,
    POLICY_SEED_OFFSET,
    REFERENCE_DATE,
    fmt_policy_id,
    lognormal,
    money,
    seeded_timestamp,
    write_csv,
)

# --- Schema -------------------------------------------------------------------
COLUMNS = [
    "policy_id", "customer_id", "policy_type",
    "start_date", "end_date", "premium", "status", "created_at",
]

# --- Enums --------------------------------------------------------------------
POLICY_TYPES = [
    "MEDICAL", "HOSPITALIZATION", "CRITICAL_ILLNESS", "PERSONAL_ACCIDENT"
]
POLICY_TYPE_WEIGHTS = [0.40, 0.30, 0.15, 0.15]

VALID_STATUSES = {"ACTIVE", "EXPIRED", "CANCELLED", "LAPSED"}


def _calculate_age(dob: date, ref: date) -> int:
    """Age in complete years at ref."""
    return ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))


def _compute_premium(policy_type: str, age_at_start: int,
                     rng: random.Random) -> Decimal:
    """Return an actuarially plausible Malaysian annual premium (Decimal, 2 dp).

    Calibrated to realistic Malaysian market rates:
    - PERSONAL_ACCIDENT: RM 200 - RM 400/yr (mostly flat, modest age loading).
    - MEDICAL: RM 900 - RM 5,500/yr (comprehensive medical card with progressive age bands).
    - HOSPITALIZATION: RM 550 - RM 3,200/yr (daily hospital income & surgical cover).
    - CRITICAL_ILLNESS: RM 700 - RM 6,500/yr (lump-sum benefit with steep age curve).

    Formula combines base rate, quadratic age loading, and seeded noise (+/-8%).
    Rounded to nearest MYR 0.10.
    """
    age = max(18, age_at_start)
    age_delta = age - 18
    noise = 1.0 + (rng.random() - 0.5) * 0.16  # uniform +/-8% seeded variation

    if policy_type == "PERSONAL_ACCIDENT":
        base = 220.0 + 2.2 * age_delta
        raw = base * noise
    elif policy_type == "MEDICAL":
        base = 920.0 * (1.0 + 0.033 * age_delta + 0.0012 * (age_delta ** 2))
        raw = base * noise
    elif policy_type == "HOSPITALIZATION":
        base = 560.0 * (1.0 + 0.029 * age_delta + 0.0011 * (age_delta ** 2))
        raw = base * noise
    elif policy_type == "CRITICAL_ILLNESS":
        base = 720.0 * (1.0 + 0.027 * age_delta + 0.0020 * (age_delta ** 2))
        raw = base * noise
    else:
        raise ValueError(f"Unknown policy type: {policy_type}")

    rounded = (Decimal(str(raw)) / Decimal("0.10")).to_integral_value() * Decimal("0.10")
    return money(rounded)


def _policy_dates(
    customer_created_date: date, rng: random.Random
) -> tuple[date | None, date | None]:
    """Sample (start_date, end_date) for one policy.

    start_date ∈ [customer_created_date, REFERENCE_DATE − 1 day]
    end_date   = start_date + 1 year − 1 day
    Returns (None, None) if the window is degenerate.
    """
    start_min = customer_created_date
    start_max = REFERENCE_DATE - timedelta(days=1)
    delta = (start_max - start_min).days
    if delta < 0:
        return None, None

    start = start_min + timedelta(days=rng.randint(0, delta))
    try:
        end = start.replace(year=start.year + 1) - timedelta(days=1)
    except ValueError:
        # Leap-day edge (29 Feb -> 28 Feb next year)
        end = date(start.year + 1, 3, 1) - timedelta(days=1)
    return start, end


def _derive_status(end_date: date, rng: random.Random) -> str:
    """Derive policy status.

    ~5% CANCELLED, ~3% LAPSED sampled regardless of date;
    remainder: EXPIRED if end_date < REFERENCE_DATE else ACTIVE.
    """
    roll = rng.random()
    if roll < 0.05:
        return "CANCELLED"
    if roll < 0.08:
        return "LAPSED"
    return "EXPIRED" if end_date < REFERENCE_DATE else "ACTIVE"


# --- Builder ------------------------------------------------------------------

def build_policies(
    customers_df: pd.DataFrame,
    seed: int = BASE_SEED + POLICY_SEED_OFFSET,
) -> list[dict]:
    """Generate policy records from the customer DataFrame.

    Returns:
        List of dicts with keys matching COLUMNS.
    """
    rng = random.Random(seed)
    records: list[dict] = []
    policy_counter = 0

    n_choices = [0, 1, 2, 3]
    n_weights = [0.10, 0.55, 0.25, 0.10]

    for _, customer in customers_df.iterrows():
        customer_id = customer["customer_id"]
        dob = date.fromisoformat(customer["date_of_birth"])
        # Strip time component from created_at
        customer_created_date = datetime.fromisoformat(
            customer["created_at"]
        ).date()

        n_policies = rng.choices(n_choices, weights=n_weights, k=1)[0]

        for _ in range(n_policies):
            policy_type = rng.choices(POLICY_TYPES, weights=POLICY_TYPE_WEIGHTS,
                                      k=1)[0]
            start, end = _policy_dates(customer_created_date, rng)
            if start is None:
                # Consume status/premium RNG calls to keep stream aligned
                rng.random()  # status roll
                rng.random()  # premium noise
                rng.randint(0, 86399)  # timestamp
                continue

            age_at_start = _calculate_age(dob, start)
            if age_at_start < 18:
                # Push start date to 18th birthday
                try:
                    start = date(dob.year + 18, dob.month, dob.day)
                except ValueError:
                    start = date(dob.year + 18, 3, 1)  # Feb 29 -> Mar 1
                if start > REFERENCE_DATE - timedelta(days=1):
                    rng.random()
                    rng.random()
                    rng.randint(0, 86399)
                    continue
                try:
                    end = start.replace(year=start.year + 1) - timedelta(days=1)
                except ValueError:
                    end = date(start.year + 1, 3, 1) - timedelta(days=1)

            premium = _compute_premium(policy_type, age_at_start, rng)
            status = _derive_status(end, rng)
            created_at = seeded_timestamp(start, rng)

            policy_counter += 1
            records.append({
                "policy_id":   fmt_policy_id(policy_counter),
                "customer_id": customer_id,
                "policy_type": policy_type,
                "start_date":  start.isoformat(),
                "end_date":    end.isoformat(),
                "premium":     str(premium),
                "status":      status,
                "created_at":  created_at,
            })

    return records


# --- Validation ---------------------------------------------------------------

def validate_policies(
    records: list[dict],
    customer_ids: set[str],
) -> None:
    """Validate policy records against all data contract rules.

    Raises:
        ValueError: on first contract violation found.
    """
    seen: set[str] = set()

    for r in records:
        pid = r["policy_id"]

        # ID format: P + 7 digits
        if len(pid) != 8 or pid[0] != "P" or not pid[1:].isdigit():
            raise ValueError(f"Invalid policy_id format: {pid!r}")
        if pid in seen:
            raise ValueError(f"Duplicate policy_id: {pid}")
        seen.add(pid)

        # FK: customer must exist
        if r["customer_id"] not in customer_ids:
            raise ValueError(
                f"Unknown customer_id {r['customer_id']!r} in policy {pid}"
            )

        # Enums
        if r["policy_type"] not in set(POLICY_TYPES):
            raise ValueError(f"Invalid policy_type {r['policy_type']!r} in {pid}")
        if r["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status {r['status']!r} in {pid}")

        # Date rule: end >= start
        start = date.fromisoformat(r["start_date"])
        end = date.fromisoformat(r["end_date"])
        if end < start:
            raise ValueError(f"end_date < start_date in policy {pid}")

        # Status vs dates
        if end < REFERENCE_DATE and r["status"] not in ("EXPIRED", "CANCELLED", "LAPSED"):
            raise ValueError(
                f"Policy {pid} has end_date {end} before REFERENCE_DATE "
                f"but status is {r['status']!r} (expected EXPIRED/CANCELLED/LAPSED)"
            )

        # Money
        premium = Decimal(r["premium"])
        if premium < 0:
            raise ValueError(f"Negative premium in policy {pid}: {premium}")

    # Sequential IDs: P0000001 … P{N}
    n = len(seen)
    for i in range(1, n + 1):
        expected = fmt_policy_id(i)
        if expected not in seen:
            raise ValueError(
                f"Non-sequential policy IDs: expected {expected} not in set"
            )


# --- Entry point --------------------------------------------------------------

def main() -> None:
    """Generate and save synthetic policies."""
    repo_root = Path(__file__).resolve().parents[2]
    customers_path = repo_root / "data" / "raw" / "customers.csv"
    output_path = repo_root / "data" / "raw" / "policies.csv"

    print("Reading customers...")
    customers_df = pd.read_csv(customers_path, dtype=str)

    print("Generating policies...")
    records = build_policies(customers_df)

    print("Validating policies...")
    validate_policies(records, set(customers_df["customer_id"]))

    print(f"Writing {len(records):,} policies to {output_path}")
    write_csv(records, COLUMNS, output_path)

    from collections import Counter
    types = Counter(r["policy_type"] for r in records)
    statuses = Counter(r["status"] for r in records)
    print("\n--- Policy Generation Summary ---")
    print(f"Total policies : {len(records):,}")
    for pt in POLICY_TYPES:
        print(f"  {pt:25s}: {types[pt]:4d}")
    print("Statuses:")
    for s in sorted(statuses):
        print(f"  {s:20s}: {statuses[s]:4d}")


if __name__ == "__main__":
    main()
