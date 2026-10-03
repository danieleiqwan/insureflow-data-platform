"""Unit tests for individual Data Quality rules (Phase 4B).

Validates each rule with distinct positive (clean) and negative (defective) examples.
"""

from datetime import date
from decimal import Decimal
import pandas as pd
import pytest

from src.quality.dq_rules import (
    ALLOWED_CLAIM_TYPES,
    ALLOWED_GENDERS,
    ALLOWED_POLICY_TYPES,
    check_claim_approved_amount,
    check_claim_approved_amount_completeness,
    check_claim_within_policy_period,
    check_completeness,
    check_customer_age,
    check_date_format,
    check_dates_not_in_future,
    check_enum_validity,
    check_numeric_range,
    check_payment_after_claim,
    check_payment_dates_validity,
    check_policy_dates,
    check_uniqueness,
)


def test_check_completeness():
    """Verify required fields detect empty string, whitespace, null, and nan."""
    df = pd.DataFrame([
        {"id": "1", "name": "Ali", "state": "Johor"},
        {"id": "2", "name": "", "state": "Melaka"},
        {"id": "3", "name": "Bala", "state": "   "},
        {"id": "4", "name": None, "state": "Selangor"},
        {"id": "5", "name": "Chong", "state": "None"},
    ])
    failures = check_completeness(df, required_columns=["name", "state"], business_key_col="id")
    assert len(failures) == 4
    failed_ids = {f.business_key for f in failures}
    assert failed_ids == {"2", "3", "4", "5"}
    assert all(f.category == "completeness" for f in failures)


def test_check_uniqueness():
    """Verify business key uniqueness flags all instances of duplicated keys."""
    df = pd.DataFrame([
        {"customer_id": "C001", "val": "A"},
        {"customer_id": "C002", "val": "B"},
        {"customer_id": "C001", "val": "A2"},
        {"customer_id": "C003", "val": "C"},
    ])
    failures = check_uniqueness(df, business_key_col="customer_id")
    assert len(failures) == 2
    assert all(f.business_key == "C001" for f in failures)
    assert all(f.category == "uniqueness" for f in failures)


def test_check_enum_validity():
    """Verify enum validity checks values against allowed sets."""
    df = pd.DataFrame([
        {"id": "P1", "ptype": "MEDICAL"},
        {"id": "P2", "ptype": "HOSPITALIZATION"},
        {"id": "P3", "ptype": "PET_INSURANCE"},
        {"id": "P4", "ptype": "INVALID_TYPE"},
    ])
    failures = check_enum_validity(df, "ptype", ALLOWED_POLICY_TYPES, "id")
    assert len(failures) == 2
    failed_ids = {f.business_key for f in failures}
    assert failed_ids == {"P3", "P4"}
    assert all(f.category == "validity" for f in failures)


def test_check_date_format():
    """Verify ISO YYYY-MM-DD parsing detects slash, dots, and bad calendar days."""
    df = pd.DataFrame([
        {"id": "1", "dt": "2025-05-24"},
        {"id": "2", "dt": "24/05/2025"},
        {"id": "3", "dt": "2025.05.24"},
        {"id": "4", "dt": "2025-02-30"},  # Invalid calendar date
        {"id": "5", "dt": "not-a-date"},
    ])
    failures = check_date_format(df, "dt", "id")
    assert len(failures) == 4
    failed_ids = {f.business_key for f in failures}
    assert failed_ids == {"2", "3", "4", "5"}
    assert all(f.category == "validity" for f in failures)


def test_check_numeric_range():
    """Verify numeric range enforces decimal parsing and min/max bounds."""
    df = pd.DataFrame([
        {"id": "1", "amt": "150.00"},
        {"id": "2", "amt": "-10.00"},
        {"id": "3", "amt": "0.00"},
        {"id": "4", "amt": "5000000.00"},
        {"id": "5", "amt": "abc"},
    ])
    # Min 0.01, Max 100000.00
    failures = check_numeric_range(df, "amt", "id", min_val=Decimal("0.01"), max_val=Decimal("100000.00"))
    assert len(failures) == 4
    failed_ids = {f.business_key for f in failures}
    assert failed_ids == {"2", "3", "4", "5"}


