"""Automated tests for synthetic customer generation in InsureFlow Phase 1."""

from datetime import date, datetime
from pathlib import Path
import re
import tempfile

import pandas as pd
import pytest

from src.generation.generate_customers import (
    DEFAULT_CUSTOMER_COUNT,
    DEFAULT_SEED,
    MALAYSIAN_OCCUPATIONS,
    MALAYSIAN_STATES,
    REFERENCE_DATE,
    build_customers,
    calculate_age,
    validate_customers,
    write_customers_to_csv,
)


@pytest.fixture(scope="module")
def customer_df() -> pd.DataFrame:
    """Fixture providing the default generated customer DataFrame."""
    df = build_customers(
        count=DEFAULT_CUSTOMER_COUNT,
        seed=DEFAULT_SEED,
        ref_date=REFERENCE_DATE,
    )
    return df


def test_row_count(customer_df: pd.DataFrame) -> None:
    """Validate that exactly 1,000 customer records are generated."""
    assert len(customer_df) == 1000, f"Expected 1,000 rows, got {len(customer_df)}"


def test_unique_sequential_customer_ids(customer_df: pd.DataFrame) -> None:
    """Validate customer_id uniqueness, format, and sequential order."""
    ids = customer_df["customer_id"].tolist()
    assert len(ids) == len(set(ids)), "Customer IDs must be completely unique"
    
    # Check format C000001 through C001000
    id_pattern = re.compile(r"^C\d{6}$")
    for idx, cid in enumerate(ids, start=1):
        assert id_pattern.match(cid), f"ID '{cid}' does not match pattern C000000"
        expected_id = f"C{idx:06d}"
        assert cid == expected_id, f"Expected {expected_id}, got {cid}"


def test_no_null_values(customer_df: pd.DataFrame) -> None:
    """Ensure no null or empty values exist across all columns."""
    assert not customer_df.isna().any().any(), "Dataset should contain zero null values"


def test_gender_validity(customer_df: pd.DataFrame) -> None:
    """Ensure all gender values conform to 'Male' or 'Female'."""
    allowed_genders = {"Male", "Female"}
    actual_genders = set(customer_df["gender"].unique())
    assert actual_genders.issubset(allowed_genders), f"Unexpected genders: {actual_genders - allowed_genders}"


def test_states_validity(customer_df: pd.DataFrame) -> None:
    """Ensure all states are valid Malaysian states or Federal Territories."""
    allowed_states = set(MALAYSIAN_STATES)
    actual_states = set(customer_df["state"].unique())
    assert actual_states.issubset(allowed_states), f"Unexpected states: {actual_states - allowed_states}"


def test_occupations_validity(customer_df: pd.DataFrame) -> None:
    """Ensure all occupations are from the curated Malaysian occupations list."""
    allowed_occupations = set(MALAYSIAN_OCCUPATIONS)
    actual_occupations = set(customer_df["occupation"].unique())
    assert actual_occupations.issubset(allowed_occupations), (
        f"Unexpected occupations: {actual_occupations - allowed_occupations}"
    )


def test_age_range_at_reference_date(customer_df: pd.DataFrame) -> None:
    """Ensure every customer's age is between 18 and 65 (inclusive) at reference date."""
    for _, row in customer_df.iterrows():
        dob = date.fromisoformat(row["date_of_birth"])
        age = calculate_age(dob, REFERENCE_DATE)
        assert 18 <= age <= 65, f"Customer {row['customer_id']} age {age} outside [18, 65] range"


def test_created_at_is_before_or_at_reference_date(customer_df: pd.DataFrame) -> None:
    """Ensure created_at timestamps are valid and no customer is created in the future."""
    for _, row in customer_df.iterrows():
        created_dt = datetime.fromisoformat(row["created_at"])
        assert created_dt.date() <= REFERENCE_DATE, (
            f"Customer {row['customer_id']} created_at {created_dt} is in future of {REFERENCE_DATE}"
        )


def test_determinism_and_reproducibility() -> None:
    """Ensure two independent runs with the same seed yield identical data and CSV bytes."""
    run1 = build_customers(count=1000, seed=42, ref_date=REFERENCE_DATE)
    run2 = build_customers(count=1000, seed=42, ref_date=REFERENCE_DATE)

    pd.testing.assert_frame_equal(run1, run2)

    with tempfile.TemporaryDirectory() as tmpdir:
        path1 = Path(tmpdir) / "run1.csv"
        path2 = Path(tmpdir) / "run2.csv"
        write_customers_to_csv(run1, path1)
        write_customers_to_csv(run2, path2)

        bytes1 = path1.read_bytes()
        bytes2 = path2.read_bytes()
        assert bytes1 == bytes2, "CSV output from identical seed must be byte-identical"


def test_validation_function_detects_corrupt_data(customer_df: pd.DataFrame) -> None:
    """Ensure validate_customers raises ValueError when dataset fails rules."""
    # Corrupt row count
    with pytest.raises(ValueError, match="Expected exactly 1000 rows"):
        validate_customers(customer_df.iloc[:-1], expected_count=1000, ref_date=REFERENCE_DATE)

    # Corrupt gender
    corrupted_df = customer_df.copy()
    corrupted_df.loc[0, "gender"] = "Other"
    with pytest.raises(ValueError, match="invalid gender"):
        validate_customers(corrupted_df, expected_count=1000, ref_date=REFERENCE_DATE)
