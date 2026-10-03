"""Data Quality Rule Framework for InsureFlow (Phase 4B).

Provides small, composable, and testable rule functions covering the four
core data quality dimensions:
1. Completeness: Required fields must be non-null and non-empty.
2. Uniqueness: Business keys must be unique.
3. Validity: Domain enums, ISO date formats, and numeric bounds.
4. Consistency: Cross-field constraints (e.g. approved_amount <= claim_amount,
   end_date >= start_date, claim_date within policy period, dates not in future).

Each rule returns a list of RuleFailure objects containing the row index,
business key, rule name, category, column affected, and human-readable reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional, Set
import pandas as pd

REFERENCE_DATE = date(2026, 1, 1)

# Domain enumerations (matching PostgreSQL CHECK constraints and Silver schema)
ALLOWED_GENDERS: Set[str] = {"Male", "Female"}
ALLOWED_STATES: Set[str] = {
    "Johor", "Kedah", "Kelantan", "Melaka", "Negeri Sembilan", "Pahang",
    "Perak", "Perlis", "Pulau Pinang", "Sabah", "Sarawak", "Selangor",
    "Terengganu", "Kuala Lumpur", "Putrajaya", "Labuan",
    "W.P. Kuala Lumpur", "W.P. Labuan", "W.P. Putrajaya",
}
ALLOWED_POLICY_TYPES: Set[str] = {"MEDICAL", "HOSPITALIZATION", "CRITICAL_ILLNESS", "PERSONAL_ACCIDENT"}
ALLOWED_POLICY_STATUSES: Set[str] = {"ACTIVE", "EXPIRED", "CANCELLED", "LAPSED"}
ALLOWED_CLAIM_TYPES: Set[str] = {"OUTPATIENT", "INPATIENT", "EMERGENCY", "DENTAL"}
ALLOWED_CLAIM_STATUSES: Set[str] = {"SUBMITTED", "APPROVED", "PARTIALLY_APPROVED", "REJECTED"}
ALLOWED_PAYMENT_METHODS: Set[str] = {"BANK_TRANSFER", "CHEQUE", "CARD", "E_WALLET"}
ALLOWED_PAYMENT_STATUSES: Set[str] = {"PENDING", "COMPLETED", "FAILED"}


@dataclass(frozen=True)
class RuleFailure:
    """Represents a single data quality failure on a specific row."""
    row_index: int
    business_key: str
    rule_name: str
    category: str
    column_name: str
    reason: str


# ─── 1. COMPLETENESS RULES ────────────────────────────────────────────────────

def check_completeness(
    df: pd.DataFrame,
    required_columns: List[str],
    business_key_col: str,
) -> List[RuleFailure]:
    """Verify that required columns are not null, empty, or whitespace-only."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        b_key = str(row.get(business_key_col, f"row_{idx}"))
        for col in required_columns:
            val = row.get(col)
            is_empty = False
            if pd.isna(val) or val is None:
                is_empty = True
            elif isinstance(val, str) and (val.strip() == "" or val.strip().lower() in ("nan", "null", "none")):
                is_empty = True

            if is_empty:
                failures.append(
                    RuleFailure(
                        row_index=int(idx),
                        business_key=b_key,
                        rule_name="check_completeness",
                        category="completeness",
                        column_name=col,
                        reason=f"Missing required value in column '{col}'",
                    )
                )
    return failures


# ─── 2. UNIQUENESS RULES ──────────────────────────────────────────────────────

def check_uniqueness(
    df: pd.DataFrame,
    business_key_col: str,
) -> List[RuleFailure]:
    """Verify that business keys are unique across the dataset."""
    failures: List[RuleFailure] = []
    # Identify duplicated rows (keep=False marks all duplicates)
    dup_mask = df.duplicated(subset=[business_key_col], keep=False)
    dup_indices = df[dup_mask].index

    for idx in dup_indices:
        b_key = str(df.at[idx, business_key_col])
        failures.append(
            RuleFailure(
                row_index=int(idx),
                business_key=b_key,
                rule_name="check_uniqueness",
                category="uniqueness",
                column_name=business_key_col,
                reason=f"Duplicate business key '{b_key}' found in column '{business_key_col}'",
            )
        )
    return failures


