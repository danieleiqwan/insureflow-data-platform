"""Synthetic claims generator for InsureFlow Phase 2B.

Reads policies.csv and facilities_master.csv; generates claims.csv.
Seeded independently (BASE_SEED + CLAIM_SEED_OFFSET).

Claim rules:
- Generated only for ACTIVE and EXPIRED policies.
- claim_date ∈ [policy.start_date, min(policy.end_date, REFERENCE_DATE − 1 day)].
- claim_type plausible for policy_type (see CLAIM_TYPE_MAP).
- facility_id: 80% in customer's home state, 20% any state.
  INPATIENT/EMERGENCY → HOSPITAL; OUTPATIENT → HOSPITAL|KLINIK; DENTAL → KLINIK PERGIGIAN.
- claim_amount: lognormal distribution by claim_type (MYR).
- status weights: APPROVED 60%, PARTIALLY_APPROVED 15%, REJECTED 15%, SUBMITTED 10%.
  Recent claims (≤30 days before REFERENCE_DATE) are weighted 40% SUBMITTED.
- approved_amount: NULL if SUBMITTED; 0.00 if REJECTED; equals claim_amount if APPROVED;
  40–95% of claim_amount if PARTIALLY_APPROVED.
- created_at: seeded seconds after midnight UTC on claim_date.

Claim-type per policy-type mapping
────────────────────────────────────────────────────────────────────
MEDICAL           → OUTPATIENT 60%, INPATIENT 30%, DENTAL 10%
HOSPITALIZATION   → INPATIENT  70%, EMERGENCY  30%
CRITICAL_ILLNESS  → INPATIENT 100%
PERSONAL_ACCIDENT → OUTPATIENT 40%, EMERGENCY  40%, INPATIENT 20%
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from src.generation.common import (
    BASE_SEED,
    CLAIM_SEED_OFFSET,
    CLAIM_TYPE_CATEGORIES,
    CUSTOMER_STATE_TO_NEGERI,
    REFERENCE_DATE,
    build_facility_pools,
    fmt_claim_id,
    lognormal,
    load_facilities,
    money,
    sample_facility,
    seeded_timestamp,
    write_csv,
)

# ─── Schema ───────────────────────────────────────────────────────────────────
COLUMNS = [
    "claim_id", "policy_id", "facility_id", "claim_date",
    "claim_type", "claim_amount", "approved_amount", "status", "created_at",
]

# ─── Enums ────────────────────────────────────────────────────────────────────
VALID_CLAIM_TYPES  = {"OUTPATIENT", "INPATIENT", "EMERGENCY", "DENTAL"}
VALID_STATUSES     = {"SUBMITTED", "APPROVED", "PARTIALLY_APPROVED", "REJECTED"}

# ─── Claim-type per policy-type ───────────────────────────────────────────────
# (claim_type_value, weight)
CLAIM_TYPE_MAP: dict[str, list[tuple[str, float]]] = {
    "MEDICAL":           [("OUTPATIENT", 0.60), ("INPATIENT", 0.30), ("DENTAL", 0.10)],
    "HOSPITALIZATION":   [("INPATIENT",  0.70), ("EMERGENCY", 0.30)],
    "CRITICAL_ILLNESS":  [("INPATIENT",  1.00)],
    "PERSONAL_ACCIDENT": [("OUTPATIENT", 0.40), ("EMERGENCY", 0.40), ("INPATIENT", 0.20)],
}

# ─── Claim count distribution by policy_type ──────────────────────────────────
# (n_claims_options, weights)
CLAIM_COUNT_MAP: dict[str, tuple[list[int], list[float]]] = {
    "MEDICAL":           ([0, 1, 2, 3], [0.30, 0.40, 0.20, 0.10]),
    "HOSPITALIZATION":   ([0, 1, 2],    [0.40, 0.40, 0.20]),
    "CRITICAL_ILLNESS":  ([0, 1],       [0.50, 0.50]),
    "PERSONAL_ACCIDENT": ([0, 1, 2],    [0.40, 0.40, 0.20]),
}

# ─── Claim amount parameters (lognormal) ─────────────────────────────────────
# (median MYR, sigma, min MYR, max MYR)
AMOUNT_PARAMS: dict[str, tuple[float, float, float, float]] = {
    "OUTPATIENT": (150.0,  0.80,   50.0,   500.0),
    "INPATIENT":  (5000.0, 1.00, 1000.0, 50000.0),
    "EMERGENCY":  (2000.0, 0.90,  500.0, 15000.0),
    "DENTAL":     (300.0,  0.70,   50.0,  2000.0),
}

# ─── Status weights ───────────────────────────────────────────────────────────
_STATUS_NORMAL_VALUES  = ["SUBMITTED", "APPROVED", "PARTIALLY_APPROVED", "REJECTED"]
_STATUS_NORMAL_WEIGHTS = [0.10, 0.60, 0.15, 0.15]

_STATUS_RECENT_VALUES  = ["SUBMITTED", "APPROVED", "PARTIALLY_APPROVED", "REJECTED"]
_STATUS_RECENT_WEIGHTS = [0.40, 0.40, 0.10, 0.10]

_RECENT_DAYS = 30  # "recent" threshold


def _derive_status(claim_date: date, rng: random.Random) -> str:
    """Sample claim status, biasing toward SUBMITTED for recent claims."""
    recent = (REFERENCE_DATE - claim_date).days <= _RECENT_DAYS
    if recent:
        return rng.choices(_STATUS_RECENT_VALUES, weights=_STATUS_RECENT_WEIGHTS, k=1)[0]
    return rng.choices(_STATUS_NORMAL_VALUES, weights=_STATUS_NORMAL_WEIGHTS, k=1)[0]


def _derive_approved_amount(
    status: str, claim_amount: Decimal, rng: random.Random
) -> str:
    """Return approved_amount as string (empty string for NULL/SUBMITTED)."""
    if status == "SUBMITTED":
        return ""
    if status == "REJECTED":
        return "0.00"
    if status == "APPROVED":
        return str(claim_amount)
    # PARTIALLY_APPROVED: 40–95% of claim_amount
    pct = Decimal(str(rng.uniform(0.40, 0.95)))
    return str(money(claim_amount * pct))


# ─── Builder ──────────────────────────────────────────────────────────────────

def build_claims(
    policies_df: pd.DataFrame,
    customers_df: pd.DataFrame,
    facilities: list[dict],
    seed: int = BASE_SEED + CLAIM_SEED_OFFSET,
) -> list[dict]:
    """Generate claim records from policies and facilities.

    Args:
        policies_df: Loaded policies.csv.
        customers_df: Loaded customers.csv (for state lookup).
        facilities: Output of load_facilities().

    Returns:
        List of dicts matching COLUMNS.
    """
    rng = random.Random(seed)

    # Build facility pools once (sorted, deterministic)
    pools = build_facility_pools(facilities)

    # Build customer_id → state lookup
    customer_state: dict[str, str] = dict(
        zip(customers_df["customer_id"], customers_df["state"])
    )

    records: list[dict] = []
    claim_counter = 0

    for _, policy in policies_df.iterrows():
        policy_id  = policy["policy_id"]
        policy_type = policy["policy_type"]
        status_pol = policy["status"]

        # Only generate claims for ACTIVE and EXPIRED policies
        if status_pol not in ("ACTIVE", "EXPIRED"):
            continue

        start = date.fromisoformat(policy["start_date"])
        end   = date.fromisoformat(policy["end_date"])
        eff_end = min(end, REFERENCE_DATE - timedelta(days=1))

        if start > eff_end:
            # No valid claim window
            continue

        cust_state = customer_state.get(policy["customer_id"], "Selangor")

        # Number of claims for this policy
        n_opts, n_wts = CLAIM_COUNT_MAP[policy_type]
        n_claims = rng.choices(n_opts, weights=n_wts, k=1)[0]

        # claim_type options for this policy_type
        ct_values  = [t for t, _ in CLAIM_TYPE_MAP[policy_type]]
        ct_weights = [w for _, w in CLAIM_TYPE_MAP[policy_type]]

        for _ in range(n_claims):
            claim_type = rng.choices(ct_values, weights=ct_weights, k=1)[0]

            # Sample claim_date uniformly within valid window
            window_days = (eff_end - start).days
            claim_date = start + timedelta(days=rng.randint(0, window_days))

            # Facility
            facility_id = sample_facility(claim_type, cust_state, pools, rng)

            # Amount
            med, sig, lo, hi = AMOUNT_PARAMS[claim_type]
            claim_amount = lognormal(med, sig, lo, hi, rng)

            # Status and approved_amount
            status = _derive_status(claim_date, rng)
            approved_str = _derive_approved_amount(status, claim_amount, rng)

            created_at = seeded_timestamp(claim_date, rng)

            claim_counter += 1
            records.append({
                "claim_id":       fmt_claim_id(claim_counter),
                "policy_id":      policy_id,
                "facility_id":    facility_id,
                "claim_date":     claim_date.isoformat(),
                "claim_type":     claim_type,
                "claim_amount":   str(claim_amount),
                "approved_amount": approved_str,
                "status":         status,
                "created_at":     created_at,
            })

    return records


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_claims(
    records: list[dict],
    policy_ids: set[str],
    policies_df: pd.DataFrame,
    facility_ids: set[str] | None = None,
) -> None:
    """Validate claims against data contract rules.

    Args:
        records: Generated claim records.
        policy_ids: Set of valid policy_ids for FK check.
        policies_df: Full policy DataFrame for date range checks.
        facility_ids: Set of valid KOD_FASILITI; pass None to skip FK check
                      (used when facilities_master.csv is absent in CI).

    Raises:
        ValueError on first violation.
    """
    seen: set[str] = set()
    policy_dates: dict[str, tuple[date, date]] = {
        row["policy_id"]: (date.fromisoformat(row["start_date"]),
                           date.fromisoformat(row["end_date"]))
        for _, row in policies_df.iterrows()
    }

    for r in records:
        cid = r["claim_id"]

        # ID format: CL + 7 digits
        if len(cid) != 9 or not cid.startswith("CL") or not cid[2:].isdigit():
            raise ValueError(f"Invalid claim_id format: {cid!r}")
        if cid in seen:
            raise ValueError(f"Duplicate claim_id: {cid}")
        seen.add(cid)

        # FK: policy
        if r["policy_id"] not in policy_ids:
            raise ValueError(f"Unknown policy_id {r['policy_id']!r} in claim {cid}")

        # FK: facility
        if facility_ids is not None and r["facility_id"] not in facility_ids:
            raise ValueError(
                f"Unknown facility_id {r['facility_id']!r} in claim {cid}"
            )

        # Enums
        if r["claim_type"] not in VALID_CLAIM_TYPES:
            raise ValueError(f"Invalid claim_type {r['claim_type']!r} in {cid}")
        if r["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status {r['status']!r} in {cid}")

        # Date within policy period and not after REFERENCE_DATE
        claim_date = date.fromisoformat(r["claim_date"])
        if claim_date > REFERENCE_DATE:
            raise ValueError(f"claim_date {claim_date} is after REFERENCE_DATE in {cid}")

        p_start, p_end = policy_dates[r["policy_id"]]
        if not (p_start <= claim_date <= p_end):
            raise ValueError(
                f"claim_date {claim_date} outside policy period "
                f"[{p_start}, {p_end}] in {cid}"
            )

        # Amount rules
        claim_amount = Decimal(r["claim_amount"])
        if claim_amount < 0:
            raise ValueError(f"Negative claim_amount in {cid}")

        status = r["status"]
        approved_str = r["approved_amount"]

        if status == "SUBMITTED":
            if approved_str != "":
                raise ValueError(f"SUBMITTED claim {cid} must have NULL approved_amount")
        elif status == "REJECTED":
            if Decimal(approved_str) != Decimal("0.00"):
                raise ValueError(f"REJECTED claim {cid} must have approved_amount=0.00")
        elif status in ("APPROVED", "PARTIALLY_APPROVED"):
            approved = Decimal(approved_str)
            if approved < 0 or approved > claim_amount:
                raise ValueError(
                    f"approved_amount {approved} out of [0, {claim_amount}] in {cid}"
                )

    # Sequential IDs
    n = len(seen)
    for i in range(1, n + 1):
        expected = fmt_claim_id(i)
        if expected not in seen:
            raise ValueError(
                f"Non-sequential claim IDs: expected {expected} not in set"
            )


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    """Generate and save synthetic claims."""
    repo_root = Path(__file__).resolve().parents[2]
    raw_dir   = repo_root / "data" / "raw"

    print("Reading policies and customers...")
    policies_df  = pd.read_csv(raw_dir / "policies.csv",  dtype=str)
    customers_df = pd.read_csv(raw_dir / "customers.csv", dtype=str)

    print("Loading facilities...")
    facilities = load_facilities(raw_dir)

    print("Generating claims...")
    records = build_claims(policies_df, customers_df, facilities)

    print("Validating claims...")
    facility_ids = {f["facility_id"] for f in facilities}
    validate_claims(
        records,
        set(policies_df["policy_id"]),
        policies_df,
        facility_ids=facility_ids,
    )

    output_path = raw_dir / "claims.csv"
    print(f"Writing {len(records):,} claims to {output_path}")
    write_csv(records, COLUMNS, output_path)

    from collections import Counter
    types    = Counter(r["claim_type"] for r in records)
    statuses = Counter(r["status"] for r in records)
    print("\n--- Claims Generation Summary ---")
    print(f"Total claims  : {len(records):,}")
    for ct in sorted(types):
        print(f"  {ct:15s}: {types[ct]:4d}")
    print("Statuses:")
    for s in sorted(statuses):
        print(f"  {s:25s}: {statuses[s]:4d}")


if __name__ == "__main__":
    main()
