"""Automated unit and integration tests for Silver layer transformation (Phase 4A).

Unit tests require no database.
Integration tests require a running PostgreSQL instance (skip cleanly if unreachable)
and execute inside isolated schemas (`bronze_silver_test` and `silver_test`) that are
completely dropped after test execution, ensuring zero pollution of real data.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Generator, List, Set

import pytest
import psycopg2
from dotenv import load_dotenv

from src.transformation.transform_silver import (
    ALLOWED_CLAIM_STATUSES,
    ALLOWED_CLAIM_TYPES,
    ALLOWED_GENDERS,
    ALLOWED_PAYMENT_METHODS,
    ALLOWED_PAYMENT_STATUSES,
    ALLOWED_POLICY_STATUSES,
    ALLOWED_POLICY_TYPES,
    get_db_connection,
    parse_date_str,
    parse_decimal_val,
    parse_timestamp_str,
    transform_silver,
    validate_and_transform_claim,
    validate_and_transform_customer,
    validate_and_transform_facility,
    validate_and_transform_payment,
    validate_and_transform_policy,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ─── Unit Tests: Parsing Helpers (No Database) ────────────────────────────────

def test_parse_date_str_valid() -> None:
    d, err = parse_date_str("2024-05-15", "test_field")
    assert err is None
    assert d == date(2024, 5, 15)


def test_parse_date_str_invalid_and_missing() -> None:
    _, err_missing = parse_date_str("", "date_col")
    assert "Missing required date field" in err_missing

    _, err_none = parse_date_str(None, "date_col")
    assert "Missing required date field" in err_none

    _, err_invalid = parse_date_str("15/05/2024", "date_col")
    assert "Invalid date format" in err_invalid


def test_parse_decimal_val_valid() -> None:
    val, err = parse_decimal_val("1250.50", "amount", min_val=Decimal("0"))
    assert err is None
    assert val == Decimal("1250.50")


def test_parse_decimal_val_constraints() -> None:
    # Negative minimum constraint
    _, err_min = parse_decimal_val("-5.00", "premium", min_val=Decimal("0"))
    assert "Constraint violation" in err_min

    # Non-numeric
    _, err_nan = parse_decimal_val("abc", "amount")
    assert "Invalid numeric value" in err_nan

    # Null handling
    val_null, err_null = parse_decimal_val(None, "approved_amount", allow_null=True)
    assert err_null is None
    assert val_null is None

    _, err_req = parse_decimal_val("", "amount", allow_null=False)
    assert "Missing required numeric field" in err_req


def test_parse_timestamp_str_valid_and_fallback() -> None:
    ts_str = "2024-06-20 20:50:05+00:00"
    ts, err = parse_timestamp_str(ts_str, "created_at")
    assert err is None
    assert ts == datetime.fromisoformat(ts_str)

    # Empty with fallback
    ts_now, err_now = parse_timestamp_str(None, "created_at", allow_default_now=True)
    assert err_now is None
    assert isinstance(ts_now, datetime)

    # Invalid format
    _, err_inv = parse_timestamp_str("not-a-timestamp", "created_at")
    assert "Invalid timestamp format" in err_inv


# ─── Unit Tests: Entity Validation (No Database) ──────────────────────────────

def test_validate_facility_valid() -> None:
    row = {
        "kod_fasiliti": "11-01030012",
        "nama": "HOSPITAL ENCHE BESAR\nHAJJAH KHALSOM",
        "kategori_fasiliti": "HOSPITAL",
        "jenis_fasiliti": "HOSPITAL PAKAR MINOR",
        "subsektor": "KKM",
        "negeri": "JOHOR",
        "daerah": "KLUANG",
        "poskod": "86000",
        "latitud": "2.00732",
        "longitud": "103.3472",
        "_ingested_at": "2024-01-01 00:00:00+00:00",
    }
    seen_ids: Set[str] = set()
    transformed, err = validate_and_transform_facility(row, seen_ids)
    assert err is None
    assert transformed["facility_id"] == "11-01030012"
    assert transformed["facility_name"] == "HOSPITAL ENCHE BESAR HAJJAH KHALSOM"
    assert transformed["latitude"] == Decimal("2.00732")
    assert "11-01030012" in seen_ids


def test_validate_facility_invalid() -> None:
    seen: Set[str] = {"EXISTING"}
    # Duplicate
    _, err_dup = validate_and_transform_facility({"kod_fasiliti": "EXISTING"}, seen)
    assert "Duplicate facility_id" in err_dup

    # Missing nama
    _, err_nama = validate_and_transform_facility({"kod_fasiliti": "NEW"}, seen)
    assert "Missing required field: nama" in err_nama

    # Invalid latitude
    bad_lat = {
        "kod_fasiliti": "NEW",
        "nama": "CLINIC",
        "kategori_fasiliti": "KLINIK",
        "jenis_fasiliti": "KLINIK KESIHATAN",
        "subsektor": "KKM",
        "negeri": "SELANGOR",
        "daerah": "PETALING",
        "latitud": "bad_lat",
        "longitud": "101.5",
    }
    _, err_lat = validate_and_transform_facility(bad_lat, seen)
    assert "Invalid numeric value for latitude" in err_lat


def test_validate_customer_valid_and_invalid() -> None:
    seen: Set[str] = set()
    good = {
        "customer_id": "C000001",
        "first_name": "Khairul",
        "last_name": "Salleh",
        "gender": "Male",
        "date_of_birth": "1994-03-24",
        "state": "Melaka",
        "occupation": "Pharmacist",
        "created_at": "2024-06-20 20:50:05+00:00",
    }
    transformed, err = validate_and_transform_customer(good, seen)
    assert err is None
    assert transformed["customer_id"] == "C000001"
    assert transformed["gender"] == "Male"

    # Bad gender
    bad_gender = dict(good, customer_id="C000002", gender="Other")
    _, err_g = validate_and_transform_customer(bad_gender, seen)
    assert "Invalid gender 'Other'" in err_g

    # Bad date of birth
    bad_dob = dict(good, customer_id="C000003", date_of_birth="1994-02-31")
    _, err_dob = validate_and_transform_customer(bad_dob, seen)
    assert "Invalid date format for date_of_birth" in err_dob


def test_validate_policy_constraints_and_fks() -> None:
    valid_customers = {"C000001"}
    seen: Set[str] = set()
    good = {
        "policy_id": "POL00001",
        "customer_id": "C000001",
        "policy_type": "MEDICAL",
        "start_date": "2024-01-01",
        "end_date": "2025-01-01",
        "premium": "150.00",
        "status": "ACTIVE",
        "created_at": "2024-01-01 00:00:00+00:00",
    }
    transformed, err = validate_and_transform_policy(good, valid_customers, seen)
    assert err is None
    assert transformed["policy_id"] == "POL00001"

    # FK violation
    bad_cust = dict(good, policy_id="POL00002", customer_id="C999999")
    _, err_fk = validate_and_transform_policy(bad_cust, valid_customers, seen)
    assert "FK violation: customer_id 'C999999' not found in silver.customers" in err_fk

    # Dates inverted (end_date < start_date)
    bad_dates = dict(good, policy_id="POL00003", start_date="2025-01-01", end_date="2024-01-01")
    _, err_dates = validate_and_transform_policy(bad_dates, valid_customers, seen)
    assert "Constraint violation: end_date" in err_dates

    # Negative premium
    bad_prem = dict(good, policy_id="POL00004", premium="-50.00")
    _, err_prem = validate_and_transform_policy(bad_prem, valid_customers, seen)
    assert "Constraint violation for premium" in err_prem

    # Invalid enum
    bad_type = dict(good, policy_id="POL00005", policy_type="TRAVEL")
    _, err_type = validate_and_transform_policy(bad_type, valid_customers, seen)
    assert "Invalid policy_type 'TRAVEL'" in err_type


def test_validate_claim_constraints_and_fks() -> None:
    valid_policies = {"POL00001"}
    valid_facilities = {"11-01030012"}
    seen: Set[str] = set()

    good = {
        "claim_id": "CLM000001",
        "policy_id": "POL00001",
        "facility_id": "11-01030012",
        "claim_date": "2024-06-01",
        "claim_type": "INPATIENT",
        "claim_amount": "2500.00",
        "approved_amount": "2000.00",
        "status": "APPROVED",
        "created_at": "2024-06-02 00:00:00+00:00",
    }
    transformed, err = validate_and_transform_claim(good, valid_policies, valid_facilities, seen)
    assert err is None
    assert transformed["approved_amount"] == Decimal("2000.00")

    # Approved amount exceeding claim amount
    bad_app = dict(good, claim_id="CLM000002", approved_amount="3000.00")
    _, err_app = validate_and_transform_claim(bad_app, valid_policies, valid_facilities, seen)
    assert "Constraint violation: approved_amount (3000.00) exceeds claim_amount (2500.00)" in err_app

    # Facility FK violation
    bad_fac = dict(good, claim_id="CLM000003", facility_id="NON_EXISTENT")
    _, err_fac = validate_and_transform_claim(bad_fac, valid_policies, valid_facilities, seen)
    assert "FK violation: facility_id 'NON_EXISTENT'" in err_fac

    # Policy FK violation
    bad_pol = dict(good, claim_id="CLM000004", policy_id="NON_EXISTENT")
    _, err_pol = validate_and_transform_claim(bad_pol, valid_policies, valid_facilities, seen)
    assert "FK violation: policy_id 'NON_EXISTENT'" in err_pol


def test_validate_payment_constraints_and_fks() -> None:
    valid_claims = {"CLM000001"}
    seen: Set[str] = set()

    good = {
        "payment_id": "PAY000001",
        "claim_id": "CLM000001",
        "payment_date": "2024-06-10",
        "amount": "2000.00",
        "payment_method": "BANK_TRANSFER",
        "status": "COMPLETED",
        "created_at": "2024-06-10 12:00:00+00:00",
    }
    transformed, err = validate_and_transform_payment(good, valid_claims, seen)
    assert err is None
    assert transformed["amount"] == Decimal("2000.00")

    # Claim FK violation
    bad_claim = dict(good, payment_id="PAY000002", claim_id="CLM999999")
    _, err_claim = validate_and_transform_payment(bad_claim, valid_claims, seen)
    assert "FK violation: claim_id 'CLM999999'" in err_claim

    # Invalid method
    bad_method = dict(good, payment_id="PAY000003", payment_method="BITCOIN")
    _, err_method = validate_and_transform_payment(bad_method, valid_claims, seen)
    assert "Invalid payment_method 'BITCOIN'" in err_method


# ─── Integration Tests: Database Setup & Teardown ─────────────────────────────

@pytest.fixture(scope="module")
def db_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """Provide a database connection, skipping cleanly if unreachable."""
    load_dotenv(_REPO_ROOT / ".env")
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = int(os.getenv("POSTGRES_PORT", "5433"))
    dbname = os.getenv("POSTGRES_DB", "insureflow")
    user = os.getenv("POSTGRES_USER", "insureflow_user")
    password = os.getenv("POSTGRES_PASSWORD", "")

    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            connect_timeout=3,
        )
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable on {host}:{port} ({exc}); skipping integration tests.")

    yield conn
    conn.close()


@pytest.fixture(scope="function")
def isolated_schemas(db_conn: psycopg2.extensions.connection) -> Generator[Dict[str, str], None, None]:
    """Create isolated test schemas for bronze and silver, completely dropped in teardown."""
    b_schema = "bronze_silver_test"
    s_schema = "silver_test"

    bronze_sql = (_REPO_ROOT / "sql" / "bronze.sql").read_text(encoding="utf-8")
    silver_sql = (_REPO_ROOT / "sql" / "silver.sql").read_text(encoding="utf-8")

    b_ddl = bronze_sql.replace("bronze.", f"{b_schema}.").replace("CREATE SCHEMA IF NOT EXISTS bronze;", f"CREATE SCHEMA IF NOT EXISTS {b_schema};")
    s_ddl = silver_sql.replace("silver.", f"{s_schema}.").replace("CREATE SCHEMA IF NOT EXISTS silver;", f"CREATE SCHEMA IF NOT EXISTS {s_schema};")

    with db_conn:
        with db_conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {b_schema} CASCADE;")
            cur.execute(f"DROP SCHEMA IF EXISTS {s_schema} CASCADE;")
            cur.execute(b_ddl)
            cur.execute(s_ddl)

    yield {"bronze": b_schema, "silver": s_schema}

    with db_conn:
        with db_conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {b_schema} CASCADE;")
            cur.execute(f"DROP SCHEMA IF EXISTS {s_schema} CASCADE;")


@pytest.mark.integration
def test_integration_full_refresh_and_idempotency(
    db_conn: psycopg2.extensions.connection,
    isolated_schemas: Dict[str, str],
) -> None:
    """Full refresh transforms bronze rows into silver, and second run yields identical counts."""
    b_schema = isolated_schemas["bronze"]
    s_schema = isolated_schemas["silver"]
    batch_id = uuid.uuid4()

    with db_conn:
        with db_conn.cursor() as cur:
            # Seed 1 facility
            cur.execute(
                f"""
                INSERT INTO {b_schema}.facilities_master
                    (_batch_id, _source_file, _source_row_number, kod_fasiliti, nama, kategori_fasiliti, jenis_fasiliti, subsektor, negeri, daerah, poskod, latitud, longitud)
                VALUES (%s, 'facilities_master.csv', 1, 'FAC001', 'HOSPITAL TEST', 'HOSPITAL', 'HOSPITAL BESAR', 'KKM', 'KEDAH', 'KOTA SETAR', '05100', '6.12', '100.36')
                """,
                (str(batch_id),),
            )
            # Seed 1 customer
            cur.execute(
                f"""
                INSERT INTO {b_schema}.customers
                    (_batch_id, _source_file, _source_row_number, customer_id, first_name, last_name, gender, date_of_birth, state, occupation, created_at)
                VALUES (%s, 'customers.csv', 1, 'C000001', 'Ali', 'Ahmad', 'Male', '1990-01-01', 'Kedah', 'Engineer', '2024-01-01 00:00:00+00:00')
                """,
                (str(batch_id),),
            )
            # Seed 1 policy
            cur.execute(
                f"""
                INSERT INTO {b_schema}.policies
                    (_batch_id, _source_file, _source_row_number, policy_id, customer_id, policy_type, start_date, end_date, premium, status, created_at)
                VALUES (%s, 'policies.csv', 1, 'POL00001', 'C000001', 'MEDICAL', '2024-01-01', '2025-01-01', '120.00', 'ACTIVE', '2024-01-01 00:00:00+00:00')
                """,
                (str(batch_id),),
            )
            # Seed 1 claim
            cur.execute(
                f"""
                INSERT INTO {b_schema}.claims
                    (_batch_id, _source_file, _source_row_number, claim_id, policy_id, facility_id, claim_date, claim_type, claim_amount, approved_amount, status, created_at)
                VALUES (%s, 'claims.csv', 1, 'CLM000001', 'POL00001', 'FAC001', '2024-05-01', 'INPATIENT', '500.00', '500.00', 'APPROVED', '2024-05-02 00:00:00+00:00')
                """,
                (str(batch_id),),
            )
            # Seed 1 payment
            cur.execute(
                f"""
                INSERT INTO {b_schema}.payments
                    (_batch_id, _source_file, _source_row_number, payment_id, claim_id, payment_date, amount, payment_method, status, created_at)
                VALUES (%s, 'payments.csv', 1, 'PAY000001', 'CLM000001', '2024-05-10', '500.00', 'BANK_TRANSFER', 'COMPLETED', '2024-05-10 00:00:00+00:00')
                """,
                (str(batch_id),),
            )

    # First run: transform
    res1 = transform_silver(db_conn, silver_schema=s_schema, bronze_schema=b_schema)
    assert res1["total_rows_loaded"] == 5
    assert res1["total_rows_rejected"] == 0

    with db_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {s_schema}.customers")
        assert cur.fetchone()[0] == 1
        cur.execute(f"SELECT COUNT(*) FROM {s_schema}.payments")
        assert cur.fetchone()[0] == 1

    # Second run: idempotent full refresh
    res2 = transform_silver(db_conn, silver_schema=s_schema, bronze_schema=b_schema)
    assert res2["total_rows_loaded"] == 5
    assert res2["total_rows_rejected"] == 0

    with db_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {s_schema}.customers")
        assert cur.fetchone()[0] == 1
        cur.execute(f"SELECT COUNT(*) FROM {s_schema}.payments")
        assert cur.fetchone()[0] == 1


@pytest.mark.integration
def test_integration_latest_batch_selection(
    db_conn: psycopg2.extensions.connection,
    isolated_schemas: Dict[str, str],
) -> None:
    """Only the most recent batch per source is processed during Silver transformation."""
    b_schema = isolated_schemas["bronze"]
    s_schema = isolated_schemas["silver"]

    batch_old = uuid.uuid4()
    batch_new = uuid.uuid4()

    with db_conn:
        with db_conn.cursor() as cur:
            # Batch old: 1 customer
            cur.execute(
                f"""
                INSERT INTO {b_schema}.customers
                    (_batch_id, _source_file, _source_row_number, _ingested_at, customer_id, first_name, last_name, gender, date_of_birth, state, occupation, created_at)
                VALUES (%s, 'customers.csv', 1, '2024-01-01 10:00:00+00', 'C000001', 'Old', 'Customer', 'Male', '1980-01-01', 'Perak', 'Clerk', '2024-01-01 00:00:00+00')
                """,
                (str(batch_old),),
            )
            # Batch new: 2 customers, later ingested_at
            cur.execute(
                f"""
                INSERT INTO {b_schema}.customers
                    (_batch_id, _source_file, _source_row_number, _ingested_at, customer_id, first_name, last_name, gender, date_of_birth, state, occupation, created_at)
                VALUES
                    (%s, 'customers.csv', 1, '2024-01-02 10:00:00+00', 'C000002', 'New1', 'Customer', 'Female', '1990-01-01', 'Johor', 'Analyst', '2024-01-02 00:00:00+00'),
                    (%s, 'customers.csv', 2, '2024-01-02 10:00:00+00', 'C000003', 'New2', 'Customer', 'Male', '1992-05-01', 'Sabah', 'Doctor', '2024-01-02 00:00:00+00')
                """,
                (str(batch_new), str(batch_new)),
            )

    res = transform_silver(db_conn, silver_schema=s_schema, bronze_schema=b_schema)
    assert res["tables"]["customers"]["batch_id"] == str(batch_new)
    assert res["tables"]["customers"]["rows_read"] == 2
    assert res["tables"]["customers"]["rows_loaded"] == 2

    with db_conn.cursor() as cur:
        cur.execute(f"SELECT customer_id FROM {s_schema}.customers ORDER BY customer_id")
        ids = [r[0] for r in cur.fetchall()]
        assert ids == ["C000002", "C000003"]
        assert "C000001" not in ids


@pytest.mark.integration
def test_integration_rejected_rows_handling(
    db_conn: psycopg2.extensions.connection,
    isolated_schemas: Dict[str, str],
) -> None:
    """Malformed and referentially broken rows are routed to silver.rejected_rows without crashing."""
    b_schema = isolated_schemas["bronze"]
    s_schema = isolated_schemas["silver"]
    batch_id = uuid.uuid4()

    with db_conn:
        with db_conn.cursor() as cur:
            # 1 valid customer + 1 customer with invalid gender
            cur.execute(
                f"""
                INSERT INTO {b_schema}.customers
                    (_batch_id, _source_file, _source_row_number, customer_id, first_name, last_name, gender, date_of_birth, state, occupation, created_at)
                VALUES
                    (%s, 'customers.csv', 1, 'C000010', 'Valid', 'User', 'Male', '1985-05-15', 'Selangor', 'Accountant', '2024-01-01 00:00:00+00'),
                    (%s, 'customers.csv', 2, 'C000011', 'Invalid', 'User', 'UNKNOWN', '1985-05-15', 'Selangor', 'Accountant', '2024-01-01 00:00:00+00')
                """,
                (str(batch_id), str(batch_id)),
            )
            # 1 policy referencing non-existent customer (FK violation)
            cur.execute(
                f"""
                INSERT INTO {b_schema}.policies
                    (_batch_id, _source_file, _source_row_number, policy_id, customer_id, policy_type, start_date, end_date, premium, status, created_at)
                VALUES
                    (%s, 'policies.csv', 1, 'POL00010', 'C999999', 'MEDICAL', '2024-01-01', '2025-01-01', '200.00', 'ACTIVE', '2024-01-01 00:00:00+00')
                """,
                (str(batch_id),),
            )

    res = transform_silver(db_conn, silver_schema=s_schema, bronze_schema=b_schema)
    assert res["tables"]["customers"]["rows_loaded"] == 1
    assert res["tables"]["customers"]["rows_rejected"] == 1
    assert res["tables"]["policies"]["rows_loaded"] == 0
    assert res["tables"]["policies"]["rows_rejected"] == 1

    with db_conn.cursor() as cur:
        cur.execute(f"SELECT source_table, reject_reason, raw_row FROM {s_schema}.rejected_rows ORDER BY source_table")
        rejections = cur.fetchall()
        assert len(rejections) == 2

        # Verify customer rejection
        cust_rej = next(r for r in rejections if r[0] == "customers")
        assert "Invalid gender 'UNKNOWN'" in cust_rej[1]
        assert cust_rej[2]["customer_id"] == "C000011"

        # Verify policy FK rejection
        pol_rej = next(r for r in rejections if r[0] == "policies")
        assert "FK violation: customer_id 'C999999'" in pol_rej[1]
        assert pol_rej[2]["policy_id"] == "POL00010"