# ─── 3. VALIDITY RULES ────────────────────────────────────────────────────────

def check_enum_validity(
    df: pd.DataFrame,
    column: str,
    allowed_values: Set[str],
    business_key_col: str,
) -> List[RuleFailure]:
    """Verify that values in a column belong to an allowed set."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        val = row.get(column)
        if pd.isna(val) or val is None or str(val).strip() == "":
            continue  # Completeness check handles nulls
        s = str(val).strip()
        if s not in allowed_values:
            b_key = str(row.get(business_key_col, f"row_{idx}"))
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_enum_validity",
                    category="validity",
                    column_name=column,
                    reason=f"Invalid enum value '{s}' in column '{column}' (expected one of {sorted(allowed_values)})",
                )
            )
    return failures


def parse_iso_date(val: Any) -> Optional[date]:
    """Strictly parse ISO YYYY-MM-DD date."""
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_timestamp_date(val: Any) -> Optional[date]:
    """Parse date from timestamp string or ISO date string."""
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    # Try YYYY-MM-DD prefix if timestamp
    if len(s) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def check_date_format(
    df: pd.DataFrame,
    column: str,
    business_key_col: str,
) -> List[RuleFailure]:
    """Verify that date values adhere strictly to ISO YYYY-MM-DD."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        val = row.get(column)
        if pd.isna(val) or val is None or str(val).strip() == "":
            continue
        d = parse_iso_date(val)
        if d is None:
            b_key = str(row.get(business_key_col, f"row_{idx}"))
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_date_format",
                    category="validity",
                    column_name=column,
                    reason=f"Invalid date format '{val}' in column '{column}' (expected ISO YYYY-MM-DD)",
                )
            )
    return failures


def check_numeric_range(
    df: pd.DataFrame,
    column: str,
    business_key_col: str,
    min_val: Optional[Decimal] = None,
    max_val: Optional[Decimal] = None,
) -> List[RuleFailure]:
    """Verify numeric strings can be parsed to Decimal and satisfy bounds."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        val = row.get(column)
        if pd.isna(val) or val is None or str(val).strip() == "":
            continue
        s = str(val).strip()
        b_key = str(row.get(business_key_col, f"row_{idx}"))
        try:
            d = Decimal(s)
        except InvalidOperation:
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_numeric_range",
                    category="validity",
                    column_name=column,
                    reason=f"Malformed numeric string '{s}' in column '{column}'",
                )
            )
            continue

        if min_val is not None and d < min_val:
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_numeric_range",
                    category="validity",
                    column_name=column,
                    reason=f"Value {d} in column '{column}' is less than minimum {min_val}",
                )
            )
        elif max_val is not None and d > max_val:
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_numeric_range",
                    category="validity",
                    column_name=column,
                    reason=f"Value {d} in column '{column}' exceeds maximum {max_val}",
                )
            )
    return failures


# ─── 4. CONSISTENCY RULES ─────────────────────────────────────────────────────

def check_dates_not_in_future(
    df: pd.DataFrame,
    date_columns: List[str],
    business_key_col: str,
    reference_date: date = REFERENCE_DATE,
) -> List[RuleFailure]:
    """Verify dates and timestamps are not in the future relative to reference date."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        b_key = str(row.get(business_key_col, f"row_{idx}"))
        for col in date_columns:
            val = row.get(col)
            if pd.isna(val) or val is None or str(val).strip() == "":
                continue
            # Try ISO date first, then timestamp
            d = parse_iso_date(val) or parse_timestamp_date(val)
            if d is not None and d > reference_date:
                failures.append(
                    RuleFailure(
                        row_index=int(idx),
                        business_key=b_key,
                        rule_name="check_dates_not_in_future",
                        category="consistency",
                        column_name=col,
                        reason=f"Date '{val}' in column '{col}' is in the future relative to reference date {reference_date}",
                    )
                )
    return failures


