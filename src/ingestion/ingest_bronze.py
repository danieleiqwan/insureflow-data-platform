"""Bronze Layer Ingestion for InsureFlow (Phase 3).

Loads raw source CSV files from data/raw/ into PostgreSQL schema `bronze`.
- bronze.customers
- bronze.policies
- bronze.claims
- bronze.payments
- bronze.facilities_master
- bronze.ingestion_log

Characteristics:
- Append-only.
- All business columns stored as TEXT (no type coercion, no cleaning, no dedupe).
- Metadata columns: _batch_id (UUID), _source_file (TEXT), _source_row_number (INT),
  _ingested_at (TIMESTAMPTZ).
- Each file loads in ONE transaction using COPY.
- Idempotent: skips if file SHA256 already recorded as SUCCESS in bronze.ingestion_log,
  unless --force is passed.
- Row counts in bronze verified against CSV data rows.

Usage:
    python src/ingestion/ingest_bronze.py [--sources all|customers,policies,...] [--force]
    python -m src.ingestion.ingest_bronze
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path when invoked directly as a script
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
import psycopg2
from psycopg2.extensions import connection as PgConnection

# ─── Source Definitions & Column Mappings ─────────────────────────────────────

FACILITIES_COLUMN_MAPPING: Dict[str, str] = {
    "Index": "index",
    "KOD_FASILITI": "kod_fasiliti",
    "STATUS": "status",
    "SEKTOR": "sektor",
    "SUBSEKTOR": "subsektor",
    "PROGRAM_GROUP": "program_group",
    "NEGERI": "negeri",
    "DAERAH": "daerah",
    "KATEGORI_FASILITI": "kategori_fasiliti",
    "JENIS_FASILITI": "jenis_fasiliti",
    "NAMA": "nama",
    "ALAMAT": "alamat",
    "BANDAR": "bandar",
    "POSKOD": "poskod",
    "DAERAH_PENTADBIRAN": "daerah_pentadbiran",
    "TELEFON": "telefon",
    "EMEL": "emel",
    "URBAN_RURAL": "urban_rural",
    "LATITUD": "latitud",
    "LONGITUD": "longitud",
}

SOURCE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "customers": {
        "file_name": "customers.csv",
        "table_name": "customers",
        "column_mapper": lambda h: h,
    },
    "policies": {
        "file_name": "policies.csv",
        "table_name": "policies",
        "column_mapper": lambda h: h,
    },
    "claims": {
        "file_name": "claims.csv",
        "table_name": "claims",
        "column_mapper": lambda h: h,
    },
    "payments": {
        "file_name": "payments.csv",
        "table_name": "payments",
        "column_mapper": lambda h: h,
    },
    "facilities_master": {
        "file_name": "facilities_master.csv",
        "table_name": "facilities_master",
        "column_mapper": lambda h: [FACILITIES_COLUMN_MAPPING.get(c, c.lower()) for c in h],
    },
}

ALL_SOURCES: List[str] = list(SOURCE_CONFIGS.keys())


# ─── Utility Functions ────────────────────────────────────────────────────────

def compute_file_sha256(path: Path) -> str:
    """Compute 64-character SHA256 hex digest of file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def get_db_connection(schema: str = "bronze") -> PgConnection:
    """Create a psycopg2 connection using environment variables."""
    load_dotenv(_REPO_ROOT / ".env")
    conn = psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "insureflow"),
        user=os.getenv("POSTGRES_USER", "insureflow_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
    )
    return conn


def is_sha256_loaded(cur, file_sha256: str, schema: str = "bronze") -> bool:
    """Check if the given file SHA256 was successfully ingested previously."""
    cur.execute(
        f"SELECT COUNT(*) FROM {schema}.ingestion_log "
        "WHERE file_sha256 = %s AND status = 'SUCCESS'",
        (file_sha256,),
    )
    return cur.fetchone()[0] > 0


