"""Automated tests for InsureFlow Phase 2B synthetic data generators.

Covers:
- customers.csv SHA256 regression (P1 contract lock)
- Policies: row counts, determinism, IDs, enums, date rules, FK
- Claims:   row counts, determinism, IDs, enums, date rules, FK, amounts
            Tests that need facilities_master.csv skip with a clear message
            if the file is absent.
- Payments: row counts, determinism, IDs, enums, date rules, FK, sum rule
"""

from __future__ import annotations

import hashlib
import tempfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from src.generation.common import (
    BASE_SEED,
    CLAIM_SEED_OFFSET,
    PAYMENT_SEED_OFFSET,
    POLICY_SEED_OFFSET,
    REFERENCE_DATE,
    fmt_claim_id,
    fmt_payment_id,
    fmt_policy_id,
    load_facilities,
    build_facility_pools,
    write_csv,
)
from src.generation.generate_policies import (
    COLUMNS as POLICY_COLUMNS,
    POLICY_TYPES,
    VALID_STATUSES as POLICY_STATUSES,
    build_policies,
    validate_policies,
)
from src.generation.generate_claims import (
    COLUMNS as CLAIM_COLUMNS,
    VALID_CLAIM_TYPES,
    VALID_STATUSES as CLAIM_STATUSES,
    build_claims,
    validate_claims,
)
from src.generation.generate_payments import (
    COLUMNS as PAYMENT_COLUMNS,
    VALID_METHODS,
    VALID_STATUSES as PAYMENT_STATUSES,
    build_payments,
    validate_payments,
)
from src.generation.generate_customers import (
    DEFAULT_CUSTOMER_COUNT,
    DEFAULT_SEED,
    build_customers,
    write_customers_to_csv,
)

# ─── Paths ────────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
_RAW_DIR   = _REPO_ROOT / "data" / "raw"

_FACILITIES_PATH  = _RAW_DIR / "facilities_master.csv"
_CUSTOMERS_PATH   = _RAW_DIR / "customers.csv"
_POLICIES_PATH    = _RAW_DIR / "policies.csv"
_CLAIMS_PATH      = _RAW_DIR / "claims.csv"
_PAYMENTS_PATH    = _RAW_DIR / "payments.csv"

# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def customers_df() -> pd.DataFrame:
    """Load customers.csv (generated in Phase 1, must already exist)."""
    if not _CUSTOMERS_PATH.exists():
        pytest.skip("customers.csv missing — run: python src/generation/generate_customers.py")
    return pd.read_csv(_CUSTOMERS_PATH, dtype=str)


@pytest.fixture(scope="module")
def policy_records(customers_df: pd.DataFrame) -> list[dict]:
    """Generate policy records from the standard customer set."""
    return build_policies(customers_df, seed=BASE_SEED + POLICY_SEED_OFFSET)


