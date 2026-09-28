"""Shared utilities for InsureFlow Phase 2B synthetic data generators.

Provides: base seed constants, ID formatters, deterministic timestamp helper,
LF-only CSV writer, and facilities loader with claim-type lookup tables.
"""

from __future__ import annotations

import csv
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

# --- Seeds --------------------------------------------------------------------
BASE_SEED = 42
POLICY_SEED_OFFSET = 1000
CLAIM_SEED_OFFSET = 2000
PAYMENT_SEED_OFFSET = 3000

# --- Reference date (must match generate_customers.py) ------------------------
REFERENCE_DATE = date(2026, 1, 1)

# --- Eligible KATEGORI_FASILITI by claim_type ---------------------------------
CLAIM_TYPE_CATEGORIES: dict[str, frozenset[str]] = {
    "INPATIENT":  frozenset({"HOSPITAL"}),
    "EMERGENCY":  frozenset({"HOSPITAL"}),
    "OUTPATIENT": frozenset({"HOSPITAL", "KLINIK"}),
    "DENTAL":     frozenset({"KLINIK PERGIGIAN"}),
}

# --- Customer state -> NEGERI values in facilities_master.csv -----------------
# Handles both simple NEGERI codes and the compound WILAYAH prefix variants.
CUSTOMER_STATE_TO_NEGERI: dict[str, list[str]] = {
    "Johor":           ["JOHOR"],
    "Kedah":           ["KEDAH"],
    "Kelantan":        ["KELANTAN"],
    "Melaka":          ["MELAKA"],
    "Negeri Sembilan": ["NEGERI SEMBILAN"],
    "Pahang":          ["PAHANG"],
    "Perak":           ["PERAK"],
    "Perlis":          ["PERLIS"],
    "Pulau Pinang":    ["PULAU PINANG"],
    "Sabah":           ["SABAH"],
    "Sarawak":         ["SARAWAK"],
    "Selangor":        ["SELANGOR"],
    "Terengganu":      ["TERENGGANU"],
    "Kuala Lumpur":    [
        "WILAYAH PERSEKUTUAN KUALA LUMPUR",
        "WILAYAH PERSEKUTUAN KUALA LUMPUR DAN PUTRAJAYA",
    ],
    "Putrajaya":       [
        "WILAYAH PERSEKUTUAN PUTRAJAYA",
        "WILAYAH PERSEKUTUAN KUALA LUMPUR DAN PUTRAJAYA",
    ],
    "Labuan":          ["WILAYAH PERSEKUTUAN LABUAN"],
}


# --- Money helpers ------------------------------------------------------------

def money(amount: Any) -> Decimal:
    """Convert to Decimal and quantize to 2 decimal places (ROUND_HALF_UP)."""
    return Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def lognormal(median: float, sigma: float, lo: float, hi: float,
              rng) -> Decimal:
    """Return a lognormally-distributed Decimal amount clamped to [lo, hi].

    Uses stdlib random.gauss -- no numpy required.
    """
    raw = math.exp(rng.gauss(math.log(median), sigma))
    return money(max(lo, min(hi, raw)))


# --- ID formatters ------------------------------------------------------------

def fmt_policy_id(n: int) -> str:
    return f"P{n:07d}"


def fmt_claim_id(n: int) -> str:
    return f"CL{n:07d}"


def fmt_payment_id(n: int) -> str:
    return f"PM{n:07d}"


# --- Deterministic timestamp --------------------------------------------------

def seeded_timestamp(event_date: date, rng) -> str:
    """Derive a deterministic TIMESTAMPTZ string from an event date.

    Adds a seeded second offset (0-86399) to midnight UTC on event_date.
    Format: YYYY-MM-DD HH:MM:SS+00:00
    """
    offset_s = rng.randint(0, 86399)
    dt = datetime(event_date.year, event_date.month, event_date.day,
                  tzinfo=timezone.utc) + timedelta(seconds=offset_s)
    ts = dt.strftime("%Y-%m-%d %H:%M:%S%z")
    if ts.endswith("+0000"):
        ts = ts[:-5] + "+00:00"
    return ts


