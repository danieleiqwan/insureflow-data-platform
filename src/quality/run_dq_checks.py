"""Data Quality Runner & Reporting Engine for InsureFlow (Phase 4B).

Executes the DQ rule suite against dirty sample CSVs (and clean baseline CSVs),
quarantines failing rows into data/sample/<table>_quarantine.csv, produces
aggregate rule reports (CSV and Markdown), and compares detections against
data/sample/defect_manifest.csv to compute detection recall and false positives.

Headline Metrics:
- Recall: Percentage of injected defects successfully detected and quarantined.
- False Positives: Quarantined rows that were not injected with defects.

Usage:
    python src/quality/run_dq_checks.py
    python -m src.quality.run_dq_checks --data-dir data/sample
    python -m src.quality.run_dq_checks --clean
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.quality.dq_rules import (
    ALLOWED_CLAIM_STATUSES,
    ALLOWED_CLAIM_TYPES,
    ALLOWED_GENDERS,
    ALLOWED_PAYMENT_METHODS,
    ALLOWED_PAYMENT_STATUSES,
    ALLOWED_POLICY_STATUSES,
    ALLOWED_POLICY_TYPES,
    ALLOWED_STATES,
    REFERENCE_DATE,
    RuleFailure,
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

_REPO_ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def read_df(file_path: Path) -> pd.DataFrame:
    """Read CSV into pandas DataFrame with all columns as strings (preserving raw format)."""
    return pd.read_csv(file_path, dtype=str, keep_default_na=False)


def run_table_rules(
    table_name: str,
    df: pd.DataFrame,
    context_dfs: Optional[Dict[str, pd.DataFrame]] = None,
) -> Tuple[List[RuleFailure], List[Dict[str, Any]]]:
    """Execute DQ rules for a given table and return (all_failures, rule_stats)."""
    context_dfs = context_dfs or {}
    failures: List[RuleFailure] = []
    rule_stats: List[Dict[str, Any]] = []

    def record_rule(
        rule_name: str,
        category: str,
        rule_failures: List[RuleFailure],
        rows_checked: int = len(df),
    ) -> None:
        failed_row_indices = {f.row_index for f in rule_failures}
        n_failed = len(failed_row_indices)
        rate = (n_failed / rows_checked * 100.0) if rows_checked > 0 else 0.0
        rule_stats.append({
            "table": table_name,
            "rule_name": rule_name,
            "category": category,
            "rows_checked": rows_checked,
            "rows_failed": n_failed,
            "fail_rate_pct": round(rate, 2),
        })
        failures.extend(rule_failures)

    # ─── Customers Rules ──────────────────────────────────────────────────────
    if table_name == "customers":
        key_col = "customer_id"
        # Completeness
        req_cols = ["customer_id", "first_name", "last_name", "gender", "date_of_birth", "state", "occupation"]
        record_rule("completeness_required_fields", "completeness", check_completeness(df, req_cols, key_col))

        # Uniqueness
        record_rule("uniqueness_customer_id", "uniqueness", check_uniqueness(df, key_col))

        # Validity
        record_rule("validity_gender_enum", "validity", check_enum_validity(df, "gender", ALLOWED_GENDERS, key_col))
        record_rule("validity_state_enum", "validity", check_enum_validity(df, "state", ALLOWED_STATES, key_col))
        record_rule("validity_date_of_birth_format", "validity", check_date_format(df, "date_of_birth", key_col))

        # Consistency
        record_rule("consistency_dates_not_in_future", "consistency", check_dates_not_in_future(df, ["date_of_birth", "created_at"], key_col))
        record_rule("consistency_customer_age_18_120", "consistency", check_customer_age(df, "date_of_birth", key_col, 18, 120))

    # ─── Policies Rules ───────────────────────────────────────────────────────
    elif table_name == "policies":
        key_col = "policy_id"
        # Completeness
        req_cols = ["policy_id", "customer_id", "policy_type", "start_date", "end_date", "premium", "status"]
        record_rule("completeness_required_fields", "completeness", check_completeness(df, req_cols, key_col))

        # Uniqueness
        record_rule("uniqueness_policy_id", "uniqueness", check_uniqueness(df, key_col))

        # Validity
        record_rule("validity_policy_type_enum", "validity", check_enum_validity(df, "policy_type", ALLOWED_POLICY_TYPES, key_col))
        record_rule("validity_status_enum", "validity", check_enum_validity(df, "status", ALLOWED_POLICY_STATUSES, key_col))
        record_rule("validity_start_date_format", "validity", check_date_format(df, "start_date", key_col))
        record_rule("validity_end_date_format", "validity", check_date_format(df, "end_date", key_col))
        record_rule("validity_premium_positive", "validity", check_numeric_range(df, "premium", key_col, min_val=Decimal("0.01")))

        # Consistency
        record_rule("consistency_dates_not_in_future", "consistency", check_dates_not_in_future(df, ["start_date", "created_at"], key_col))
        record_rule("consistency_end_date_ge_start_date", "consistency", check_policy_dates(df, "start_date", "end_date", key_col))

    # ─── Claims Rules ─────────────────────────────────────────────────────────
    elif table_name == "claims":
        key_col = "claim_id"
        # Completeness (approved_amount checked conditionally)
        req_cols = ["claim_id", "policy_id", "facility_id", "claim_date", "claim_type", "claim_amount", "status"]
        record_rule("completeness_required_fields", "completeness", check_completeness(df, req_cols, key_col))
        record_rule("completeness_approved_amount_if_decided", "completeness", check_claim_approved_amount_completeness(df, "status", "approved_amount", key_col))

        # Uniqueness
        record_rule("uniqueness_claim_id", "uniqueness", check_uniqueness(df, key_col))

        # Validity
        record_rule("validity_claim_type_enum", "validity", check_enum_validity(df, "claim_type", ALLOWED_CLAIM_TYPES, key_col))
        record_rule("validity_status_enum", "validity", check_enum_validity(df, "status", ALLOWED_CLAIM_STATUSES, key_col))
        record_rule("validity_claim_date_format", "validity", check_date_format(df, "claim_date", key_col))
        record_rule("validity_claim_amount_non_negative", "validity", check_numeric_range(df, "claim_amount", key_col, min_val=Decimal("0.00")))
        record_rule("validity_approved_amount_non_negative", "validity", check_numeric_range(df, "approved_amount", key_col, min_val=Decimal("0.00")))

        # Consistency
        record_rule("consistency_dates_not_in_future", "consistency", check_dates_not_in_future(df, ["claim_date", "created_at"], key_col))
        record_rule("consistency_approved_le_claim_amount", "consistency", check_claim_approved_amount(df, "claim_amount", "approved_amount", key_col))

        if "policies" in context_dfs:
            record_rule(
                "consistency_claim_within_policy_period",
                "consistency",
                check_claim_within_policy_period(df, context_dfs["policies"], "claim_date", "policy_id", key_col),
            )

    # ─── Payments Rules ───────────────────────────────────────────────────────
    elif table_name == "payments":
        key_col = "payment_id"
        # Completeness
        req_cols = ["payment_id", "claim_id", "payment_date", "amount", "payment_method", "status"]
        record_rule("completeness_required_fields", "completeness", check_completeness(df, req_cols, key_col))

        # Uniqueness
        record_rule("uniqueness_payment_id", "uniqueness", check_uniqueness(df, key_col))

        # Validity
        record_rule("validity_payment_method_enum", "validity", check_enum_validity(df, "payment_method", ALLOWED_PAYMENT_METHODS, key_col))
        record_rule("validity_status_enum", "validity", check_enum_validity(df, "status", ALLOWED_PAYMENT_STATUSES, key_col))
        record_rule("validity_payment_date_format", "validity", check_date_format(df, "payment_date", key_col))
        record_rule("validity_amount_positive", "validity", check_numeric_range(df, "amount", key_col, min_val=Decimal("0.01")))

        # Consistency
        record_rule("consistency_payment_dates_validity", "consistency", check_payment_dates_validity(df, "payment_date", "status", key_col))

        if "claims" in context_dfs:
            record_rule(
                "consistency_payment_after_claim",
                "consistency",
                check_payment_after_claim(df, context_dfs["claims"], "payment_date", "claim_id", key_col),
            )

    return failures, rule_stats


def quarantine_table(
    df: pd.DataFrame,
    failures: List[RuleFailure],
    key_col: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split DataFrame into quarantined rows and passed rows."""
    failures_by_idx: Dict[int, List[str]] = {}
    for f in failures:
        failures_by_idx.setdefault(f.row_index, []).append(f.reason)

    quarantined_indices = sorted(failures_by_idx.keys())
    passed_indices = [idx for idx in df.index if idx not in failures_by_idx]

    if quarantined_indices:
        q_df = df.loc[quarantined_indices].copy()
        # Add combined quarantine reason
        reasons_list = [" | ".join(failures_by_idx[idx]) for idx in quarantined_indices]
        q_df["quarantine_reason"] = reasons_list
        q_df["reason"] = reasons_list
    else:
        q_df = df.iloc[0:0].copy()
        q_df["quarantine_reason"] = []
        q_df["reason"] = []

    passed_df = df.loc[passed_indices].copy()
    return q_df, passed_df


