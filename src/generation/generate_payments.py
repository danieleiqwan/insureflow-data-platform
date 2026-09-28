"""Synthetic payments generator for InsureFlow Phase 2B.

Reads claims.csv and generates payments.csv.
Seeded independently (BASE_SEED + PAYMENT_SEED_OFFSET).

Payment rules:
- Only for APPROVED and PARTIALLY_APPROVED claims.
- Normally 1 COMPLETED payment per claim; ~15% have a FAILED attempt first.
- payment_date = claim_date + 3–30 days; if > REFERENCE_DATE → PENDING.
- For FAILED+retry: retry_date = payment_date + 1–10 days.
  If retry_date > REFERENCE_DATE → main payment is PENDING (no failed entry added).
- Sum of COMPLETED payments per claim = approved_amount.
- FAILED payments use the same amount and do not count toward the sum.
- payment_method weights: BANK_TRANSFER 60%, E_WALLET 15%, CARD 15%, CHEQUE 10%.
- created_at: seeded seconds after midnight UTC on payment_date.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from src.generation.common import (
    BASE_SEED,
    PAYMENT_SEED_OFFSET,
    REFERENCE_DATE,
    fmt_payment_id,
    money,
    seeded_timestamp,
    write_csv,
)

# ─── Schema ───────────────────────────────────────────────────────────────────
COLUMNS = [
    "payment_id", "claim_id", "payment_date",
    "amount", "payment_method", "status", "created_at",
]

# ─── Enums ────────────────────────────────────────────────────────────────────
VALID_METHODS  = {"BANK_TRANSFER", "CHEQUE", "CARD", "E_WALLET"}
VALID_STATUSES = {"PENDING", "COMPLETED", "FAILED"}

_METHODS  = ["BANK_TRANSFER", "E_WALLET", "CARD", "CHEQUE"]
_WEIGHTS  = [0.60, 0.15, 0.15, 0.10]

# ─── Builder ──────────────────────────────────────────────────────────────────

def build_payments(
    claims_df: pd.DataFrame,
    seed: int = BASE_SEED + PAYMENT_SEED_OFFSET,
) -> list[dict]:
    """Generate payment records for APPROVED / PARTIALLY_APPROVED claims.

    Returns:
        List of dicts matching COLUMNS.
    """
    rng = random.Random(seed)
    records: list[dict] = []
    pay_counter = 0

    for _, claim in claims_df.iterrows():
        status = claim["status"]
        if status not in ("APPROVED", "PARTIALLY_APPROVED"):
            continue

        claim_id = claim["claim_id"]
        approved_amount = money(claim["approved_amount"])
        claim_date = date.fromisoformat(claim["claim_date"])

        # Base payment date: claim_date + 3–30 days
        base_days = rng.randint(3, 30)
        payment_date = claim_date + timedelta(days=base_days)

        method = rng.choices(_METHODS, weights=_WEIGHTS, k=1)[0]
        has_failed = rng.random() < 0.15

        if has_failed:
            # Only add the FAILED entry if the retry will actually complete
            # (otherwise the complexity adds no value — single PENDING is cleaner)
            retry_date = payment_date + timedelta(days=rng.randint(1, 10))
            if retry_date <= REFERENCE_DATE:
                # FAILED attempt on original payment_date
                pay_counter += 1
                failed_created = seeded_timestamp(payment_date, rng)
                records.append({
                    "payment_id":     fmt_payment_id(pay_counter),
                    "claim_id":       claim_id,
                    "payment_date":   payment_date.isoformat(),
                    "amount":         str(approved_amount),
                    "payment_method": method,
                    "status":         "FAILED",
                    "created_at":     failed_created,
                })
                # Swap to the retry date for the main (COMPLETED) payment
                payment_date = retry_date
                method = rng.choices(_METHODS, weights=_WEIGHTS, k=1)[0]
            else:
                # Retry is in the future — consume the RNG calls to stay aligned
                # but just emit a single PENDING payment on the base date
                rng.choices(_METHODS, weights=_WEIGHTS, k=1)  # consume retry method

        # Main payment
        if payment_date > REFERENCE_DATE:
            pay_status = "PENDING"
        else:
            pay_status = "COMPLETED"

        pay_counter += 1
        created_at = seeded_timestamp(payment_date, rng)
        records.append({
            "payment_id":     fmt_payment_id(pay_counter),
            "claim_id":       claim_id,
            "payment_date":   payment_date.isoformat(),
            "amount":         str(approved_amount),
            "payment_method": method,
            "status":         pay_status,
            "created_at":     created_at,
        })

    return records


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_payments(
    records: list[dict],
    claims_df: pd.DataFrame,
) -> None:
    """Validate payment records against all data contract rules.

    Checks:
    - ID format and uniqueness.
    - FK: claim_id exists in claims.
    - Enum: payment_method and status.
    - payment_date ≥ claim_date.
    - Sum of COMPLETED payments per claim = approved_amount.
    - PENDING/FAILED payments only for valid claims.

    Raises:
        ValueError on first violation.
    """
    seen: set[str] = set()

    # Build claim lookup: claim_id → {claim_date, approved_amount, status}
    claim_info: dict[str, dict] = {}
    for _, row in claims_df.iterrows():
        claim_info[row["claim_id"]] = {
            "claim_date":      date.fromisoformat(row["claim_date"]),
            "approved_amount": money(row["approved_amount"])
                               if row["status"] not in ("SUBMITTED",) and row["approved_amount"]
                               else Decimal("0.00"),
            "status":          row["status"],
        }

    # Track COMPLETED sums per claim
    completed_sum: dict[str, Decimal] = {}

    for r in records:
        pid = r["payment_id"]

        # ID: PM + 7 digits
        if len(pid) != 9 or not pid.startswith("PM") or not pid[2:].isdigit():
            raise ValueError(f"Invalid payment_id format: {pid!r}")
        if pid in seen:
            raise ValueError(f"Duplicate payment_id: {pid}")
        seen.add(pid)

        # FK: claim
        if r["claim_id"] not in claim_info:
            raise ValueError(f"Unknown claim_id {r['claim_id']!r} in payment {pid}")

        # Enums
        if r["payment_method"] not in VALID_METHODS:
            raise ValueError(f"Invalid payment_method {r['payment_method']!r} in {pid}")
        if r["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status {r['status']!r} in {pid}")

        # Amount
        amount = money(r["amount"])
        if amount < 0:
            raise ValueError(f"Negative amount in payment {pid}")

        # payment_date ≥ claim_date
        pay_date   = date.fromisoformat(r["payment_date"])
        claim_date = claim_info[r["claim_id"]]["claim_date"]
        if pay_date < claim_date:
            raise ValueError(
                f"payment_date {pay_date} before claim_date {claim_date} in {pid}"
            )

        # Accumulate COMPLETED sums
        if r["status"] == "COMPLETED":
            cid = r["claim_id"]
            completed_sum[cid] = completed_sum.get(cid, Decimal("0.00")) + amount

    # Verify COMPLETED sums equal approved_amount for each claim that has COMPLETEDs
    for cid, total in completed_sum.items():
        expected = claim_info[cid]["approved_amount"]
        if total != expected:
            raise ValueError(
                f"COMPLETED payment sum {total} ≠ approved_amount {expected} "
                f"for claim {cid}"
            )

    # Sequential IDs
    n = len(seen)
    for i in range(1, n + 1):
        expected = fmt_payment_id(i)
        if expected not in seen:
            raise ValueError(
                f"Non-sequential payment IDs: expected {expected} not in set"
            )


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    """Generate and save synthetic payments."""
    repo_root = Path(__file__).resolve().parents[2]
    raw_dir   = repo_root / "data" / "raw"

    print("Reading claims...")
    claims_df = pd.read_csv(raw_dir / "claims.csv", dtype=str)

    print("Generating payments...")
    records = build_payments(claims_df)

    print("Validating payments...")
    validate_payments(records, claims_df)

    output_path = raw_dir / "payments.csv"
    print(f"Writing {len(records):,} payments to {output_path}")
    write_csv(records, COLUMNS, output_path)

    from collections import Counter
    methods  = Counter(r["payment_method"] for r in records)
    statuses = Counter(r["status"] for r in records)
    print("\n--- Payments Generation Summary ---")
    print(f"Total payments : {len(records):,}")
    print("Methods:")
    for m in sorted(methods):
        print(f"  {m:20s}: {methods[m]:4d}")
    print("Statuses:")
    for s in sorted(statuses):
        print(f"  {s:12s}: {statuses[s]:4d}")


if __name__ == "__main__":
    main()