def check_customer_age(
    df: pd.DataFrame,
    birth_date_col: str = "date_of_birth",
    business_key_col: str = "customer_id",
    min_age: int = 18,
    max_age: int = 120,
    reference_date: date = REFERENCE_DATE,
) -> List[RuleFailure]:
    """Verify customer age is between min_age and max_age as of reference date."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        val = row.get(birth_date_col)
        d = parse_iso_date(val)
        if d is not None:
            # Calculate age at reference date
            age = reference_date.year - d.year - ((reference_date.month, reference_date.day) < (d.month, d.day))
            if age < min_age or age > max_age:
                b_key = str(row.get(business_key_col, f"row_{idx}"))
                failures.append(
                    RuleFailure(
                        row_index=int(idx),
                        business_key=b_key,
                        rule_name="check_customer_age",
                        category="consistency",
                        column_name=birth_date_col,
                        reason=f"Calculated age {age} (DOB {val}) is outside valid range [{min_age}, {max_age}]",
                    )
                )
    return failures


def check_policy_dates(
    df: pd.DataFrame,
    start_col: str = "start_date",
    end_col: str = "end_date",
    business_key_col: str = "policy_id",
) -> List[RuleFailure]:
    """Verify policy end_date >= start_date."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        start_d = parse_iso_date(row.get(start_col))
        end_d = parse_iso_date(row.get(end_col))
        if start_d is not None and end_d is not None and end_d < start_d:
            b_key = str(row.get(business_key_col, f"row_{idx}"))
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_policy_dates",
                    category="consistency",
                    column_name=end_col,
                    reason=f"Policy end_date ({row[end_col]}) is before start_date ({row[start_col]})",
                )
            )
    return failures


