"""Data Quality Defect Injector for InsureFlow (Phase 4B).

Reads clean CSVs from data/raw/ and injects realistic defects into separate
copies in data/sample/ without modifying clean data:
- customers_dirty.csv
- policies_dirty.csv
- claims_dirty.csv
- payments_dirty.csv

Records every injected defect in data/sample/defect_manifest.csv:
(file, row_business_key, defect_category, column_affected, original_value, injected_value)

Defect Categories (~1-2% of rows each, applied independently):
1. missing_value: Required value replaced with empty string / null.
2. duplicate_row: Same business key duplicated with minor attribute variation.
3. out_of_range: Logical domain violations (e.g. approved > claim amount, future dates).
4. malformed_format: Formatting issues (e.g. DD/MM/YYYY dates, non-numeric values, invalid enums).

Deterministic:
Uses a fixed random seed. Same seed produces byte-identical dirty CSVs and manifest.

Usage:
    python src/quality/inject_defects.py
    python -m src.quality.inject_defects --seed 42 --rate 0.015
"""

from __future__ import annotations

import argparse
import copy
import csv
import logging
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SEED = 42
DEFAULT_RATE = 0.015  # ~1.5% per category

TABLE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "customers": {
        "file_name": "customers.csv",
        "dirty_file": "customers_dirty.csv",
        "key_col": "customer_id",
        "required_cols": ["first_name", "last_name", "gender", "date_of_birth", "state", "occupation"],
    },
    "policies": {
        "file_name": "policies.csv",
        "dirty_file": "policies_dirty.csv",
        "key_col": "policy_id",
        "required_cols": ["policy_type", "start_date", "end_date", "premium", "status"],
    },
    "claims": {
        "file_name": "claims.csv",
        "dirty_file": "claims_dirty.csv",
        "key_col": "claim_id",
        "required_cols": ["claim_date", "claim_type", "claim_amount", "approved_amount", "status"],
    },
    "payments": {
        "file_name": "payments.csv",
        "dirty_file": "payments_dirty.csv",
        "key_col": "payment_id",
        "required_cols": ["payment_date", "amount", "payment_method", "status"],
    },
}


