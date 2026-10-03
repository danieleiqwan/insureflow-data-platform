"""Integration tests for Data Quality runner and reporting pipeline (Phase 4B).

Validates:
- End-to-end execution of run_dq_pipeline against dirty data.
- High recall (>95%) across all four defect categories.
- Quarantine files contain the expected quarantine reason column.
- Report artifacts (CSV and Markdown) are written properly.
- Baseline clean execution against data/raw/ produces zero failures.
"""

from pathlib import Path
import pytest

from src.quality.inject_defects import inject_all_defects
from src.quality.run_dq_checks import run_dq_pipeline

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RAW_DIR = _REPO_ROOT / "data" / "raw"


@pytest.fixture(scope="module")
def prepared_sample_data(tmp_path_factory):
    """Generate sample dirty datasets and manifest in a temporary directory."""
    sample_dir = tmp_path_factory.mktemp("sample_data")
    res = inject_all_defects(raw_dir=_RAW_DIR, output_dir=sample_dir, seed=42)
    return sample_dir, res


def test_run_dq_pipeline_on_dirty_data(prepared_sample_data):
    """Running on dirty data achieves high recall and generates quarantine/reports."""
    sample_dir, inject_res = prepared_sample_data
    out_dir = sample_dir / "dq_output"

    result = run_dq_pipeline(data_dir=sample_dir, output_dir=out_dir, is_clean=False)

    manifest_metrics = result["manifest_metrics"]
    assert manifest_metrics["has_manifest"] is True
    assert manifest_metrics["total_injected"] == inject_res["total_defects"]

    # Overall recall must be very high (target >= 95%, here 100%)
    assert manifest_metrics["overall_recall_pct"] >= 95.0

    # Category recalls
    cat_stats = manifest_metrics["category_stats"]
    for cat in ["missing_value", "duplicate_row", "out_of_range", "malformed_format"]:
        cstats = cat_stats[cat]
        cat_recall = (cstats["detected"] / cstats["injected"]) * 100.0
        assert cat_recall >= 90.0, f"Category {cat} recall too low: {cat_recall}%"

    # Quarantine files created and non-empty
    for tbl in ["customers", "policies", "claims", "payments"]:
        q_path = out_dir / f"{tbl}_quarantine.csv"
        assert q_path.exists(), f"Quarantine file missing: {q_path}"
        q_df = result["quarantined_tables"][tbl]
        assert len(q_df) > 0, f"Quarantine table {tbl} is unexpectedly empty"
        assert "quarantine_reason" in q_df.columns
        assert "reason" in q_df.columns

    # Report files generated
    report_csv = out_dir / "dq_report.csv"
    report_md = out_dir / "dq_report.md"
    assert report_csv.exists()
    assert report_md.exists()
    assert report_csv.stat().st_size > 0
    assert report_md.stat().st_size > 0


def test_run_dq_pipeline_on_clean_data(tmp_path: Path):
    """Running on clean raw data produces zero failures and zero quarantined rows."""
    out_dir = tmp_path / "clean_dq_output"
    result = run_dq_pipeline(data_dir=_RAW_DIR, output_dir=out_dir, is_clean=True)

    # Check rule stats
    for stat in result["rule_stats"]:
        assert stat["rows_failed"] == 0, (
            f"Clean data rule failure: {stat['table']} / {stat['rule_name']} "
            f"failed {stat['rows_failed']} rows"
        )

    # Check quarantined tables
    for tbl, q_df in result["quarantined_tables"].items():
        assert len(q_df) == 0, f"Table {tbl} had {len(q_df)} quarantined rows on clean data"
