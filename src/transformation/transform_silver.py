"""Silver Layer Transformation for InsureFlow (Phase 4A).

Transforms untyped, append-only Bronze data into typed, constrained, and validated
Silver tables in PostgreSQL schema `silver`:
- silver.facilities
- silver.customers
- silver.policies
- silver.claims
- silver.payments
- silver.rejected_rows

Characteristics:
- Full refresh: TRUNCATE target silver tables and reload in a single database transaction.
- Reads only the latest batch per source table from bronze.* (determined from
  bronze.ingestion_log where status = 'SUCCESS', falling back to latest _batch_id in the table).
- Explicit type casting: dates, numerics (Decimal), timestamps, enum checks.
- Topological load order respecting referential integrity:
  facilities & customers -> policies -> claims -> payments.
- Non-crashing row-level validation: invalid or referentially broken rows are routed
  to silver.rejected_rows with detailed reject reasons, raw row payload (JSONB), and batch provenance.
- Clean summary report of rows read, rows loaded, and rows rejected per table.

Usage:
    python src/transformation/transform_silver.py
    python -m src.transformation.transform_silver
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure repository root is on sys.path when invoked directly as a script
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import execute_values, Json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Allowed Domain Values (matching CHECK constraints) ────────────────────────

ALLOWED_GENDERS: Set[str] = {"Male", "Female"}
ALLOWED_POLICY_TYPES: Set[str] = {"MEDICAL", "HOSPITALIZATION", "CRITICAL_ILLNESS", "PERSONAL_ACCIDENT"}
ALLOWED_POLICY_STATUSES: Set[str] = {"ACTIVE", "EXPIRED", "CANCELLED", "LAPSED"}
ALLOWED_CLAIM_TYPES: Set[str] = {"OUTPATIENT", "INPATIENT", "EMERGENCY", "DENTAL"}
ALLOWED_CLAIM_STATUSES: Set[str] = {"SUBMITTED", "APPROVED", "PARTIALLY_APPROVED", "REJECTED"}
ALLOWED_PAYMENT_METHODS: Set[str] = {"BANK_TRANSFER", "CHEQUE", "CARD", "E_WALLET"}
ALLOWED_PAYMENT_STATUSES: Set[str] = {"PENDING", "COMPLETED", "FAILED"}


# ─── Pure Parsing & Validation Helpers ────────────────────────────────────────

def parse_date_str(val: Optional[str], field_name: str) -> Tuple[Optional[date], Optional[str]]:
    """Parse YYYY-MM-DD date string. Returns (date_obj, error_message)."""
    if not val or not str(val).strip():
        return None, f"Missing required date field: {field_name}"
    s = str(val).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"Invalid date format for {field_name}: '{s}' (expected YYYY-MM-DD)"


def parse_decimal_val(
    val: Optional[str],
    field_name: str,
    min_val: Optional[Decimal] = None,
    max_val: Optional[Decimal] = None,
    allow_null: bool = False,
) -> Tuple[Optional[Decimal], Optional[str]]:
    """Parse numeric Decimal string with bounds checking. Returns (Decimal, error_message)."""
    if val is None or str(val).strip() == "" or str(val).strip().upper() in ("NONE", "NULL"):
        if allow_null:
            return None, None
        return None, f"Missing required numeric field: {field_name}"
    s = str(val).strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None, f"Invalid numeric value for {field_name}: '{s}'"

    if min_val is not None and d < min_val:
        return None, f"Constraint violation for {field_name}: {d} is less than minimum {min_val}"
    if max_val is not None and d > max_val:
        return None, f"Constraint violation for {field_name}: {d} exceeds maximum {max_val}"
    return d, None


def parse_timestamp_str(val: Optional[str], field_name: str, allow_default_now: bool = False) -> Tuple[Optional[datetime], Optional[str]]:
    """Parse ISO timestamptz string. Returns (datetime_obj, error_message)."""
    if not val or not str(val).strip():
        if allow_default_now:
            return datetime.now(timezone.utc), None
        return None, f"Missing required timestamp field: {field_name}"
    s = str(val).strip()
    try:
        return datetime.fromisoformat(s), None
    except ValueError:
        return None, f"Invalid timestamp format for {field_name}: '{s}'"


# ─── Entity Validation Functions ──────────────────────────────────────────────

def validate_and_transform_facility(
    row: Dict[str, Any],
    seen_ids: Set[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate and transform a bronze.facilities_master row for silver.facilities."""
    fac_id = (row.get("kod_fasiliti") or "").strip()
    if not fac_id:
        return None, "Missing required field: kod_fasiliti"
    if len(fac_id) > 15:
        return None, f"facility_id exceeds max length 15: '{fac_id}'"
    if fac_id in seen_ids:
        return None, f"Duplicate facility_id '{fac_id}' in batch"

    raw_name = (row.get("nama") or "").strip()
    if not raw_name:
        return None, "Missing required field: nama"
    fac_name = re.sub(r"\s+", " ", raw_name)
    if len(fac_name) > 200:
        return None, f"facility_name exceeds max length 200: '{fac_name[:40]}...'"

    category = (row.get("kategori_fasiliti") or "").strip()
    if not category:
        return None, "Missing required field: kategori_fasiliti"
    if len(category) > 50:
        return None, f"facility_category exceeds max length 50: '{category}'"

    fac_type = (row.get("jenis_fasiliti") or "").strip()
    if not fac_type:
        return None, "Missing required field: jenis_fasiliti"
    if len(fac_type) > 100:
        return None, f"facility_type exceeds max length 100: '{fac_type}'"

    subsector = (row.get("subsektor") or "").strip()
    if not subsector:
        return None, "Missing required field: subsektor"
    if len(subsector) > 10:
        return None, f"subsector exceeds max length 10: '{subsector}'"

    state = (row.get("negeri") or "").strip()
    if not state:
        return None, "Missing required field: negeri"
    if len(state) > 60:
        return None, f"state exceeds max length 60: '{state}'"

    district = (row.get("daerah") or "").strip()
    if not district:
        return None, "Missing required field: daerah"
    if len(district) > 60:
        return None, f"district exceeds max length 60: '{district}'"

    postcode = (row.get("poskod") or "").strip() or None
    if postcode and len(postcode) > 10:
        return None, f"postcode exceeds max length 10: '{postcode}'"

    lat, lat_err = parse_decimal_val(row.get("latitud"), "latitude")
    if lat_err:
        return None, lat_err

    lon, lon_err = parse_decimal_val(row.get("longitud"), "longitude")
    if lon_err:
        return None, lon_err

    created_at, _ = parse_timestamp_str(row.get("_ingested_at"), "created_at", allow_default_now=True)

    seen_ids.add(fac_id)
    return {
        "facility_id": fac_id,
        "facility_name": fac_name,
        "facility_category": category,
        "facility_type": fac_type,
        "subsector": subsector,
        "state": state,
        "district": district,
        "postcode": postcode,
        "latitude": lat,
        "longitude": lon,
        "created_at": created_at,
    }, None


