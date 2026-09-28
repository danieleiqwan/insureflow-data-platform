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
    compute_file_sha256,
    get_db_connection,
    ingest_source,
    is_sha256_loaded,
    parse_args,
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

    yield schema_name

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
def test_integration_bronze_failure_rollback(
    db_conn: psycopg2.extensions.connection,
    test_schema: str,
) -> None:
    """A failed COPY must roll back table rows and record FAILED in ingestion_log."""
    with tempfile.TemporaryDirectory() as td:
        bad_dir = Path(td)
        bad_file = bad_dir / "customers.csv"
        # Write malformed CSV with unknown column so COPY fails schema expectation
        bad_file.write_text("customer_id,nonexistent_column\nC999,bad_val\n", encoding="utf-8")

        res = ingest_source(
            conn=db_conn,
            source_name="customers",
            raw_dir=bad_dir,
            force=True,
            schema=test_schema,
        )

        assert res["status"] == "FAILED"
        assert res["rows_loaded"] == 0
        assert "FAILED" in res["message"]

        # Confirm failure was audited in ingestion_log
        with db_conn.cursor() as cur:
            cur.execute(
                f"SELECT status, error_message FROM {test_schema}.ingestion_log "
                "WHERE batch_id = %s",
                (res["batch_id"],),
            )
            log_row = cur.fetchone()
            assert log_row is not None
            assert log_row[0] == "FAILED"
            assert log_row[1] is not None