def test_check_dates_not_in_future():
    """Verify dates after reference date 2026-01-01 are flagged."""
    ref = date(2026, 1, 1)
    df = pd.DataFrame([
        {"id": "1", "d1": "2025-12-31", "d2": "2024-01-01 12:00:00+00:00"},
        {"id": "2", "d1": "2026-01-01", "d2": "2025-06-01 10:00:00+00:00"},
        {"id": "3", "d1": "2026-06-15", "d2": "2025-01-01 10:00:00+00:00"},
        {"id": "4", "d1": "2025-01-01", "d2": "2028-01-01 10:00:00+00:00"},
    ])
    failures = check_dates_not_in_future(df, ["d1", "d2"], "id", reference_date=ref)
    assert len(failures) == 2
    failed_ids = {f.business_key for f in failures}
    assert failed_ids == {"3", "4"}
    assert all(f.category == "consistency" for f in failures)


def test_check_customer_age():
    """Verify age boundaries (18 to 120) relative to reference date 2026-01-01."""
    ref = date(2026, 1, 1)
    df = pd.DataFrame([
        {"customer_id": "C1", "dob": "1990-05-15"},  # 35 years old -> PASS
        {"customer_id": "C2", "dob": "2015-05-15"},  # 10 years old -> FAIL (<18)
        {"customer_id": "C3", "dob": "1880-01-01"},  # 146 years old -> FAIL (>120)
        {"customer_id": "C4", "dob": "2030-01-01"},  # Future -> FAIL
    ])
    failures = check_customer_age(df, birth_date_col="dob", business_key_col="customer_id", reference_date=ref)
    assert len(failures) == 3
    failed_ids = {f.business_key for f in failures}
    assert failed_ids == {"C2", "C3", "C4"}


def test_check_policy_dates():
    """Verify policy end_date >= start_date."""
    df = pd.DataFrame([
        {"policy_id": "P1", "start_date": "2025-01-01", "end_date": "2026-01-01"},  # PASS
        {"policy_id": "P2", "start_date": "2025-01-01", "end_date": "2025-01-01"},  # PASS
        {"policy_id": "P3", "start_date": "2025-06-01", "end_date": "2025-01-01"},  # FAIL
    ])
    failures = check_policy_dates(df, "start_date", "end_date", "policy_id")
    assert len(failures) == 1
    assert failures[0].business_key == "P3"
    assert failures[0].category == "consistency"


def test_check_claim_approved_amount():
    """Verify approved_amount <= claim_amount and >= 0."""
    df = pd.DataFrame([
        {"claim_id": "CL1", "claim_amount": "500.00", "approved_amount": "300.00"},   # PASS
        {"claim_id": "CL2", "claim_amount": "500.00", "approved_amount": "500.00"},   # PASS
        {"claim_id": "CL3", "claim_amount": "500.00", "approved_amount": "750.00"},   # FAIL (exceeds)
        {"claim_id": "CL4", "claim_amount": "500.00", "approved_amount": "-50.00"},   # FAIL (negative)
    ])
    failures = check_claim_approved_amount(df, "claim_amount", "approved_amount", "claim_id")
    assert len(failures) == 2
    failed_ids = {f.business_key for f in failures}
    assert failed_ids == {"CL3", "CL4"}


def test_check_claim_within_policy_period():
    """Verify claim_date is within policy start_date and end_date."""
    policies_df = pd.DataFrame([
        {"policy_id": "P1", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    ])
    claims_df = pd.DataFrame([
        {"claim_id": "CL1", "policy_id": "P1", "claim_date": "2025-06-01"},  # PASS
        {"claim_id": "CL2", "policy_id": "P1", "claim_date": "2024-12-15"},  # FAIL (before start)
        {"claim_id": "CL3", "policy_id": "P1", "claim_date": "2026-02-01"},  # FAIL (after end)
    ])
    failures = check_claim_within_policy_period(claims_df, policies_df, "claim_date", "policy_id", "claim_id")
    assert len(failures) == 2
    failed_ids = {f.business_key for f in failures}
    assert failed_ids == {"CL2", "CL3"}


def test_check_payment_after_claim():
    """Verify payment_date >= claim_date."""
    claims_df = pd.DataFrame([
        {"claim_id": "CL1", "claim_date": "2025-05-10"},
    ])
    payments_df = pd.DataFrame([
        {"payment_id": "PM1", "claim_id": "CL1", "payment_date": "2025-05-15"},  # PASS
        {"payment_id": "PM2", "claim_id": "CL1", "payment_date": "2025-05-10"},  # PASS
        {"payment_id": "PM3", "claim_id": "CL1", "payment_date": "2025-05-01"},  # FAIL (before claim)
    ])
    failures = check_payment_after_claim(payments_df, claims_df, "payment_date", "claim_id", "payment_id")
    assert len(failures) == 1
    assert failures[0].business_key == "PM3"
