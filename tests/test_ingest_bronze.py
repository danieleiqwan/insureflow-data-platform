"""Automated unit and integration tests for Bronze layer ingestion (Phase 3).

Unit tests require no database.
Integration tests require a running PostgreSQL instance (skip cleanly if unreachable)
and execute inside an isolated test schema (`bronze_test`) that is completely dropped
after test execution, ensuring zero pollution of production bronze tables.
"""

from __future__ import annotations

import csv
import hashlib
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
import psycopg2
from dotenv import load_dotenv

from src.ingestion.ingest_bronze import (
    ALL_SOURCES,
    FACILITIES_COLUMN_MAPPING,
    SOURCE_CONFIGS,
    HeaderValidationError,
    compute_file_sha256,
    get_db_connection,
    get_expected_table_columns,
    ingest_source,
    is_sha256_loaded,
    parse_args,
    validate_header,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ─── Unit Tests (No Database Required) ────────────────────────────────────────

def test_compute_file_sha256() -> None:
    """compute_file_sha256 must match standard hashlib digest."""
    with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
        tf.write(b"InsureFlow Bronze Test Content")
        tf_path = Path(tf.name)

    try:
        expected = hashlib.sha256(b"InsureFlow Bronze Test Content").hexdigest().upper()
        assert compute_file_sha256(tf_path) == expected
    finally:
        tf_path.unlink()


def test_facilities_column_mapping() -> None:
    """All 20 facilities_master columns must map to clean lowercase snake_case."""
    raw_headers = [
        "Index", "KOD_FASILITI", "STATUS", "SEKTOR", "SUBSEKTOR",
        "PROGRAM_GROUP", "NEGERI", "DAERAH", "KATEGORI_FASILITI", "JENIS_FASILITI",
        "NAMA", "ALAMAT", "BANDAR", "POSKOD", "DAERAH_PENTADBIRAN",
        "TELEFON", "EMEL", "URBAN_RURAL", "LATITUD", "LONGITUD",
    ]
    mapper = SOURCE_CONFIGS["facilities_master"]["column_mapper"]
    mapped = mapper(raw_headers)
    assert len(mapped) == 20
    assert mapped[0] == "index"
    assert mapped[1] == "kod_fasiliti"
    assert mapped[8] == "kategori_fasiliti"
    assert mapped[19] == "longitud"
    for col in mapped:
        assert col == col.lower(), f"Column {col} is not lowercase"
        assert " " not in col, f"Column {col} contains spaces"


def test_source_configs_registered() -> None:
    """All 5 target sources must be defined in SOURCE_CONFIGS."""
    expected = {"customers", "policies", "claims", "payments", "facilities_master"}
    assert set(ALL_SOURCES) == expected
    for name in expected:
        assert name in SOURCE_CONFIGS
        assert "file_name" in SOURCE_CONFIGS[name]
        assert "table_name" in SOURCE_CONFIGS[name]
        assert "column_mapper" in SOURCE_CONFIGS[name]


def test_cli_parsing_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default CLI arguments must be sources='all' and force=False."""
    monkeypatch.setattr("sys.argv", ["ingest_bronze.py"])
    args = parse_args()
    assert args.sources == "all"
    assert not args.force


def test_cli_parsing_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI options --sources and --force must parse accurately."""
    monkeypatch.setattr(
        "sys.argv",
        ["ingest_bronze.py", "--sources", "customers,claims", "--force"],
    )
    args = parse_args()
    assert args.sources == "customers,claims"
    assert args.force is True


def test_validate_header_success() -> None:
    """Matching headers in identical order must pass without exception."""
    cols = ["col_a", "col_b", "col_c"]
    validate_header(actual_columns=cols, expected_columns=cols, table_name="test_tbl")


def test_validate_header_order_differences_allowed() -> None:
    """Headers with permuted column order must pass validation."""
    actual = ["col_c", "col_a", "col_b"]
    expected = ["col_a", "col_b", "col_c"]
    validate_header(actual_columns=actual, expected_columns=expected, table_name="test_tbl")


def test_validate_header_missing_columns() -> None:
    """Missing columns must raise HeaderValidationError listing missing columns."""
    actual = ["col_a"]
    expected = ["col_a", "col_b", "col_c"]
    with pytest.raises(HeaderValidationError) as exc_info:
        validate_header(actual_columns=actual, expected_columns=expected, table_name="test_tbl")

    err = str(exc_info.value)
    assert "missing column(s): col_b, col_c" in err
    assert exc_info.value.missing == ["col_b", "col_c"]


def test_validate_header_extra_columns() -> None:
    """Extra columns must raise HeaderValidationError listing extra columns."""
    actual = ["col_a", "col_b", "col_c", "unexpected_col"]
    expected = ["col_a", "col_b", "col_c"]
    with pytest.raises(HeaderValidationError) as exc_info:
        validate_header(actual_columns=actual, expected_columns=expected, table_name="test_tbl")

    err = str(exc_info.value)
    assert "extra column(s): unexpected_col" in err
    assert exc_info.value.extra == ["unexpected_col"]


def test_validate_header_duplicates() -> None:
    """Duplicate columns must raise HeaderValidationError."""
    actual = ["col_a", "col_b", "col_a"]
    expected = ["col_a", "col_b"]
    with pytest.raises(HeaderValidationError) as exc_info:
        validate_header(actual_columns=actual, expected_columns=expected, table_name="test_tbl")

    err = str(exc_info.value)
    assert "duplicate column(s): col_a" in err


# ─── Integration Tests (Isolated in bronze_test Schema) ───────────────────────

@pytest.fixture(scope="module")
def db_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """Yield a database connection if PostgreSQL is running, else skip tests."""
    load_dotenv(_REPO_ROOT / ".env")
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("POSTGRES_DB", "insureflow"),
            user=os.getenv("POSTGRES_USER", "insureflow_user"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", "5433")),
            connect_timeout=3,
        )
    except Exception as exc:
        pytest.skip(f"PostgreSQL container not reachable: {exc}")

    yield conn
    conn.close()


@pytest.fixture(scope="module", autouse=True)
def guard_real_bronze_and_cleanup_test_schema(
    db_conn: psycopg2.extensions.connection,
) -> Generator[None, None, None]:
    """Ensure real bronze schema tables and audit log are completely untouched by tests,
    and verify bronze_test schema is dropped after test suite completion."""
    tables = ["customers", "policies", "claims", "payments", "facilities_master", "ingestion_log"]
    before = {}
    with db_conn.cursor() as cur:
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM bronze.{t}")
            before[t] = cur.fetchone()[0]

    yield

    # Verification 1: Confirm bronze_test schema was completely dropped and leaves no residue
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = 'bronze_test'"
        )
        assert cur.fetchone()[0] == 0, "bronze_test schema was not cleaned up after tests!"

    # Verification 2: Confirm real bronze tables and audit log were NEVER modified
    with db_conn.cursor() as cur:
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM bronze.{t}")
            after = cur.fetchone()[0]
            assert after == before[t], (
                f"Real bronze.{t} was modified during tests! Before: {before[t]}, After: {after}"
            )


