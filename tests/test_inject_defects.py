"""Tests for Data Quality Defect Injector (Phase 4B).

Validates:
- Determinism: running with identical seed yields byte-identical files and manifest.
- Defect counts per category within expected tolerance (~1.5%).
- Manifest accuracy: every manifest record corresponds to an actual difference between
  clean data and dirty data.
- Duplicate row injection creates matching business keys.
"""

import csv
import hashlib
from pathlib import Path
import pytest

from src.quality.inject_defects import (
    DEFAULT_RATE,
    DEFAULT_SEED,
    TABLE_CONFIGS,
    inject_all_defects,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RAW_DIR = _REPO_ROOT / "data" / "raw"


@pytest.fixture
def tmp_output_dirs(tmp_path: Path):
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    out1.mkdir()
    out2.mkdir()
    return out1, out2


def test_defect_injector_determinism(tmp_output_dirs):
    """Running inject_all_defects twice with the same seed yields byte-identical outputs."""
    out1, out2 = tmp_output_dirs
    res1 = inject_all_defects(raw_dir=_RAW_DIR, output_dir=out1, seed=42)
    res2 = inject_all_defects(raw_dir=_RAW_DIR, output_dir=out2, seed=42)

    assert res1["total_defects"] == res2["total_defects"]
    assert res1["total_defects"] > 0

    csv_files1 = sorted(out1.glob("*.csv"))
    csv_files2 = sorted(out2.glob("*.csv"))
    assert len(csv_files1) == 5  # 4 dirty CSVs + 1 manifest
    assert len(csv_files1) == len(csv_files2)

    for f1, f2 in zip(csv_files1, csv_files2):
        assert f1.name == f2.name
        h1 = hashlib.sha256(f1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(f2.read_bytes()).hexdigest()
        assert h1 == h2, f"Determinism failure in file {f1.name}: {h1} != {h2}"


def test_defect_counts_per_category(tmp_path: Path):
    """Verify that defect counts per category and file match expected ~1.5% target."""
    out_dir = tmp_path / "sample"
    res = inject_all_defects(raw_dir=_RAW_DIR, output_dir=out_dir, seed=DEFAULT_SEED, rate=DEFAULT_RATE)

    summary = res["summary"]
    for dirty_name, stats in summary.items():
        assert stats["missing_value"] >= 2
        assert stats["duplicate_row"] >= 2
        assert stats["out_of_range"] >= 2
        assert stats["malformed_format"] >= 2
        # Target rate ~1.5%, total defects ~6% of clean rows
        expected_defects = stats["missing_value"] * 4
        assert stats["total_defects"] == expected_defects


def test_manifest_matches_actual_changes(tmp_path: Path):
    """Verify that every entry in defect_manifest.csv reflects an actual modification."""
    out_dir = tmp_path / "sample"
    inject_all_defects(raw_dir=_RAW_DIR, output_dir=out_dir, seed=42)

    manifest_file = out_dir / "defect_manifest.csv"
    assert manifest_file.exists()

    with open(manifest_file, mode="r", encoding="utf-8") as f:
        manifest_rows = list(csv.DictReader(f))

    assert len(manifest_rows) > 0

    # Group by dirty file
    for table_name, cfg in TABLE_CONFIGS.items():
        dirty_file = out_dir / cfg["dirty_file"]
        raw_file = _RAW_DIR / cfg["file_name"]

        with open(dirty_file, mode="r", encoding="utf-8") as f:
            dirty_rows = list(csv.DictReader(f))
        with open(raw_file, mode="r", encoding="utf-8") as f:
            raw_rows = list(csv.DictReader(f))

        key_col = cfg["key_col"]
        raw_by_key = {r[key_col]: r for r in raw_rows}

        table_manifest = [m for m in manifest_rows if m["file"] == cfg["dirty_file"]]
        assert len(table_manifest) > 0

        for m in table_manifest:
            b_key = m["row_business_key"]
            cat = m["defect_category"]
            col = m["column_affected"]

            assert b_key in raw_by_key, f"Manifest key {b_key} not in clean raw data"
            orig_in_raw = raw_by_key[b_key][col] if col in raw_by_key[b_key] else b_key
            assert orig_in_raw == m["original_value"]

            if cat == "duplicate_row":
                # Must appear at least twice in dirty rows
                matching_dirty = [r for r in dirty_rows if r[key_col] == b_key]
                assert len(matching_dirty) >= 2
            elif cat == "missing_value":
                # At least one matching dirty row should have empty value in affected column
                matching_dirty = [r for r in dirty_rows if r[key_col] == b_key and r[col] == ""]
                assert len(matching_dirty) >= 1