def log_ingestion_result(
    conn: PgConnection,
    batch_id: uuid.UUID,
    source_file: str,
    file_sha256: str,
    file_size_bytes: int,
    rows_loaded: int,
    status: str,
    error_message: Optional[str],
    started_at: datetime,
    finished_at: datetime,
    schema: str = "bronze",
) -> None:
    """Write an audit entry into bronze.ingestion_log outside the failed load transaction."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {schema}.ingestion_log (
                    batch_id, source_file, file_sha256, file_size_bytes,
                    rows_loaded, status, error_message, started_at, finished_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(batch_id),
                    source_file,
                    file_sha256,
                    file_size_bytes,
                    rows_loaded,
                    status,
                    error_message,
                    started_at,
                    finished_at,
                ),
            )


# ─── Ingestion Core ───────────────────────────────────────────────────────────

def ingest_source(
    conn: PgConnection,
    source_name: str,
    raw_dir: Path,
    force: bool = False,
    schema: str = "bronze",
) -> Dict[str, Any]:
    """Ingest a single source file into the bronze schema.

    Returns dict summarizing the result:
      {
        "source": source_name,
        "status": "SUCCESS" | "SKIPPED" | "FAILED",
        "rows_loaded": int,
        "batch_id": Optional[str],
        "message": str,
      }
    """
    config = SOURCE_CONFIGS[source_name]
    file_path = raw_dir / config["file_name"]
    table_name = f"{schema}.{config['table_name']}"

    # Check existence
    if not file_path.exists():
        if source_name == "facilities_master":
            msg = (
                f"Source file {file_path} not found. "
                "Please run 'python src/ingestion/download_sources.py' first."
            )
        else:
            msg = f"Source file {file_path} not found."
        raise FileNotFoundError(msg)

    # Compute metadata
    file_sha256 = compute_file_sha256(file_path)
    file_size_bytes = file_path.stat().st_size
    batch_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    # Check idempotency
    with conn.cursor() as cur:
        if not force and is_sha256_loaded(cur, file_sha256, schema=schema):
            return {
                "source": source_name,
                "status": "SKIPPED",
                "rows_loaded": 0,
                "batch_id": None,
                "file_sha256": file_sha256,
                "message": "skipped: already loaded",
            }

    # Prepare data in memory buffer for COPY
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    ingested_at_iso = started_at.isoformat()

    row_count = 0
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        target_columns = config["column_mapper"](header)

        for row_idx, row in enumerate(reader, start=1):
            row_count += 1
            # Row structure: [_batch_id, _source_file, _source_row_number, _ingested_at, <raw_columns...>]
            writer.writerow([str(batch_id), config["file_name"], row_idx, ingested_at_iso, *row])

    buffer.seek(0)

    # Single-transaction COPY with rollback on failure
    try:
        with conn:
            with conn.cursor() as cur:
                all_cols = ["_batch_id", "_source_file", "_source_row_number", "_ingested_at"] + target_columns
                cols_sql = ", ".join(f'"{c}"' for c in all_cols)
                copy_sql = f"COPY {table_name} ({cols_sql}) FROM STDIN WITH (FORMAT CSV, HEADER FALSE, DELIMITER ',')"
                cur.copy_expert(copy_sql, buffer)

                # Verify row counts match exactly
                cur.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE _batch_id = %s",
                    (str(batch_id),),
                )
                db_count = cur.fetchone()[0]
                if db_count != row_count:
                    raise ValueError(
                        f"Row count mismatch in {table_name}: CSV had {row_count} rows, DB loaded {db_count} rows"
                    )

        finished_at = datetime.now(timezone.utc)
        log_ingestion_result(
            conn=conn,
            batch_id=batch_id,
            source_file=config["file_name"],
            file_sha256=file_sha256,
            file_size_bytes=file_size_bytes,
            rows_loaded=row_count,
            status="SUCCESS",
            error_message=None,
            started_at=started_at,
            finished_at=finished_at,
            schema=schema,
        )
        return {
            "source": source_name,
            "status": "SUCCESS",
            "rows_loaded": row_count,
            "batch_id": str(batch_id),
            "file_sha256": file_sha256,
            "message": f"loaded {row_count:,} rows into {table_name}",
        }

    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        err_msg = str(exc)
        # Log FAILED entry in separate transaction
        try:
            log_ingestion_result(
                conn=conn,
                batch_id=batch_id,
                source_file=config["file_name"],
                file_sha256=file_sha256,
                file_size_bytes=file_size_bytes,
                rows_loaded=0,
                status="FAILED",
                error_message=err_msg,
                started_at=started_at,
                finished_at=finished_at,
                schema=schema,
            )
        except Exception as log_exc:
            err_msg += f" (also failed to write failure log: {log_exc})"
        return {
            "source": source_name,
            "status": "FAILED",
            "rows_loaded": 0,
            "batch_id": str(batch_id),
            "file_sha256": file_sha256,
            "message": f"FAILED: {err_msg}",
        }


def run_pipeline(
    sources: Optional[List[str]] = None,
    force: bool = False,
    raw_dir: Optional[Path] = None,
    schema: str = "bronze",
) -> List[Dict[str, Any]]:
    """Run bronze ingestion for the specified sources."""
    if sources is None:
        target_sources = ALL_SOURCES
    else:
        target_sources = sources

    if raw_dir is None:
        raw_dir = _REPO_ROOT / "data" / "raw"

    conn = get_db_connection(schema=schema)
    results: List[Dict[str, Any]] = []
    try:
        for src in target_sources:
            if src not in SOURCE_CONFIGS:
                raise ValueError(f"Unknown source '{src}'. Valid sources: {', '.join(ALL_SOURCES)}")
            res = ingest_source(conn, src, raw_dir=raw_dir, force=force, schema=schema)
            results.append(res)
    finally:
        conn.close()

    return results


# ─── CLI Entrypoint ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Ingest raw CSV files into InsureFlow Bronze layer.")
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated list of sources to ingest (e.g. customers,policies) or 'all' (default: all).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force ingestion as a new batch even if the file SHA256 has already been loaded.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI execution entry point."""
    args = parse_args()
    if args.sources.strip().lower() == "all":
        sources = ALL_SOURCES
    else:
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    print("=" * 60)
    print("InsureFlow Phase 3 -- Bronze Layer Ingestion")
    print(f"Sources : {', '.join(sources)}")
    print(f"Force   : {args.force}")
    print("=" * 60)

    try:
        results = run_pipeline(sources=sources, force=args.force)
    except FileNotFoundError as err:
        print(f"\n[ERROR] {err}", file=sys.stderr)
        sys.exit(1)
    except Exception as err:
        print(f"\n[FATAL] Database or pipeline error: {err}", file=sys.stderr)
        sys.exit(1)

    print("\n--- Ingestion Summary ---")
    any_failed = False
    for r in results:
        status_tag = f"[{r['status']}]"
        print(f"{status_tag:10} {r['source']:18}: {r['message']}")
        if r["status"] == "FAILED":
            any_failed = True

    print("=" * 60)
    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