# --- CSV writer ---------------------------------------------------------------

def write_csv(rows: list[dict[str, Any]], columns: list[str],
              output_path: Path) -> None:
    """Write rows as UTF-8 CSV with LF line endings, fixed column order.

    Args:
        rows: List of dicts (extra keys are silently ignored).
        columns: Exact column order to write (must match table DDL).
        output_path: Destination path; parent directories are created if needed.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=columns, lineterminator="\n", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


# --- Facilities loader --------------------------------------------------------

def load_facilities(raw_dir: Path) -> list[dict[str, str]]:
    """Load facilities_master.csv and return as list of dicts.

    Raises FileNotFoundError if the file is absent -- callers should surface this
    with a clear message so users know to run download_sources.py first.
    """
    path = raw_dir / "facilities_master.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"facilities_master.csv not found at {path}.\n"
            "Run: python src/ingestion/download_sources.py"
        )
    rows: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({
                "facility_id":       row["KOD_FASILITI"].strip(),
                "facility_category": row["KATEGORI_FASILITI"].strip(),
                "facility_type":     row["JENIS_FASILITI"].strip(),
                "facility_name":     row["NAMA"].strip().replace("\n", " "),
                "subsektor":         row["SUBSEKTOR"].strip(),
                "negeri":            row["NEGERI"].strip(),
                "daerah":            row["DAERAH"].strip(),
                "poskod":            row["POSKOD"].strip(),
                "latitud":           row["LATITUD"].strip(),
                "longitud":          row["LONGITUD"].strip(),
            })
    return rows


def build_facility_pools(
    facilities: list[dict[str, str]]
) -> dict[str, dict[str, list[str]]]:
    """Return facility_id pools keyed by claim_type then NEGERI.

    Pools are built from sorted KOD_FASILITI so sampling is deterministic
    regardless of the order rows appear in the source CSV.

    Structure:
        pools[claim_type][negeri] = [sorted list of facility_ids]
        pools[claim_type]["_all"] = [sorted list across all states]
    """
    # Collect by (claim_type, negeri)
    staging: dict[str, dict[str, set[str]]] = {}
    for claim_type, eligible_cats in CLAIM_TYPE_CATEGORIES.items():
        staging[claim_type] = {}

    for row in facilities:
        cat = row["facility_category"]
        fid = row["facility_id"]
        negeri = row["negeri"]
        for claim_type, eligible_cats in CLAIM_TYPE_CATEGORIES.items():
            if cat in eligible_cats:
                staging[claim_type].setdefault(negeri, set()).add(fid)

    # Convert to sorted lists and add "_all" bucket
    pools: dict[str, dict[str, list[str]]] = {}
    for claim_type, by_state in staging.items():
        pools[claim_type] = {
            neg: sorted(ids) for neg, ids in by_state.items()
        }
        all_ids: set[str] = set()
        for ids in by_state.values():
            all_ids.update(ids)
        pools[claim_type]["_all"] = sorted(all_ids)

    return pools


def sample_facility(claim_type: str, customer_state: str,
                    pools: dict[str, dict[str, list[str]]],
                    rng) -> str:
    """Sample a facility_id for the given claim_type and customer home state.

    80% of the time draws from facilities in the customer's home state;
    20% draws from any state. Falls back to the global pool if the home
    state has no eligible facilities.
    """
    state_pool = pools[claim_type]
    home_negeris = CUSTOMER_STATE_TO_NEGERI.get(customer_state, [])

    # Collect all facility IDs matching the home state
    home_ids: list[str] = []
    seen: set[str] = set()
    for neg in home_negeris:
        for fid in state_pool.get(neg, []):
            if fid not in seen:
                home_ids.append(fid)
                seen.add(fid)
    home_ids.sort()

    use_home = bool(home_ids) and rng.random() < 0.80
    pool = home_ids if use_home else state_pool["_all"]

    if not pool:
        pool = state_pool["_all"]

    return rng.choice(pool)