def compare_with_manifest(
    manifest_path: Path,
    quarantined_tables: Dict[str, pd.DataFrame],
    key_cols: Dict[str, str],
) -> Dict[str, Any]:
    """Compare quarantined rows against defect manifest to calculate recall and false positives."""
    if not manifest_path.exists():
        return {"has_manifest": False}

    with open(manifest_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        manifest_rows = list(reader)

    # Build quarantined keys per file and table
    table_to_file = {
        "customers": "customers_dirty.csv",
        "policies": "policies_dirty.csv",
        "claims": "claims_dirty.csv",
        "payments": "payments_dirty.csv",
    }
    file_to_table = {v: k for k, v in table_to_file.items()}

    quarantined_keys_by_file: Dict[str, Set[str]] = {}
    for tbl, q_df in quarantined_tables.items():
        k_col = key_cols[tbl]
        f_name = table_to_file.get(tbl, f"{tbl}.csv")
        if not q_df.empty and k_col in q_df.columns:
            quarantined_keys_by_file[f_name] = set(q_df[k_col].astype(str))
        else:
            quarantined_keys_by_file[f_name] = set()

    # Track detections per category and file
    cat_stats: Dict[str, Dict[str, int]] = {
        "missing_value": {"injected": 0, "detected": 0},
        "duplicate_row": {"injected": 0, "detected": 0},
        "out_of_range": {"injected": 0, "detected": 0},
        "malformed_format": {"injected": 0, "detected": 0},
    }
    file_stats: Dict[str, Dict[str, int]] = {}

    manifest_keys_by_file: Dict[str, Set[str]] = {}
    for entry in manifest_rows:
        fn = entry["file"]
        b_key = entry["row_business_key"]
        cat = entry["defect_category"]

        manifest_keys_by_file.setdefault(fn, set()).add(b_key)

        if fn not in file_stats:
            file_stats[fn] = {"injected": 0, "detected": 0}

        file_stats[fn]["injected"] += 1
        if cat in cat_stats:
            cat_stats[cat]["injected"] += 1

        is_caught = b_key in quarantined_keys_by_file.get(fn, set())
        if is_caught:
            file_stats[fn]["detected"] += 1
            if cat in cat_stats:
                cat_stats[cat]["detected"] += 1

    # False positives: Quarantined keys NOT in manifest for that file
    false_positives_by_file: Dict[str, List[str]] = {}
    total_false_positives = 0
    for fn, q_keys in quarantined_keys_by_file.items():
        m_keys = manifest_keys_by_file.get(fn, set())
        fps = sorted(list(q_keys - m_keys))
        false_positives_by_file[fn] = fps
        total_false_positives += len(fps)

    total_injected = len(manifest_rows)
    total_detected = sum(f["detected"] for f in file_stats.values())
    overall_recall = (total_detected / total_injected * 100.0) if total_injected > 0 else 0.0

    return {
        "has_manifest": True,
        "total_injected": total_injected,
        "total_detected": total_detected,
        "overall_recall_pct": round(overall_recall, 2),
        "total_false_positives": total_false_positives,
        "category_stats": cat_stats,
        "file_stats": file_stats,
        "false_positives_by_file": false_positives_by_file,
    }


def write_dq_report(
    report_csv_path: Path,
    report_md_path: Path,
    rule_stats: List[Dict[str, Any]],
    manifest_metrics: Dict[str, Any],
) -> None:
    """Write DQ check report in both CSV and Markdown formats."""
    report_csv_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Write CSV report
    csv_headers = ["table", "rule_name", "category", "rows_checked", "rows_failed", "fail_rate_pct"]
    with open(report_csv_path, mode="w", encoding="utf-8", newline="\n") as f:
        writer = csv.DictWriter(f, fieldnames=csv_headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rule_stats)

    # 2. Write Markdown report
    with open(report_md_path, mode="w", encoding="utf-8") as f:
        f.write("# InsureFlow Data Quality Execution Report (Phase 4B)\n\n")
        f.write(f"- **Execution Date:** {date.today().isoformat()}\n")
        f.write(f"- **Reference Date:** {REFERENCE_DATE.isoformat()}\n\n")

        if manifest_metrics.get("has_manifest"):
            f.write("## 1. Headline Metrics (Ground Truth Comparison)\n\n")
            f.write(f"- **Total Injected Defects:** {manifest_metrics['total_injected']}\n")
            f.write(f"- **Defects Detected & Quarantined:** {manifest_metrics['total_detected']}\n")
            f.write(f"- **Overall Recall:** {manifest_metrics['overall_recall_pct']}%\n")
            f.write(f"- **False Positives:** {manifest_metrics['total_false_positives']}\n\n")

            f.write("### Detection Recall by Defect Category\n\n")
            f.write("| Defect Category | Injected | Detected | Recall Rate |\n")
            f.write("|---|---|---|---|\n")
            for cat, cstats in manifest_metrics["category_stats"].items():
                inj = cstats["injected"]
                det = cstats["detected"]
                rate = (det / inj * 100.0) if inj > 0 else 0.0
                f.write(f"| `{cat}` | {inj} | {det} | {rate:.1f}% |\n")
            f.write("\n")

        f.write("## 2. Rule Execution Summary\n\n")
        f.write("| Table | Rule Name | Category | Rows Checked | Rows Failed | Fail Rate |\n")
        f.write("|---|---|---|---|---|---|\n")
        for stat in rule_stats:
            f.write(
                f"| `{stat['table']}` | `{stat['rule_name']}` | `{stat['category']}` | "
                f"{stat['rows_checked']} | {stat['rows_failed']} | {stat['fail_rate_pct']:.2f}% |\n"
            )
        f.write("\n")


def print_dq_summary(
    rule_stats: List[Dict[str, Any]],
    quarantined_tables: Dict[str, pd.DataFrame],
    manifest_metrics: Dict[str, Any],
) -> None:
    """Print readable terminal output for data quality execution."""
    print("\n" + "=" * 90)
    print("INSUREFLOW DATA QUALITY — RULE EXECUTION SUMMARY (Phase 4B)")
    print("=" * 90)
    print(f"{'Table':<12} | {'Rule Name':<38} | {'Category':<12} | {'Checked':<8} | {'Failed':<6} | {'Fail Rate'}")
    print("-" * 90)
    for stat in rule_stats:
        print(
            f"{stat['table']:<12} | "
            f"{stat['rule_name']:<38} | "
            f"{stat['category']:<12} | "
            f"{stat['rows_checked']:<8} | "
            f"{stat['rows_failed']:<6} | "
            f"{stat['fail_rate_pct']:.2f}%"
        )
    print("-" * 90)

    print("\n" + "=" * 90)
    print("QUARANTINE SUMMARY")
    print("=" * 90)
    print(f"{'Table':<16} | {'Quarantined Rows':<18} | {'Quarantine CSV File'}")
    print("-" * 90)
    for tbl, q_df in quarantined_tables.items():
        print(f"{tbl:<16} | {len(q_df):<18} | data/sample/{tbl}_quarantine.csv")
    print("-" * 90)

    if manifest_metrics.get("has_manifest"):
        print("\n" + "=" * 90)
        print("HEADLINE METRICS — DEFECT DETECTION (MANIFEST COMPARISON)")
        print("=" * 90)
        print(f"Overall Injected Defects: {manifest_metrics['total_injected']}")
        print(f"Defects Caught & Quarantined: {manifest_metrics['total_detected']}")
        print(f"Overall Recall Rate: {manifest_metrics['overall_recall_pct']}%")
        print(f"False Positives: {manifest_metrics['total_false_positives']}")
        print("-" * 90)
        print(f"{'Defect Category':<22} | {'Injected':<10} | {'Detected':<10} | {'Recall Rate'}")
        print("-" * 90)
        for cat, cstats in manifest_metrics["category_stats"].items():
            inj = cstats["injected"]
            det = cstats["detected"]
            rate = (det / inj * 100.0) if inj > 0 else 0.0
            print(f"{cat:<22} | {inj:<10} | {det:<10} | {rate:.1f}%")
        print("=" * 90 + "\n")


def run_dq_pipeline(
    data_dir: Path,
    output_dir: Path,
    is_clean: bool = False,
) -> Dict[str, Any]:
    """Execute complete DQ check and quarantine pipeline."""
    output_dir.mkdir(parents=True, exist_ok=True)

    table_filenames = {
        "customers": "customers.csv" if is_clean else "customers_dirty.csv",
        "policies": "policies.csv" if is_clean else "policies_dirty.csv",
        "claims": "claims.csv" if is_clean else "claims_dirty.csv",
        "payments": "payments.csv" if is_clean else "payments_dirty.csv",
    }
    key_cols = {
        "customers": "customer_id",
        "policies": "policy_id",
        "claims": "claim_id",
        "payments": "payment_id",
    }

    # Load DataFrames
    dfs: Dict[str, pd.DataFrame] = {}
    for tbl, fn in table_filenames.items():
        csv_file = data_dir / fn
        if not csv_file.exists():
            raise FileNotFoundError(f"Required input CSV not found: {csv_file}")
        dfs[tbl] = read_df(csv_file)

    all_rule_stats: List[Dict[str, Any]] = []
    quarantined_tables: Dict[str, pd.DataFrame] = {}
    passed_tables: Dict[str, pd.DataFrame] = {}

    # Run checks table-by-table
    for tbl in ["customers", "policies", "claims", "payments"]:
        df = dfs[tbl]
        failures, stats = run_table_rules(tbl, df, context_dfs=dfs)
        all_rule_stats.extend(stats)

        q_df, p_df = quarantine_table(df, failures, key_cols[tbl])
        quarantined_tables[tbl] = q_df
        passed_tables[tbl] = p_df

        # Write quarantine file
        q_path = output_dir / f"{tbl}_quarantine.csv"
        q_df.to_csv(q_path, index=False, lineterminator="\n")

        # Write clean passing rows
        p_path = output_dir / f"{tbl}_passed.csv"
        p_df.to_csv(p_path, index=False, lineterminator="\n")

    # Ground truth comparison with manifest (if evaluating sample dirty data)
    manifest_path = data_dir / "defect_manifest.csv"
    manifest_metrics = compare_with_manifest(manifest_path, quarantined_tables, key_cols)

    # Write report files
    report_csv = output_dir / "dq_report.csv"
    report_md = output_dir / "dq_report.md"
    write_dq_report(report_csv, report_md, all_rule_stats, manifest_metrics)

    return {
        "rule_stats": all_rule_stats,
        "quarantined_tables": quarantined_tables,
        "manifest_metrics": manifest_metrics,
        "report_csv": report_csv,
        "report_md": report_md,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run InsureFlow Data Quality checks and quarantine.")
    parser.add_argument("--data-dir", type=Path, default=None, help="Directory containing CSVs to inspect.")
    parser.add_argument("--output-dir", type=Path, default=_REPO_ROOT / "data" / "sample", help="Directory for quarantine and reports.")
    parser.add_argument("--clean", action="store_true", help="Run against data/raw/ clean datasets as baseline.")
    args = parser.parse_args()

    if args.clean:
        data_dir = _REPO_ROOT / "data" / "raw"
        is_clean = True
    elif args.data_dir is not None:
        data_dir = args.data_dir
        is_clean = False
    else:
        data_dir = _REPO_ROOT / "data" / "sample"
        is_clean = False

    result = run_dq_pipeline(
        data_dir=data_dir,
        output_dir=args.output_dir,
        is_clean=is_clean,
    )
    print_dq_summary(result["rule_stats"], result["quarantined_tables"], result["manifest_metrics"])


if __name__ == "__main__":
    main()