def validate_and_transform_customer(
    row: Dict[str, Any],
    seen_ids: Set[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate and transform a bronze.customers row for silver.customers."""
    cust_id = (row.get("customer_id") or "").strip()
    if not cust_id:
        return None, "Missing required field: customer_id"
    if len(cust_id) > 7:
        return None, f"customer_id exceeds max length 7: '{cust_id}'"
    if cust_id in seen_ids:
        return None, f"Duplicate customer_id '{cust_id}' in batch"

    first_name = (row.get("first_name") or "").strip()
    if not first_name:
        return None, "Missing required field: first_name"
    if len(first_name) > 100:
        return None, f"first_name exceeds max length 100: '{first_name[:30]}...'"

    last_name = (row.get("last_name") or "").strip()
    if not last_name:
        return None, "Missing required field: last_name"
    if len(last_name) > 100:
        return None, f"last_name exceeds max length 100: '{last_name[:30]}...'"

    gender = (row.get("gender") or "").strip()
    if gender not in ALLOWED_GENDERS:
        return None, f"Invalid gender '{gender}': must be one of {sorted(ALLOWED_GENDERS)}"

    dob, dob_err = parse_date_str(row.get("date_of_birth"), "date_of_birth")
    if dob_err:
        return None, dob_err

    state = (row.get("state") or "").strip()
    if not state:
        return None, "Missing required field: state"
    if len(state) > 50:
        return None, f"state exceeds max length 50: '{state}'"

    occupation = (row.get("occupation") or "").strip()
    if not occupation:
        return None, "Missing required field: occupation"
    if len(occupation) > 100:
        return None, f"occupation exceeds max length 100: '{occupation}'"

    created_at, cat_err = parse_timestamp_str(row.get("created_at"), "created_at", allow_default_now=True)
    if cat_err:
        return None, cat_err

    seen_ids.add(cust_id)
    return {
        "customer_id": cust_id,
        "first_name": first_name,
        "last_name": last_name,
        "gender": gender,
        "date_of_birth": dob,
        "state": state,
        "occupation": occupation,
        "created_at": created_at,
    }, None


def validate_and_transform_policy(
    row: Dict[str, Any],
    valid_customer_ids: Set[str],
    seen_ids: Set[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate and transform a bronze.policies row for silver.policies."""
    pol_id = (row.get("policy_id") or "").strip()
    if not pol_id:
        return None, "Missing required field: policy_id"
    if len(pol_id) > 8:
        return None, f"policy_id exceeds max length 8: '{pol_id}'"
    if pol_id in seen_ids:
        return None, f"Duplicate policy_id '{pol_id}' in batch"

    cust_id = (row.get("customer_id") or "").strip()
    if not cust_id:
        return None, "Missing required field: customer_id"
    if cust_id not in valid_customer_ids:
        return None, f"FK violation: customer_id '{cust_id}' not found in silver.customers"

    pol_type = (row.get("policy_type") or "").strip()
    if pol_type not in ALLOWED_POLICY_TYPES:
        return None, f"Invalid policy_type '{pol_type}': must be one of {sorted(ALLOWED_POLICY_TYPES)}"

    start_date, start_err = parse_date_str(row.get("start_date"), "start_date")
    if start_err:
        return None, start_err

    end_date, end_err = parse_date_str(row.get("end_date"), "end_date")
    if end_err:
        return None, end_err

    if end_date < start_date:
        return None, f"Constraint violation: end_date ({end_date}) must be >= start_date ({start_date})"

    premium, prem_err = parse_decimal_val(row.get("premium"), "premium", min_val=Decimal("0"))
    if prem_err:
        return None, prem_err

    status = (row.get("status") or "").strip()
    if status not in ALLOWED_POLICY_STATUSES:
        return None, f"Invalid status '{status}': must be one of {sorted(ALLOWED_POLICY_STATUSES)}"

    created_at, cat_err = parse_timestamp_str(row.get("created_at"), "created_at", allow_default_now=True)
    if cat_err:
        return None, cat_err

    seen_ids.add(pol_id)
    return {
        "policy_id": pol_id,
        "customer_id": cust_id,
        "policy_type": pol_type,
        "start_date": start_date,
        "end_date": end_date,
        "premium": premium,
        "status": status,
        "created_at": created_at,
    }, None


def validate_and_transform_claim(
    row: Dict[str, Any],
    valid_policy_ids: Set[str],
    valid_facility_ids: Set[str],
    seen_ids: Set[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate and transform a bronze.claims row for silver.claims."""
    claim_id = (row.get("claim_id") or "").strip()
    if not claim_id:
        return None, "Missing required field: claim_id"
    if len(claim_id) > 9:
        return None, f"claim_id exceeds max length 9: '{claim_id}'"
    if claim_id in seen_ids:
        return None, f"Duplicate claim_id '{claim_id}' in batch"

    pol_id = (row.get("policy_id") or "").strip()
    if not pol_id:
        return None, "Missing required field: policy_id"
    if pol_id not in valid_policy_ids:
        return None, f"FK violation: policy_id '{pol_id}' not found in silver.policies"

    fac_id = (row.get("facility_id") or "").strip()
    if not fac_id:
        return None, "Missing required field: facility_id"
    if fac_id not in valid_facility_ids:
        return None, f"FK violation: facility_id '{fac_id}' not found in silver.facilities"

    claim_date, cdate_err = parse_date_str(row.get("claim_date"), "claim_date")
    if cdate_err:
        return None, cdate_err

    claim_type = (row.get("claim_type") or "").strip()
    if claim_type not in ALLOWED_CLAIM_TYPES:
        return None, f"Invalid claim_type '{claim_type}': must be one of {sorted(ALLOWED_CLAIM_TYPES)}"

    claim_amount, camt_err = parse_decimal_val(row.get("claim_amount"), "claim_amount", min_val=Decimal("0"))
    if camt_err:
        return None, camt_err

    raw_app = row.get("approved_amount")
    approved_amount, app_err = parse_decimal_val(raw_app, "approved_amount", min_val=Decimal("0"), allow_null=True)
    if app_err:
        return None, app_err

    if approved_amount is not None and approved_amount > claim_amount:
        return (
            None,
            f"Constraint violation: approved_amount ({approved_amount}) exceeds claim_amount ({claim_amount})",
        )

    status = (row.get("status") or "").strip()
    if status not in ALLOWED_CLAIM_STATUSES:
        return None, f"Invalid status '{status}': must be one of {sorted(ALLOWED_CLAIM_STATUSES)}"

    created_at, cat_err = parse_timestamp_str(row.get("created_at"), "created_at", allow_default_now=True)
    if cat_err:
        return None, cat_err

    seen_ids.add(claim_id)
    return {
        "claim_id": claim_id,
        "policy_id": pol_id,
        "facility_id": fac_id,
        "claim_date": claim_date,
        "claim_type": claim_type,
        "claim_amount": claim_amount,
        "approved_amount": approved_amount,
        "status": status,
        "created_at": created_at,
    }, None


def validate_and_transform_payment(
    row: Dict[str, Any],
    valid_claim_ids: Set[str],
    seen_ids: Set[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate and transform a bronze.payments row for silver.payments."""
    pay_id = (row.get("payment_id") or "").strip()
    if not pay_id:
        return None, "Missing required field: payment_id"
    if len(pay_id) > 9:
        return None, f"payment_id exceeds max length 9: '{pay_id}'"
    if pay_id in seen_ids:
        return None, f"Duplicate payment_id '{pay_id}' in batch"

    claim_id = (row.get("claim_id") or "").strip()
    if not claim_id:
        return None, "Missing required field: claim_id"
    if claim_id not in valid_claim_ids:
        return None, f"FK violation: claim_id '{claim_id}' not found in silver.claims"

    pay_date, pdate_err = parse_date_str(row.get("payment_date"), "payment_date")
    if pdate_err:
        return None, pdate_err

    amount, amt_err = parse_decimal_val(row.get("amount"), "amount", min_val=Decimal("0"))
    if amt_err:
        return None, amt_err

    method = (row.get("payment_method") or "").strip()
    if method not in ALLOWED_PAYMENT_METHODS:
        return None, f"Invalid payment_method '{method}': must be one of {sorted(ALLOWED_PAYMENT_METHODS)}"

    status = (row.get("status") or "").strip()
    if status not in ALLOWED_PAYMENT_STATUSES:
        return None, f"Invalid status '{status}': must be one of {sorted(ALLOWED_PAYMENT_STATUSES)}"

    created_at, cat_err = parse_timestamp_str(row.get("created_at"), "created_at", allow_default_now=True)
    if cat_err:
        return None, cat_err

    seen_ids.add(pay_id)
    return {
        "payment_id": pay_id,
        "claim_id": claim_id,
        "payment_date": pay_date,
        "amount": amount,
        "payment_method": method,
        "status": status,
        "created_at": created_at,
    }, None


# ─── Database Operations ──────────────────────────────────────────────────────

def get_db_connection() -> PgConnection:
    """Create a psycopg2 connection using environment variables from .env."""
    load_dotenv(_REPO_ROOT / ".env")
    return psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "insureflow"),
        user=os.getenv("POSTGRES_USER", "insureflow_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
    )


def get_latest_batch_id(
    cur,
    source_table: str,
    source_file: str,
    bronze_schema: str = "bronze",
) -> Optional[uuid.UUID]:
    """Find the most recent batch_id for a source.

    Strategy:
    First queries bronze.ingestion_log for the most recent SUCCESS batch.
    If no entry is found (e.g. in test fixtures where rows were inserted
    directly into bronze tables), falls back to querying the latest _batch_id
    from the bronze table.
    """
    try:
        cur.execute(
            f"""
            SELECT batch_id FROM {bronze_schema}.ingestion_log
            WHERE source_file = %s AND status = 'SUCCESS'
            ORDER BY finished_at DESC, started_at DESC
            LIMIT 1
            """,
            (source_file,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))
    except Exception:
        pass

    try:
        cur.execute(
            f"""
            SELECT _batch_id FROM {bronze_schema}.{source_table}
            ORDER BY _ingested_at DESC, _source_row_number DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))
    except Exception:
        pass

    return None


def fetch_bronze_rows(
    cur,
    source_table: str,
    batch_id: uuid.UUID,
    bronze_schema: str = "bronze",
) -> List[Dict[str, Any]]:
    """Fetch all rows from bronze table for a specific batch in source order."""
    cur.execute(
        f"""
        SELECT * FROM {bronze_schema}.{source_table}
        WHERE _batch_id = %s
        ORDER BY _source_row_number ASC
        """,
        (str(batch_id),),
    )
    colnames = [desc[0] for desc in cur.description]
    return [dict(zip(colnames, row)) for row in cur.fetchall()]


def truncate_silver_tables(cur, silver_schema: str = "silver") -> None:
    """Truncate all silver tables and silver.rejected_rows in single statement."""
    cur.execute(
        f"""
        TRUNCATE TABLE
            {silver_schema}.payments,
            {silver_schema}.claims,
            {silver_schema}.policies,
            {silver_schema}.customers,
            {silver_schema}.facilities,
            {silver_schema}.rejected_rows
        CASCADE
        """
    )


def insert_silver_rows(
    cur,
    table_name: str,
    rows: List[Dict[str, Any]],
    silver_schema: str = "silver",
) -> int:
    """Insert validated rows into target silver table."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    col_str = ", ".join(cols)
    sql = f"INSERT INTO {silver_schema}.{table_name} ({col_str}) VALUES %s"
    tuples = [tuple(r[c] for c in cols) for r in rows]
    execute_values(cur, sql, tuples, page_size=1000)
    return len(rows)


def _json_serial(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return str(obj)


def insert_rejected_rows(
    cur,
    rejected_records: List[Dict[str, Any]],
    silver_schema: str = "silver",
) -> int:
    """Insert rejected rows into silver.rejected_rows."""
    if not rejected_records:
        return 0
    sql = f"""
        INSERT INTO {silver_schema}.rejected_rows
            (source_table, source_batch_id, source_row_number, reject_reason, raw_row, rejected_at)
        VALUES %s
    """
    tuples = [
        (
            r["source_table"],
            str(r["source_batch_id"]) if r.get("source_batch_id") else None,
            r.get("source_row_number"),
            r["reject_reason"],
            Json(r["raw_row"], dumps=lambda o: json.dumps(o, default=_json_serial)),
            r.get("rejected_at", datetime.now(timezone.utc)),
        )
        for r in rejected_records
    ]
    execute_values(cur, sql, tuples, page_size=1000)
    return len(rejected_records)


# ─── Transformation Pipeline Orchestration ────────────────────────────────────

SOURCE_MAPPINGS = [
    {
        "silver_table": "facilities",
        "bronze_table": "facilities_master",
        "source_file": "facilities_master.csv",
        "pk_field": "facility_id",
    },
    {
        "silver_table": "customers",
        "bronze_table": "customers",
        "source_file": "customers.csv",
        "pk_field": "customer_id",
    },
    {
        "silver_table": "policies",
        "bronze_table": "policies",
        "source_file": "policies.csv",
        "pk_field": "policy_id",
    },
    {
        "silver_table": "claims",
        "bronze_table": "claims",
        "source_file": "claims.csv",
        "pk_field": "claim_id",
    },
    {
        "silver_table": "payments",
        "bronze_table": "payments",
        "source_file": "payments.csv",
        "pk_field": "payment_id",
    },
]


def transform_silver(
    conn: PgConnection,
    silver_schema: str = "silver",
    bronze_schema: str = "bronze",
) -> Dict[str, Any]:
    """Execute the Silver full refresh transformation inside a single transaction.

    Returns a summary dictionary with per-table metrics and totals.
    """
    cur = conn.cursor()

    # Step 1: Truncate existing silver tables
    logger.info("Truncating silver tables for full refresh...")
    truncate_silver_tables(cur, silver_schema=silver_schema)

    summary: Dict[str, Any] = {
        "tables": {},
        "total_rows_read": 0,
        "total_rows_loaded": 0,
        "total_rows_rejected": 0,
    }

    # Tracking valid PK sets across stages to enforce referential integrity
    valid_facility_ids: Set[str] = set()
    valid_customer_ids: Set[str] = set()
    valid_policy_ids: Set[str] = set()
    valid_claim_ids: Set[str] = set()

    all_rejected: List[Dict[str, Any]] = []

    for mapping in SOURCE_MAPPINGS:
        tbl = mapping["silver_table"]
        bronze_tbl = mapping["bronze_table"]
        source_file = mapping["source_file"]

        batch_id = get_latest_batch_id(cur, bronze_tbl, source_file, bronze_schema=bronze_schema)
        if not batch_id:
            logger.warning("No batch found for %s.%s (%s)", bronze_schema, bronze_tbl, source_file)
            summary["tables"][tbl] = {
                "batch_id": None,
                "rows_read": 0,
                "rows_loaded": 0,
                "rows_rejected": 0,
            }
            continue

        raw_rows = fetch_bronze_rows(cur, bronze_tbl, batch_id, bronze_schema=bronze_schema)
        logger.info("Processing %s: latest batch %s (%d rows read)", tbl, batch_id, len(raw_rows))

        valid_rows: List[Dict[str, Any]] = []
        table_rejected: List[Dict[str, Any]] = []
        seen_pks: Set[str] = set()

        for r in raw_rows:
            source_row_num = r.get("_source_row_number")
            source_batch = r.get("_batch_id") or batch_id

            try:
                if tbl == "facilities":
                    transformed, err = validate_and_transform_facility(r, seen_pks)
                elif tbl == "customers":
                    transformed, err = validate_and_transform_customer(r, seen_pks)
                elif tbl == "policies":
                    transformed, err = validate_and_transform_policy(r, valid_customer_ids, seen_pks)
                elif tbl == "claims":
                    transformed, err = validate_and_transform_claim(r, valid_policy_ids, valid_facility_ids, seen_pks)
                elif tbl == "payments":
                    transformed, err = validate_and_transform_payment(r, valid_claim_ids, seen_pks)
                else:
                    transformed, err = None, f"Unknown target table: {tbl}"
            except Exception as exc:
                transformed, err = None, f"Unexpected error during transformation: {exc}"

            if err or transformed is None:
                table_rejected.append({
                    "source_table": tbl,
                    "source_batch_id": source_batch,
                    "source_row_number": source_row_num,
                    "reject_reason": err or "Validation failed",
                    "raw_row": r,
                    "rejected_at": datetime.now(timezone.utc),
                })
            else:
                valid_rows.append(transformed)

        # Insert valid rows
        n_loaded = insert_silver_rows(cur, tbl, valid_rows, silver_schema=silver_schema)

        # Update in-memory referential sets
        if tbl == "facilities":
            valid_facility_ids = {r["facility_id"] for r in valid_rows}
        elif tbl == "customers":
            valid_customer_ids = {r["customer_id"] for r in valid_rows}
        elif tbl == "policies":
            valid_policy_ids = {r["policy_id"] for r in valid_rows}
        elif tbl == "claims":
            valid_claim_ids = {r["claim_id"] for r in valid_rows}

        all_rejected.extend(table_rejected)

        summary["tables"][tbl] = {
            "batch_id": str(batch_id),
            "rows_read": len(raw_rows),
            "rows_loaded": n_loaded,
            "rows_rejected": len(table_rejected),
        }
        summary["total_rows_read"] += len(raw_rows)
        summary["total_rows_loaded"] += n_loaded
        summary["total_rows_rejected"] += len(table_rejected)

    # Insert all collected rejected rows
    if all_rejected:
        insert_rejected_rows(cur, all_rejected, silver_schema=silver_schema)

    return summary


def print_summary(summary: Dict[str, Any]) -> None:
    """Print a clean execution report to stdout."""
    print("\n" + "=" * 65)
    print("InsureFlow Silver Layer Transformation Summary (Phase 4A)")
    print("=" * 65)
    print(f"{'Table':<15} {'Latest Batch ID':<38} {'Read':>6} {'Loaded':>7} {'Rejected':>8}")
    print("-" * 65)
    for tbl, info in summary["tables"].items():
        bid = info["batch_id"] or "None"
        if len(bid) > 36:
            bid = bid[:36]
        print(f"{tbl:<15} {bid:<38} {info['rows_read']:>6,d} {info['rows_loaded']:>7,d} {info['rows_rejected']:>8,d}")
    print("-" * 65)
    print(f"{'TOTAL':<15} {'':<38} {summary['total_rows_read']:>6,d} {summary['total_rows_loaded']:>7,d} {summary['total_rows_rejected']:>8,d}")
    print("=" * 65 + "\n")


def main() -> None:
    """CLI entry point for Silver transformation."""
    parser = argparse.ArgumentParser(description="Transform Bronze data into Silver schema.")
    parser.add_argument("--silver-schema", default="silver", help="Target Silver schema (default: silver)")
    parser.add_argument("--bronze-schema", default="bronze", help="Source Bronze schema (default: bronze)")
    args = parser.parse_args()

    conn = get_db_connection()
    conn.autocommit = False

    try:
        summary = transform_silver(
            conn,
            silver_schema=args.silver_schema,
            bronze_schema=args.bronze_schema,
        )
        conn.commit()
        print_summary(summary)
    except Exception as exc:
        conn.rollback()
        logger.error("Transformation failed and was rolled back: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