@pytest.fixture(scope="module")
def test_schema(db_conn: psycopg2.extensions.connection) -> Generator[str, None, None]:
    """Create an isolated test schema with bronze tables, cleaning up afterwards."""
    schema_name = "bronze_test"
    bronze_sql_path = _REPO_ROOT / "sql" / "bronze.sql"
    raw_ddl = bronze_sql_path.read_text(encoding="utf-8")

    # Adapt DDL to target test schema
    test_ddl = raw_ddl.replace("bronze.", f"{schema_name}.").replace(
        "CREATE SCHEMA IF NOT EXISTS bronze;",
        f"CREATE SCHEMA IF NOT EXISTS {schema_name};",
    )

    with db_conn:
        with db_conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;")
            cur.execute(test_ddl)

    try:
        yield schema_name
    finally:
        # Teardown: drop test schema completely
        with db_conn:
            with db_conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;")


@pytest.mark.integration
def test_integration_bronze_ingestion_counts(
    db_conn: psycopg2.extensions.connection,
    test_schema: str,
) -> None:
    """Ingest all 5 sources into isolated schema and verify row counts match CSVs."""
    raw_dir = _REPO_ROOT / "data" / "raw"
    expected_counts = {
        "customers": 1000,
        "policies": 1379,
        "claims": 423,
        "payments": 364,
        "facilities_master": 5160,
    }

    for src, expected_row_count in expected_counts.items():
        res = ingest_source(
            conn=db_conn,
            source_name=src,
            raw_dir=raw_dir,
            force=False,
            schema=test_schema,
        )
        assert res["status"] == "SUCCESS", f"Failed to ingest {src}: {res['message']}"
        assert res["rows_loaded"] == expected_row_count

        # Query database to confirm count
        table_name = f"{test_schema}.{SOURCE_CONFIGS[src]['table_name']}"
        with db_conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            actual_count = cur.fetchone()[0]
            assert actual_count == expected_row_count, (
                f"Row count mismatch in {table_name}: expected {expected_row_count}, got {actual_count}"
            )


@pytest.mark.integration
def test_integration_bronze_idempotent_skip(
    db_conn: psycopg2.extensions.connection,
    test_schema: str,
) -> None:
    """Re-ingesting without --force must skip files with matching SHA256."""
    raw_dir = _REPO_ROOT / "data" / "raw"
    res = ingest_source(
        conn=db_conn,
        source_name="customers",
        raw_dir=raw_dir,
        force=False,
        schema=test_schema,
    )
    assert res["status"] == "SKIPPED"
    assert res["rows_loaded"] == 0
    assert "skipped: already loaded" in res["message"]

    # Table count should remain 1,000
    with db_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {test_schema}.customers")
        assert cur.fetchone()[0] == 1000