def read_csv_rows(file_path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read CSV file into a list of row dicts and return (headers, rows)."""
    with open(file_path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


def write_csv_rows(file_path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    """Write list of row dicts to CSV using Unix newlines for determinism."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, mode="w", encoding="utf-8", newline="\n") as f:
        writer = csv.DictWriter(f, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def inject_missing_values(
    table_name: str,
    rows: List[Dict[str, str]],
    clean_rows: List[Dict[str, str]],
    key_col: str,
    required_cols: List[str],
    indices: List[int],
    rng: random.Random,
    dirty_filename: str,
) -> List[Dict[str, str]]:
    """Category 1: Inject missing values in required non-key columns."""
    manifest_entries: List[Dict[str, str]] = []
    for idx in indices:
        row = rows[idx]
        col = rng.choice(required_cols)
        orig_val = clean_rows[idx][col]
        row[col] = ""
        manifest_entries.append({
            "file": dirty_filename,
            "row_business_key": row[key_col],
            "defect_category": "missing_value",
            "column_affected": col,
            "original_value": orig_val,
            "injected_value": "",
        })
    return manifest_entries


def inject_duplicate_rows(
    table_name: str,
    rows: List[Dict[str, str]],
    key_col: str,
    indices: List[int],
    rng: random.Random,
    dirty_filename: str,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Category 2: Duplicate row with the same business key but minor difference."""
    manifest_entries: List[Dict[str, str]] = []
    duplicates_to_add: List[Dict[str, str]] = []

    for idx in indices:
        orig_row = rows[idx]
        dup_row = copy.deepcopy(orig_row)
        b_key = orig_row[key_col]

        # Apply minor variation depending on table
        if table_name == "customers":
            dup_row["occupation"] = f"{orig_row.get('occupation', 'Executive')} (Duplicate)"
        elif table_name == "policies":
            try:
                curr_prem = float(orig_row.get("premium", "1000.00"))
                dup_row["premium"] = f"{curr_prem + 15.50:.2f}"
            except ValueError:
                dup_row["status"] = "LAPSED"
        elif table_name == "claims":
            try:
                curr_amt = float(orig_row.get("claim_amount", "500.00"))
                dup_row["claim_amount"] = f"{curr_amt + 25.00:.2f}"
            except ValueError:
                dup_row["claim_type"] = "EMERGENCY"
        elif table_name == "payments":
            dup_row["status"] = "PENDING" if orig_row.get("status") != "PENDING" else "COMPLETED"

        duplicates_to_add.append(dup_row)
        manifest_entries.append({
            "file": dirty_filename,
            "row_business_key": b_key,
            "defect_category": "duplicate_row",
            "column_affected": key_col,
            "original_value": b_key,
            "injected_value": b_key,
        })

    return duplicates_to_add, manifest_entries


def inject_out_of_range(
    table_name: str,
    rows: List[Dict[str, str]],
    clean_rows: List[Dict[str, str]],
    key_col: str,
    indices: List[int],
    rng: random.Random,
    dirty_filename: str,
) -> List[Dict[str, str]]:
    """Category 3: Out-of-range values violating domain or cross-field constraints."""
    manifest_entries: List[Dict[str, str]] = []

    for i, idx in enumerate(indices):
        row = rows[idx]
        b_key = row[key_col]

        if table_name == "customers":
            # Future birth date or extreme age > 120
            col = "date_of_birth"
            orig_val = clean_rows[idx][col]
            new_val = "2035-08-14" if (i % 2 == 0) else "1875-01-01"
            row[col] = new_val

        elif table_name == "policies":
            variant = i % 3
            if variant == 0:
                # end_date before start_date
                col = "end_date"
                orig_val = clean_rows[idx][col]
                new_val = "2020-01-01"
            elif variant == 1:
                # Negative premium
                col = "premium"
                orig_val = clean_rows[idx][col]
                new_val = "-750.00"
            else:
                # Policy start_date in the future relative to 2026-01-01
                col = "start_date"
                orig_val = clean_rows[idx][col]
                new_val = "2028-06-01"
            row[col] = new_val

        elif table_name == "claims":
            variant = i % 3
            if variant == 0:
                # approved_amount > claim_amount
                col = "approved_amount"
                orig_val = clean_rows[idx][col]
                try:
                    c_amt = float(row.get("claim_amount", 500.0))
                    new_val = f"{c_amt + 5000.00:.2f}"
                except ValueError:
                    new_val = "99999.00"
            elif variant == 1:
                # Negative claim_amount
                col = "claim_amount"
                orig_val = clean_rows[idx][col]
                new_val = "-250.00"
            else:
                # claim_date in future relative to 2026-01-01
                col = "claim_date"
                orig_val = clean_rows[idx][col]
                new_val = "2027-09-15"
            row[col] = new_val

        elif table_name == "payments":
            variant = i % 2
            if variant == 0:
                # Negative payment amount
                col = "amount"
                orig_val = clean_rows[idx][col]
                new_val = "-100.00"
            else:
                # payment_date in future relative to 2026-01-01
                col = "payment_date"
                orig_val = clean_rows[idx][col]
                new_val = "2028-03-20"
            row[col] = new_val

        manifest_entries.append({
            "file": dirty_filename,
            "row_business_key": b_key,
            "defect_category": "out_of_range",
            "column_affected": col,
            "original_value": orig_val,
            "injected_value": new_val,
        })

    return manifest_entries


def inject_malformed_formats(
    table_name: str,
    rows: List[Dict[str, str]],
    clean_rows: List[Dict[str, str]],
    key_col: str,
    indices: List[int],
    rng: random.Random,
    dirty_filename: str,
) -> List[Dict[str, str]]:
    """Category 4: Malformed formats (e.g. non-ISO dates, non-numeric strings, invalid enums)."""
    manifest_entries: List[Dict[str, str]] = []

    for i, idx in enumerate(indices):
        row = rows[idx]
        b_key = row[key_col]

        if table_name == "customers":
            variant = i % 3
            if variant == 0:
                # Non-ISO date DD/MM/YYYY
                col = "date_of_birth"
                orig_val = clean_rows[idx][col]
                new_val = "24/03/1994"
            elif variant == 1:
                # Invalid gender enum
                col = "gender"
                orig_val = clean_rows[idx][col]
                new_val = "Non-Binary"
            else:
                # Invalid Malaysian state
                col = "state"
                orig_val = clean_rows[idx][col]
                new_val = "California"
            row[col] = new_val

        elif table_name == "policies":
            variant = i % 3
            if variant == 0:
                # Non-ISO date DD-MM-YYYY
                col = "start_date"
                orig_val = clean_rows[idx][col]
                new_val = "15-05-2025"
            elif variant == 1:
                # Invalid policy_type enum
                col = "policy_type"
                orig_val = clean_rows[idx][col]
                new_val = "PET_INSURANCE"
            else:
                # Non-numeric premium
                col = "premium"
                orig_val = clean_rows[idx][col]
                new_val = "1250.00_MYR"
            row[col] = new_val

        elif table_name == "claims":
            variant = i % 3
            if variant == 0:
                # Non-ISO date with slashes
                col = "claim_date"
                orig_val = clean_rows[idx][col]
                new_val = "2025/11/05"
            elif variant == 1:
                # Invalid claim_type enum
                col = "claim_type"
                orig_val = clean_rows[idx][col]
                new_val = "COSMETIC"
            else:
                # Non-numeric claim_amount
                col = "claim_amount"
                orig_val = clean_rows[idx][col]
                new_val = "INVALID_AMT"
            row[col] = new_val

        elif table_name == "payments":
            variant = i % 3
            if variant == 0:
                # Non-ISO date
                col = "payment_date"
                orig_val = clean_rows[idx][col]
                new_val = "09.08.2025"
            elif variant == 1:
                # Invalid payment_method enum
                col = "payment_method"
                orig_val = clean_rows[idx][col]
                new_val = "BITCOIN"
            else:
                # Non-numeric amount
                col = "amount"
                orig_val = clean_rows[idx][col]
                new_val = "N/A"
            row[col] = new_val

        manifest_entries.append({
            "file": dirty_filename,
            "row_business_key": b_key,
            "defect_category": "malformed_format",
            "column_affected": col,
            "original_value": orig_val,
            "injected_value": new_val,
        })

    return manifest_entries


def inject_defects_into_table(
    table_name: str,
    raw_csv_path: Path,
    output_dir: Path,
    rng: random.Random,
    rate: float,
) -> Tuple[int, List[Dict[str, str]]]:
    """Inject defects for one table and write dirty CSV. Returns (row_count, manifest_entries)."""
    config = TABLE_CONFIGS[table_name]
    headers, rows = read_csv_rows(raw_csv_path)
    n_rows = len(rows)
    key_col = config["key_col"]
    dirty_filename = config["dirty_file"]

    # Target defect count per category
    k = max(2, int(round(n_rows * rate)))

    # Sample row indices independently for all 4 categories
    cat1_indices = rng.sample(range(n_rows), k)
    cat2_indices = rng.sample(range(n_rows), k)
    cat3_indices = rng.sample(range(n_rows), k)
    cat4_indices = rng.sample(range(n_rows), k)

    # Deepcopy original clean rows to accurately record original values
    clean_rows = copy.deepcopy(rows)

    # 1. Missing values
    m1 = inject_missing_values(table_name, rows, clean_rows, key_col, config["required_cols"], cat1_indices, rng, dirty_filename)

    # 2. Duplicate rows
    duplicates, m2 = inject_duplicate_rows(table_name, rows, key_col, cat2_indices, rng, dirty_filename)

    # 3. Out-of-range
    m3 = inject_out_of_range(table_name, rows, clean_rows, key_col, cat3_indices, rng, dirty_filename)

    # 4. Malformed formats
    m4 = inject_malformed_formats(table_name, rows, clean_rows, key_col, cat4_indices, rng, dirty_filename)

    # Combine rows with duplicates
    all_rows = rows + duplicates

    dirty_path = output_dir / dirty_filename
    write_csv_rows(dirty_path, headers, all_rows)

    all_manifest = m1 + m2 + m3 + m4
    return len(all_rows), all_manifest


def inject_all_defects(
    raw_dir: Path,
    output_dir: Path,
    seed: int = DEFAULT_SEED,
    rate: float = DEFAULT_RATE,
) -> Dict[str, Any]:
    """Inject defects across all four clean data files deterministically."""
    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_records: List[Dict[str, str]] = []
    summary_counts: Dict[str, Dict[str, int]] = {}

    for table_name, config in TABLE_CONFIGS.items():
        raw_csv = raw_dir / config["file_name"]
        if not raw_csv.exists():
            raise FileNotFoundError(f"Clean source CSV not found: {raw_csv}")

        total_rows, table_manifest = inject_defects_into_table(
            table_name=table_name,
            raw_csv_path=raw_csv,
            output_dir=output_dir,
            rng=rng,
            rate=rate,
        )
        manifest_records.extend(table_manifest)

        # Compute summary counts per category
        cat_counts: Dict[str, int] = {
            "missing_value": 0,
            "duplicate_row": 0,
            "out_of_range": 0,
            "malformed_format": 0,
        }
        for entry in table_manifest:
            cat = entry["defect_category"]
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        summary_counts[config["dirty_file"]] = {
            "total_rows": total_rows,
            **cat_counts,
            "total_defects": len(table_manifest),
        }

    # Write manifest CSV
    manifest_headers = [
        "file",
        "row_business_key",
        "defect_category",
        "column_affected",
        "original_value",
        "injected_value",
    ]
    manifest_path = output_dir / "defect_manifest.csv"
    write_csv_rows(manifest_path, manifest_headers, manifest_records)

    return {
        "manifest_path": manifest_path,
        "total_defects": len(manifest_records),
        "summary": summary_counts,
    }


def print_summary(summary_data: Dict[str, Any]) -> None:
    """Print readable summary table of defect injection."""
    print("\n" + "=" * 80)
    print("INSUREFLOW DATA QUALITY — DEFECT INJECTION SUMMARY (Phase 4B)")
    print("=" * 80)
    print(f"{'Dirty File':<24} | {'Total Rows':<10} | {'Missing':<8} | {'Duplicate':<9} | {'Out of Range':<12} | {'Malformed':<9} | {'Total'}")
    print("-" * 80)
    for file_name, stats in summary_data["summary"].items():
        print(
            f"{file_name:<24} | "
            f"{stats['total_rows']:<10} | "
            f"{stats['missing_value']:<8} | "
            f"{stats['duplicate_row']:<9} | "
            f"{stats['out_of_range']:<12} | "
            f"{stats['malformed_format']:<9} | "
            f"{stats['total_defects']}"
        )
    print("-" * 80)
    print(f"Total injected defects across all files: {summary_data['total_defects']}")
    print(f"Manifest written to: {summary_data['manifest_path']}")
    print("=" * 80 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject realistic data defects into sample datasets.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"Random seed (default: {DEFAULT_SEED})")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help=f"Defect rate per category (default: {DEFAULT_RATE})")
    parser.add_argument("--raw-dir", type=Path, default=_REPO_ROOT / "data" / "raw", help="Path to raw clean CSVs")
    parser.add_argument("--output-dir", type=Path, default=_REPO_ROOT / "data" / "sample", help="Path to write sample CSVs")
    args = parser.parse_args()

    result = inject_all_defects(
        raw_dir=args.raw_dir,
        output_dir=args.output_dir,
        seed=args.seed,
        rate=args.rate,
    )
    print_summary(result)


if __name__ == "__main__":
    main()