@pytest.fixture(scope="module")
def policies_df(policy_records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(policy_records)


@pytest.fixture(scope="module")
def facilities() -> list[dict]:
    """Load facilities. Skip all facilities-dependent tests if absent."""
    if not _FACILITIES_PATH.exists():
        pytest.skip(
            "facilities_master.csv not found in data/raw/. "
            "Run: python src/ingestion/download_sources.py"
        )
    return load_facilities(_RAW_DIR)


@pytest.fixture(scope="module")
def claim_records(
    policy_records: list[dict],
    customers_df: pd.DataFrame,
    facilities: list[dict],
) -> list[dict]:
    """Generate claim records."""
    policies_df_ = pd.DataFrame(policy_records)
    return build_claims(
        policies_df_, customers_df, facilities,
        seed=BASE_SEED + CLAIM_SEED_OFFSET,
    )


@pytest.fixture(scope="module")
def claims_df(claim_records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(claim_records)


@pytest.fixture(scope="module")
def payment_records(claim_records: list[dict]) -> list[dict]:
    """Generate payment records."""
    claims_df_ = pd.DataFrame(claim_records)
    return build_payments(claims_df_, seed=BASE_SEED + PAYMENT_SEED_OFFSET)


# ─── Phase 1 regression ──────────────────────────────────────────────────────

CUSTOMERS_SHA256 = "794E8ED790773C510C5D48BACCB7849194DFC6A44902FCFE4431EC88E043452C"


def test_customers_csv_sha256_regression() -> None:
    """Ensure customers.csv has not changed since Phase 1 (byte-identical lock)."""
    if not _CUSTOMERS_PATH.exists():
        pytest.skip("customers.csv not found")
    sha = hashlib.sha256(_CUSTOMERS_PATH.read_bytes()).hexdigest().upper()
    assert sha == CUSTOMERS_SHA256, (
        f"customers.csv SHA256 changed!\n  Expected: {CUSTOMERS_SHA256}\n  Got:      {sha}"
    )


# ─── Policies ────────────────────────────────────────────────────────────────

def test_policies_row_count(policy_records: list[dict]) -> None:
    """Policies row count should be in plausible range for 1,000 customers."""
    n = len(policy_records)
    # Minimum: if all had 1 policy → ~900; maximum: all had 3 → ~2,700
    assert 700 <= n <= 2700, f"Unexpected policy count: {n}"


def test_policies_id_format_and_uniqueness(policy_records: list[dict]) -> None:
    """Policy IDs must be P0000001..P{N}, sequential and unique."""
    ids = [r["policy_id"] for r in policy_records]
    assert len(ids) == len(set(ids)), "Duplicate policy IDs found"
    for i, pid in enumerate(ids, start=1):
        assert pid == fmt_policy_id(i), f"Policy ID at position {i}: {pid!r}"


def test_policies_enums(policy_records: list[dict]) -> None:
    """All policy_type and status values must be within allowed enums."""
    for r in policy_records:
        assert r["policy_type"] in set(POLICY_TYPES), \
            f"Invalid policy_type: {r['policy_type']!r}"
        assert r["status"] in POLICY_STATUSES, \
            f"Invalid status: {r['status']!r}"


def test_policies_date_rule(policy_records: list[dict]) -> None:
    """end_date must be >= start_date for every policy."""
    for r in policy_records:
        start = date.fromisoformat(r["start_date"])
        end   = date.fromisoformat(r["end_date"])
        assert end >= start, \
            f"Policy {r['policy_id']}: end_date {end} < start_date {start}"


def test_policies_status_vs_dates(policy_records: list[dict]) -> None:
    """Policies with end_date before REFERENCE_DATE must not be ACTIVE."""
    for r in policy_records:
        end = date.fromisoformat(r["end_date"])
        if end < REFERENCE_DATE:
            assert r["status"] != "ACTIVE", \
                f"Policy {r['policy_id']} has past end_date but status=ACTIVE"


def test_policies_premium_positive(policy_records: list[dict]) -> None:
    """All premiums must be >= 0."""
    for r in policy_records:
        assert Decimal(r["premium"]) >= 0, \
            f"Negative premium in {r['policy_id']}: {r['premium']}"


def test_policies_customer_fk(
    policy_records: list[dict],
    customers_df: pd.DataFrame,
) -> None:
    """All customer_ids in policies must exist in customers."""
    valid_cids = set(customers_df["customer_id"])
    for r in policy_records:
        assert r["customer_id"] in valid_cids, \
            f"Unknown customer_id {r['customer_id']!r} in policy {r['policy_id']}"


def test_policies_determinism(customers_df: pd.DataFrame) -> None:
    """Two independent policy generation runs must produce byte-identical CSVs."""
    run1 = build_policies(customers_df, seed=BASE_SEED + POLICY_SEED_OFFSET)
    run2 = build_policies(customers_df, seed=BASE_SEED + POLICY_SEED_OFFSET)
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "run1.csv"
        p2 = Path(td) / "run2.csv"
        write_csv(run1, POLICY_COLUMNS, p1)
        write_csv(run2, POLICY_COLUMNS, p2)
        assert p1.read_bytes() == p2.read_bytes(), \
            "policies.csv output is not byte-identical across runs"


def test_policies_validate_function(
    policy_records: list[dict],
    customers_df: pd.DataFrame,
) -> None:
    """validate_policies() must pass on good data and raise on bad data."""
    # Should not raise
    validate_policies(policy_records, set(customers_df["customer_id"]))

    # Should raise on unknown customer
    bad = [{**policy_records[0], "customer_id": "C999999"}]
    with pytest.raises(ValueError, match="Unknown customer_id"):
        validate_policies(bad, set(customers_df["customer_id"]))


# ─── Claims ──────────────────────────────────────────────────────────────────

def test_claims_row_count(claim_records: list[dict]) -> None:
    """Claims row count must be positive and plausible."""
    n = len(claim_records)
    assert n > 0, "No claims generated"
    assert n <= 5000, f"Implausibly large claim count: {n}"


def test_claims_id_format_and_uniqueness(claim_records: list[dict]) -> None:
    """Claim IDs must be CL0000001..CL{N}, sequential and unique."""
    ids = [r["claim_id"] for r in claim_records]
    assert len(ids) == len(set(ids)), "Duplicate claim IDs found"
    for i, cid in enumerate(ids, start=1):
        assert cid == fmt_claim_id(i), f"Claim ID at position {i}: {cid!r}"


def test_claims_enums(claim_records: list[dict]) -> None:
    """All claim_type and status values must be within allowed enums."""
    for r in claim_records:
        assert r["claim_type"] in VALID_CLAIM_TYPES, \
            f"Invalid claim_type: {r['claim_type']!r}"
        assert r["status"] in CLAIM_STATUSES, \
            f"Invalid status: {r['status']!r}"


def test_claims_date_within_policy_period(
    claim_records: list[dict],
    policies_df: pd.DataFrame,
) -> None:
    """claim_date must be within the policy's [start_date, end_date]."""
    p_dates = {
        row["policy_id"]: (date.fromisoformat(row["start_date"]),
                            date.fromisoformat(row["end_date"]))
        for _, row in policies_df.iterrows()
    }
    for r in claim_records:
        cd = date.fromisoformat(r["claim_date"])
        ps, pe = p_dates[r["policy_id"]]
        assert ps <= cd <= pe, (
            f"Claim {r['claim_id']}: claim_date {cd} outside "
            f"policy [{ps}, {pe}]"
        )


def test_claims_date_not_after_reference(claim_records: list[dict]) -> None:
    """No claim_date may be after REFERENCE_DATE."""
    for r in claim_records:
        cd = date.fromisoformat(r["claim_date"])
        assert cd <= REFERENCE_DATE, \
            f"Claim {r['claim_id']}: claim_date {cd} > REFERENCE_DATE"


def test_claims_approved_amount_rules(claim_records: list[dict]) -> None:
    """Verify approved_amount rules per status."""
    for r in claim_records:
        status = r["status"]
        ca = Decimal(r["claim_amount"])
        aa_str = r["approved_amount"]

        if status == "SUBMITTED":
            assert aa_str == "", \
                f"Claim {r['claim_id']} SUBMITTED must have empty approved_amount"
        elif status == "REJECTED":
            assert Decimal(aa_str) == Decimal("0.00"), \
                f"Claim {r['claim_id']} REJECTED must have approved_amount=0.00"
        elif status == "APPROVED":
            assert Decimal(aa_str) == ca, \
                f"Claim {r['claim_id']} APPROVED: approved_amount != claim_amount"
        elif status == "PARTIALLY_APPROVED":
            aa = Decimal(aa_str)
            assert Decimal("0.00") <= aa <= ca, \
                f"Claim {r['claim_id']} PARTIALLY_APPROVED: {aa} not in [0, {ca}]"


def test_claims_policy_fk(
    claim_records: list[dict],
    policies_df: pd.DataFrame,
) -> None:
    """All policy_ids in claims must exist in policies."""
    valid_pids = set(policies_df["policy_id"])
    for r in claim_records:
        assert r["policy_id"] in valid_pids, \
            f"Unknown policy_id {r['policy_id']!r} in claim {r['claim_id']}"


def test_claims_facility_fk(
    claim_records: list[dict],
    facilities: list[dict],
) -> None:
    """All facility_ids in claims must exist in facilities_master."""
    valid_fids = {f["facility_id"] for f in facilities}
    for r in claim_records:
        assert r["facility_id"] in valid_fids, \
            f"Unknown facility_id {r['facility_id']!r} in claim {r['claim_id']}"


def test_claims_determinism(
    policies_df: pd.DataFrame,
    customers_df: pd.DataFrame,
    facilities: list[dict],
) -> None:
    """Two independent claim generation runs must produce byte-identical CSVs."""
    run1 = build_claims(policies_df, customers_df, facilities,
                        seed=BASE_SEED + CLAIM_SEED_OFFSET)
    run2 = build_claims(policies_df, customers_df, facilities,
                        seed=BASE_SEED + CLAIM_SEED_OFFSET)
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "run1.csv"
        p2 = Path(td) / "run2.csv"
        write_csv(run1, CLAIM_COLUMNS, p1)
        write_csv(run2, CLAIM_COLUMNS, p2)
        assert p1.read_bytes() == p2.read_bytes(), \
            "claims.csv output is not byte-identical across runs"


# ─── Payments ────────────────────────────────────────────────────────────────

def test_payments_row_count(payment_records: list[dict]) -> None:
    """Payments row count must be positive."""
    assert len(payment_records) > 0, "No payments generated"


def test_payments_id_format_and_uniqueness(payment_records: list[dict]) -> None:
    """Payment IDs must be PM0000001..PM{N}, sequential and unique."""
    ids = [r["payment_id"] for r in payment_records]
    assert len(ids) == len(set(ids)), "Duplicate payment IDs found"
    for i, pid in enumerate(ids, start=1):
        assert pid == fmt_payment_id(i), f"Payment ID at position {i}: {pid!r}"


def test_payments_enums(payment_records: list[dict]) -> None:
    """All payment_method and status values must be within allowed enums."""
    for r in payment_records:
        assert r["payment_method"] in VALID_METHODS, \
            f"Invalid payment_method: {r['payment_method']!r}"
        assert r["status"] in PAYMENT_STATUSES, \
            f"Invalid status: {r['status']!r}"


def test_payments_claim_fk(
    payment_records: list[dict],
    claims_df: pd.DataFrame,
) -> None:
    """All claim_ids in payments must exist in claims."""
    valid_cids = set(claims_df["claim_id"])
    for r in payment_records:
        assert r["claim_id"] in valid_cids, \
            f"Unknown claim_id {r['claim_id']!r} in payment {r['payment_id']}"


def test_payments_only_for_approved_claims(
    payment_records: list[dict],
    claims_df: pd.DataFrame,
) -> None:
    """Payments must only reference APPROVED or PARTIALLY_APPROVED claims."""
    claim_status = dict(zip(claims_df["claim_id"], claims_df["status"]))
    for r in payment_records:
        cs = claim_status[r["claim_id"]]
        assert cs in ("APPROVED", "PARTIALLY_APPROVED"), \
            f"Payment {r['payment_id']} references {cs} claim {r['claim_id']}"


def test_payments_date_after_claim_date(
    payment_records: list[dict],
    claims_df: pd.DataFrame,
) -> None:
    """payment_date must be >= the associated claim_date."""
    claim_date_map = dict(zip(claims_df["claim_id"], claims_df["claim_date"]))
    for r in payment_records:
        pd_ = date.fromisoformat(r["payment_date"])
        cd  = date.fromisoformat(claim_date_map[r["claim_id"]])
        assert pd_ >= cd, \
            f"Payment {r['payment_id']}: payment_date {pd_} < claim_date {cd}"


def test_payments_completed_sum_equals_approved_amount(
    payment_records: list[dict],
    claims_df: pd.DataFrame,
) -> None:
    """Sum of COMPLETED payments per claim must equal the claim's approved_amount."""
    approved = {
        row["claim_id"]: money_d(row["approved_amount"])
        for _, row in claims_df.iterrows()
        if row["status"] in ("APPROVED", "PARTIALLY_APPROVED")
    }

    completed_sum: dict[str, Decimal] = {}
    for r in payment_records:
        if r["status"] == "COMPLETED":
            cid = r["claim_id"]
            completed_sum[cid] = (
                completed_sum.get(cid, Decimal("0.00")) + Decimal(r["amount"])
            )

    for cid, total in completed_sum.items():
        expected = approved[cid]
        assert total == expected, (
            f"COMPLETED payment sum {total} != approved_amount {expected} "
            f"for claim {cid}"
        )


def money_d(s: str) -> Decimal:
    from decimal import Decimal, ROUND_HALF_UP
    return Decimal(str(s)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def test_payments_determinism(claims_df: pd.DataFrame) -> None:
    """Two independent payment generation runs must produce byte-identical CSVs."""
    run1 = build_payments(claims_df, seed=BASE_SEED + PAYMENT_SEED_OFFSET)
    run2 = build_payments(claims_df, seed=BASE_SEED + PAYMENT_SEED_OFFSET)
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "run1.csv"
        p2 = Path(td) / "run2.csv"
        write_csv(run1, PAYMENT_COLUMNS, p1)
        write_csv(run2, PAYMENT_COLUMNS, p2)
        assert p1.read_bytes() == p2.read_bytes(), \
            "payments.csv output is not byte-identical across runs"


# ─── Invocation Mode Subprocess Tests ──────────────────────────────────────────

GENERATOR_SCRIPTS = [
    "generate_customers",
    "generate_policies",
    "generate_claims",
    "generate_payments",
    "generate_all",
]


@pytest.mark.parametrize("script_name", GENERATOR_SCRIPTS)
def test_generator_direct_script_invocation(script_name: str) -> None:
    """Each generator must run cleanly via `python src/generation/<name>.py`."""
    import subprocess
    import sys
    script_path = Path("src") / "generation" / f"{script_name}.py"
    res = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"Direct script execution failed for {script_path}:\n"
        f"stdout: {res.stdout}\nstderr: {res.stderr}"
    )


@pytest.mark.parametrize("script_name", GENERATOR_SCRIPTS)
def test_generator_module_invocation(script_name: str) -> None:
    """Each generator must run cleanly via `python -m src.generation.<name>`."""
    import subprocess
    import sys
    module_path = f"src.generation.{script_name}"
    res = subprocess.run(
        [sys.executable, "-m", module_path],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"Module execution failed for {module_path}:\n"
        f"stdout: {res.stdout}\nstderr: {res.stderr}"
    )