@pytest.mark.integration
def test_integration_bronze_force_reload(
    db_conn: psycopg2.extensions.connection,
    test_schema: str,
) -> None:
    """Re-ingesting with --force must append a new batch under a new batch_id."""
    raw_dir = _REPO_ROOT / "data" / "raw"
    res = ingest_source(
        conn=db_conn,
        source_name="customers",
        raw_dir=raw_dir,
        force=True,
        schema=test_schema,
    )
    assert res["status"] == "SUCCESS"
    assert res["rows_loaded"] == 1000

    # Table count should now be 2,000 rows across 2 batches
    with db_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT _batch_id) FROM {test_schema}.customers")
        total_rows, distinct_batches = cur.fetchone()
        assert total_rows == 2000
        assert distinct_batches == 2


@pytest.mark.integration
def test_integration_bronze_failure_missing_columns(
    db_conn: psycopg2.extensions.connection,
    test_schema: str,
) -> None:
    """Original failure test restored: CSV with missing columns must fail header validation,
    roll back any changes, and record FAILED in ingestion_log listing the missing columns."""
    with tempfile.TemporaryDirectory() as td:
        bad_dir = Path(td)
        bad_file = bad_dir / "customers.csv"
        # Missing columns: last_name, gender, date_of_birth, state, occupation, created_at
        bad_file.write_text("customer_id,first_name\nC000001,Ahmad\n", encoding="utf-8")

        res = ingest_source(
            conn=db_conn,
            source_name="customers",
            raw_dir=bad_dir,
            force=True,
            schema=test_schema,
        )

        assert res["status"] == "FAILED"
        assert res["rows_loaded"] == 0
        assert "missing column(s):" in res["message"]
        assert "last_name" in res["message"]
        assert "created_at" in res["message"]

        # Confirm failure was audited in ingestion_log with missing columns listed
        with db_conn.cursor() as cur:
            cur.execute(
                f"SELECT status, error_message FROM {test_schema}.ingestion_log "
                "WHERE batch_id = %s",
                (res["batch_id"],),
            )
            log_row = cur.fetchone()
            assert log_row is not None
            assert log_row[0] == "FAILED"
            assert "missing column(s):" in log_row[1]

            # Confirm rollback: no rows inserted into customers table for this batch
            cur.execute(
                f"SELECT COUNT(*) FROM {test_schema}.customers WHERE _batch_id = %s",
                (res["batch_id"],),
            )
            assert cur.fetchone()[0] == 0


@pytest.mark.integration
def test_integration_bronze_failure_nonexistent_column(
    db_conn: psycopg2.extensions.connection,
    test_schema: str,
) -> None:
    """Second separate test: CSV with nonexistent/extra column must fail header validation,
    roll back any changes, and record FAILED in ingestion_log listing the extra column."""
    with tempfile.TemporaryDirectory() as td:
        bad_dir = Path(td)
        bad_file = bad_dir / "customers.csv"
        # CSV with a nonexistent/extra column
        bad_file.write_text("customer_id,nonexistent_column\nC000001,bad_val\n", encoding="utf-8")

        res = ingest_source(
            conn=db_conn,
            source_name="customers",
            raw_dir=bad_dir,
            force=True,
            schema=test_schema,
        )

        assert res["status"] == "FAILED"
        assert res["rows_loaded"] == 0
        assert "extra column(s): nonexistent_column" in res["message"]

        # Confirm failure was audited in ingestion_log with extra column listed
        with db_conn.cursor() as cur:
            cur.execute(
                f"SELECT status, error_message FROM {test_schema}.ingestion_log "
                "WHERE batch_id = %s",
                (res["batch_id"],),
            )
            log_row = cur.fetchone()
            assert log_row is not None
            assert log_row[0] == "FAILED"
            assert "extra column(s): nonexistent_column" in log_row[1]

            # Confirm rollback
            cur.execute(
                f"SELECT COUNT(*) FROM {test_schema}.customers WHERE _batch_id = %s",
                (res["batch_id"],),
            )
            assert cur.fetchone()[0] == 0


@pytest.mark.integration
def test_integration_bronze_column_order_allowed(
    db_conn: psycopg2.extensions.connection,
    test_schema: str,
) -> None:
    """Column order differences are allowed: ingestion must succeed and correctly map values."""
    with tempfile.TemporaryDirectory() as td:
        test_dir = Path(td)
        test_file = test_dir / "customers.csv"
        # Reorder columns: created_at first, customer_id second, etc.
        header = "created_at,customer_id,first_name,last_name,gender,date_of_birth,state,occupation\n"
        row = "2026-01-01T00:00:00+08:00,C009999,Zul,Ariff,Male,1990-05-15,Selangor,Teacher\n"
        test_file.write_text(header + row, encoding="utf-8")

        res = ingest_source(
            conn=db_conn,
            source_name="customers",
            raw_dir=test_dir,
            force=True,
            schema=test_schema,
        )

        assert res["status"] == "SUCCESS"
        assert res["rows_loaded"] == 1

        # Confirm data landed in the right columns despite different CSV order
        with db_conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT customer_id, first_name, last_name, occupation, state, created_at
                FROM {test_schema}.customers
                WHERE _batch_id = %s
                """,
                (res["batch_id"],),
            )
            db_row = cur.fetchone()
            assert db_row == ("C009999", "Zul", "Ariff", "Teacher", "Selangor", "2026-01-01T00:00:00+08:00")

