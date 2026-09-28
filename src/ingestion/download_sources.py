"""InsureFlow Data Platform — Source Dataset Ingestion (Phase 2A).

Downloads official Malaysian public healthcare datasets into data/raw/
using only the Python standard library (urllib.request, hashlib, pathlib).
Idempotent, reproducible, and verifiable via SHA256 checksums.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import TypedDict
import urllib.request


class DatasetManifest(TypedDict):
    name: str
    filename: str
    url: str
    publisher: str
    file_size_bytes: int
    sha256: str
    row_count: int


# Verified open data source URLs
SOURCES: list[dict[str, str]] = [
    {
        "name": "MOH Facilities Master Registry",
        "filename": "facilities_master.csv",
        "url": "https://raw.githubusercontent.com/MoH-Malaysia/data-resources-public/main/facilities_master.csv",
        "publisher": "Ministry of Health Malaysia (KKM)",
    },
    {
        "name": "MOH Hospital Bed Utilisation (KKMNOW)",
        "filename": "bedutil_facility.csv",
        "url": "https://raw.githubusercontent.com/MoH-Malaysia/data-resources-public/main/bedutil_facility.csv",
        "publisher": "Ministry of Health Malaysia (KKM / KKMNOW)",
    },
    {
        "name": "data.gov.my Hospital Beds by State & District",
        "filename": "hospital_beds.csv",
        "url": "https://storage.data.gov.my/healthcare/hospital_beds.csv",
        "publisher": "Health Informatics Centre, MOH via data.gov.my",
    },
]


def count_csv_rows(file_path: Path) -> int:
    """Count data rows in a CSV file (excluding header)."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # skip header
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def download_dataset(
    url: str,
    dest_path: Path,
    user_agent: str = "InsureFlow-Data-Platform/1.0",
) -> tuple[int, str, int]:
    """Download a file via HTTP/HTTPS, compute SHA256, and return metadata.

    Args:
        url: Direct HTTP/HTTPS download link.
        dest_path: Path to target file on local disk.
        user_agent: Custom User-Agent header string.

    Returns:
        tuple of (file_size_bytes, sha256_checksum, data_row_count).
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    hasher = hashlib.sha256()
    total_bytes = 0

    # Write to a temporary file first for atomic download
    temp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    try:
        with urllib.request.urlopen(req) as resp, open(temp_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)
                total_bytes += len(chunk)

        # Atomically replace destination file
        temp_path.replace(dest_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    sha256 = hasher.hexdigest()
    row_count = count_csv_rows(dest_path)

    return total_bytes, sha256, row_count


def download_all_sources(data_dir: Path | None = None) -> list[DatasetManifest]:
    """Download all configured source datasets into data_dir.

    Args:
        data_dir: Target directory for raw files (defaults to data/raw/).

    Returns:
        List of DatasetManifest dictionaries.
    """
    if data_dir is None:
        data_dir = Path(__file__).resolve().parents[2] / "data" / "raw"

    manifests: list[DatasetManifest] = []

    for source in SOURCES:
        dest = data_dir / source["filename"]
        size_bytes, sha256, row_count = download_dataset(
            url=source["url"],
            dest_path=dest,
        )
        manifests.append(
            {
                "name": source["name"],
                "filename": source["filename"],
                "url": source["url"],
                "publisher": source["publisher"],
                "file_size_bytes": size_bytes,
                "sha256": sha256,
                "row_count": row_count,
            }
        )

    return manifests


def main() -> None:
    """CLI entry point: downloads datasets and displays execution summary."""
    raw_dir = Path(__file__).resolve().parents[2] / "data" / "raw"
    print("=" * 80)
    print(f"InsureFlow Phase 2A — Downloading Raw Sources to: {raw_dir}")
    print("=" * 80)

    manifests = download_all_sources(raw_dir)

    print(f"\nSuccessfully downloaded {len(manifests)} datasets:\n")
    print(
        f"{'Filename':<24} {'Size (KB)':<12} {'Rows':<8} {'SHA256 (first 16)':<20} {'Source'}"
    )
    print("-" * 80)
    for m in manifests:
        size_kb = m["file_size_bytes"] / 1024
        print(
            f"{m['filename']:<24} {size_kb:>9.2f} KB  {m['row_count']:<8} {m['sha256'][:16]:<20} {m['name']}"
        )
        print(f"  URL: {m['url']}")
        print(f"  Full SHA256: {m['sha256']}")
        print()

    print("=" * 80)
    print("Download complete. All files saved to data/raw/.")
    print("=" * 80)


if __name__ == "__main__":
    main()