def check_claim_approved_amount(
    df: pd.DataFrame,
    claim_amt_col: str = "claim_amount",
    approved_amt_col: str = "approved_amount",
    business_key_col: str = "claim_id",
) -> List[RuleFailure]:
    """Verify approved_amount <= claim_amount and approved_amount >= 0."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        b_key = str(row.get(business_key_col, f"row_{idx}"))
        try:
            claim_amt = Decimal(str(row.get(claim_amt_col)).strip())
            appr_amt = Decimal(str(row.get(approved_amt_col)).strip())
        except (InvalidOperation, TypeError, AttributeError):
            continue  # Handled by numeric range check

        if appr_amt > claim_amt:
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_claim_approved_amount",
                    category="consistency",
                    column_name=approved_amt_col,
                    reason=f"Approved amount ({appr_amt}) exceeds claim amount ({claim_amt})",
                )
            )
        elif appr_amt < Decimal("0.00"):
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_claim_approved_amount",
                    category="consistency",
                    column_name=approved_amt_col,
                    reason=f"Approved amount ({appr_amt}) cannot be negative",
                )
            )
    return failures


def check_claim_within_policy_period(
    claims_df: pd.DataFrame,
    policies_df: pd.DataFrame,
    claim_date_col: str = "claim_date",
    policy_id_col: str = "policy_id",
    business_key_col: str = "claim_id",
) -> List[RuleFailure]:
    """Verify cross-table consistency: claim_date must fall within policy coverage period."""
    failures: List[RuleFailure] = []
    # Build policy date lookup
    policy_lookup: Dict[str, Tuple[Optional[date], Optional[date]]] = {}
    for _, prow in policies_df.iterrows():
        p_id = str(prow.get("policy_id", "")).strip()
        if p_id:
            s_date = parse_iso_date(prow.get("start_date"))
            e_date = parse_iso_date(prow.get("end_date"))
            policy_lookup[p_id] = (s_date, e_date)

    for idx, crow in claims_df.iterrows():
        p_id = str(crow.get(policy_id_col, "")).strip()
        c_date = parse_iso_date(crow.get(claim_date_col))
        if not p_id or c_date is None or p_id not in policy_lookup:
            continue

        p_start, p_end = policy_lookup[p_id]
        if p_start is not None and c_date < p_start:
            b_key = str(crow.get(business_key_col, f"row_{idx}"))
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_claim_within_policy_period",
                    category="consistency",
                    column_name=claim_date_col,
                    reason=f"Claim date ({crow[claim_date_col]}) is before policy start date ({p_start}) for policy {p_id}",
                )
            )
        elif p_end is not None and c_date > p_end:
            b_key = str(crow.get(business_key_col, f"row_{idx}"))
            failures.append(
                RuleFailure(
                    row_index=int(idx),
                    business_key=b_key,
                    rule_name="check_claim_within_policy_period",
                    category="consistency",
                    column_name=claim_date_col,
                    reason=f"Claim date ({crow[claim_date_col]}) is after policy end date ({p_end}) for policy {p_id}",
                )
            )
    return failures


def check_claim_approved_amount_completeness(
    df: pd.DataFrame,
    status_col: str = "status",
    approved_amt_col: str = "approved_amount",
    business_key_col: str = "claim_id",
) -> List[RuleFailure]:
    """Verify approved_amount is provided if claim status is not SUBMITTED."""
    failures: List[RuleFailure] = []
    for idx, row in df.iterrows():
        status = str(row.get(status_col, "")).strip()
        val = row.get(approved_amt_col)
        if status in ("APPROVED", "PARTIALLY_APPROVED", "REJECTED"):
            is_empty = (
                pd.isna(val)
                or val is None
                or str(val).strip() == ""
                or str(val).strip().lower() in ("nan", "null")
            )
            if is_empty:
                b_key = str(row.get(business_key_col, f"row_{idx}"))
                failures.append(
                    RuleFailure(
                        row_index=int(idx),
                        business_key=b_key,
                        rule_name="check_claim_approved_amount_completeness",
                        category="completeness",
                        column_name=approved_amt_col,
                        reason=f"Missing approved_amount for adjudicated claim with status '{status}'",
                    )
                )
    return failures


def check_payment_dates_validity(
    df: pd.DataFrame,
    payment_date_col: str = "payment_date",
    status_col: str = "status",
    business_key_col: str = "payment_id",
    reference_date: date = REFERENCE_DATE,
) -> List[RuleFailure]:
    """Verify that completed/failed payments are not in the future, and pending payments are within reasonable range."""
    failures: List[RuleFailure] = []
    # Pending payments generated near end of December 2025 can be scheduled in Jan 2026 (reference date + 60 days)
    max_pending_date = date(2026, 3, 1)
    for idx, row in df.iterrows():
        val = row.get(payment_date_col)
        status = str(row.get(status_col, "")).strip()
        d = parse_iso_date(val)
        if d is not None:
            if status in ("COMPLETED", "FAILED") and d > reference_date:
                b_key = str(row.get(business_key_col, f"row_{idx}"))
                failures.append(
                    RuleFailure(
                        row_index=int(idx),
                        business_key=b_key,
                        rule_name="check_payment_dates_validity",
                        category="consistency",
                        column_name=payment_date_col,
                        reason=f"Payment date ({val}) for {status} payment is after reference date {reference_date}",
                    )
                )
            elif status == "PENDING" and d > max_pending_date:
                b_key = str(row.get(business_key_col, f"row_{idx}"))
                failures.append(
                    RuleFailure(
                        row_index=int(idx),
                        business_key=b_key,
                        rule_name="check_payment_dates_validity",
                        category="consistency",
                        column_name=payment_date_col,
                        reason=f"Pending payment date ({val}) is too far in future (exceeds {max_pending_date})",
                    )
                )
    return failures


def check_payment_after_claim(
    payments_df: pd.DataFrame,
    claims_df: pd.DataFrame,
    payment_date_col: str = "payment_date",
    claim_id_col: str = "claim_id",
    business_key_col: str = "payment_id",
) -> List[RuleFailure]:
    """Verify that payment_date >= associated claim's claim_date."""
    failures: List[RuleFailure] = []
    claim_dates: Dict[str, date] = {}
    for _, crow in claims_df.iterrows():
        cid = str(crow.get("claim_id", "")).strip()
        cd = parse_iso_date(crow.get("claim_date"))
        if cid and cd:
            claim_dates[cid] = cd

    for idx, prow in payments_df.iterrows():
        cid = str(prow.get(claim_id_col, "")).strip()
        pdate = parse_iso_date(prow.get(payment_date_col))
        if cid in claim_dates and pdate is not None:
            cdate = claim_dates[cid]
            if pdate < cdate:
                b_key = str(prow.get(business_key_col, f"row_{idx}"))
                failures.append(
                    RuleFailure(
                        row_index=int(idx),
                        business_key=b_key,
                        rule_name="check_payment_after_claim",
                        category="consistency",
                        column_name=payment_date_col,
                        reason=f"Payment date ({pdate}) is before claim date ({cdate}) for claim {cid}",
                    )
                )
    return failures

