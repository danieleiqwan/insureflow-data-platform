"""Synthetic customer data generator for InsureFlow.

Generates reproducible, synthetic Malaysian customer records adhering to Phase 1
specifications:
- 1,000 unique sequential customer IDs (C000001 to C001000)
- Curated Malaysian name distributions (Malay, Chinese, Indian)
- Realistic curated occupations (30 options)
- 13 Malaysian states + 3 Federal Territories
- Deterministic timestamps derived from a fixed reference date
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import random
from typing import List, Tuple

from faker import Faker
import pandas as pd

DEFAULT_SEED = 42
DEFAULT_CUSTOMER_COUNT = 1000
REFERENCE_DATE = date(2026, 1, 1)

# Malaysian administrative divisions: 13 states + 3 Federal Territories
MALAYSIAN_STATES = [
    "Johor",
    "Kedah",
    "Kelantan",
    "Melaka",
    "Negeri Sembilan",
    "Pahang",
    "Perak",
    "Perlis",
    "Pulau Pinang",
    "Sabah",
    "Sarawak",
    "Selangor",
    "Terengganu",
    "Kuala Lumpur",
    "Putrajaya",
    "Labuan",
]

# 30 curated realistic Malaysian occupations across diverse industries
MALAYSIAN_OCCUPATIONS = [
    "Accountant",
    "Administrative Assistant",
    "Bank Officer",
    "Chef",
    "Civil Servant",
    "Construction Supervisor",
    "Customer Service Representative",
    "Doctor",
    "Driver",
    "Electrician",
    "Factory Technician",
    "Financial Analyst",
    "Graphic Designer",
    "HR Executive",
    "Lecturer",
    "Legal Assistant",
    "Marketing Specialist",
    "Mechanical Engineer",
    "Nurse",
    "Operations Manager",
    "Palm Oil Mill Operator",
    "Pharmacist",
    "Police Officer",
    "Retail Assistant",
    "Sales Executive",
    "Security Guard",
    "Software Engineer",
    "Teacher",
    "Warehouse Coordinator",
    "Welder",
]

# Curated synthetic names by ethnic community
# Demographic weights: ~60% Malay, ~28% Chinese, ~12% Indian
ETHNIC_GROUPS = ["Malay", "Chinese", "Indian"]
ETHNIC_WEIGHTS = [0.60, 0.28, 0.12]

MALAY_MALE_FIRST_NAMES = [
    "Ahmad", "Muhammad", "Mohd", "Hafiz", "Danial", "Amir", "Farhan",
    "Khairul", "Zikri", "Aiman", "Syamil", "Harith", "Faizal", "Azman",
    "Haziq", "Hakim", "Izzat", "Syafiq", "Firdaus", "Luqman"
]

MALAY_FEMALE_FIRST_NAMES = [
    "Siti", "Nur", "Aisyah", "Farhana", "Nadhirah", "Anis", "Ain",
    "Zulaikha", "Sarah", "Intan", "Puteri", "Mastura", "Nadia", "Syahirah",
    "Hidayah", "Fazira", "Alia", "Aqilah", "Balqis", "Diana"
]

MALAY_LAST_NAMES = [
    "Abdullah", "Ismail", "Ibrahim", "Othman", "Razak", "Yusof", "Hashim",
    "Mansor", "Salleh", "Ariffin", "Hamzah", "Sulaiman", "Zulkifli", "Daud",
    "Karim", "Baharuddin", "Ghani", "Halim", "Jaafar", "Kassim"
]

CHINESE_MALE_FIRST_NAMES = [
    "Wei Kang", "Jun Jie", "Zhi Wei", "Ming Yang", "Jia Hao", "Kah Seng",
    "Wai Loon", "Chee Keong", "Kok Wai", "Jian Hao", "Zi Yang", "Hong Sen",
    "Kian Wee", "Boon Teck", "Chun Kit", "Meng Tat"
]

CHINESE_FEMALE_FIRST_NAMES = [
    "Mei Ling", "Shu Ting", "Xin Yi", "Xiao Wei", "Jia Ying", "Hui Min",
    "Li Ting", "Pei Shan", "Zi Xuan", "Yan Ling", "Sue Anne", "Zhi Ling",
    "Wen Jing", "Ying Ying", "Siew Ling", "Chui Ping"
]

CHINESE_LAST_NAMES = [
    "Tan", "Lee", "Wong", "Lim", "Chong", "Ng", "Lau", "Goh",
    "Chan", "Teoh", "Low", "Chia", "Yeoh", "Sim", "Ho", "Khoo",
    "Liew", "Ong", "Tee", "Yap"
]

INDIAN_MALE_FIRST_NAMES = [
    "Suresh", "Ravi", "Prakash", "Mohan", "Dinesh", "Rajan", "Vijay",
    "Anand", "Kumar", "Karthik", "Saravanan", "Vignesh", "Murugan",
    "Selvam", "Sanjay", "Ganesh"
]

INDIAN_FEMALE_FIRST_NAMES = [
    "Priya", "Anitha", "Kavitha", "Deepa", "Shanti", "Divya", "Meena",
    "Shalini", "Preethi", "Lakshmi", "Pavithra", "Bavani", "Revathi",
    "Gayathri", "Geetha", "Tharani"
]

INDIAN_LAST_NAMES = [
    "Subramaniam", "Pillai", "Nair", "Raman", "Krishnan", "Murugan",
    "Chettiar", "Appadurai", "Devadas", "Chandran", "Ganesan", "Nambiar",
    "Menon", "Sundaram", "Naidu"
]


def calculate_age(dob: date, ref_date: date) -> int:
    """Calculate age in complete years at a given reference date."""
    return ref_date.year - dob.year - (
        (ref_date.month, ref_date.day) < (dob.month, dob.day)
    )


def generate_dob(min_age: int, max_age: int, ref_date: date, rng: random.Random) -> date:
    """Generate a deterministic date of birth such that age is in [min_age, max_age] at ref_date."""
    # Days offset range roughly covering min_age to max_age
    # Exact check loop ensures edge-case leap years and exact birth dates comply
    while True:
        target_age = rng.randint(min_age, max_age)
        birth_year = ref_date.year - target_age
        month = rng.randint(1, 12)
        # Determine days in month
        if month in (1, 3, 5, 7, 8, 10, 12):
            max_day = 31
        elif month in (4, 6, 9, 11):
            max_day = 30
        else:
            is_leap = (birth_year % 4 == 0 and birth_year % 100 != 0) or (birth_year % 400 == 0)
            max_day = 29 if is_leap else 28
        day = rng.randint(1, max_day)
        dob = date(birth_year, month, day)
        age = calculate_age(dob, ref_date)
        if min_age <= age <= max_age:
            return dob


def generate_name(gender: str, rng: random.Random) -> Tuple[str, str]:
    """Generate a culturally appropriate Malaysian name (first_name, last_name)."""
    community = rng.choices(ETHNIC_GROUPS, weights=ETHNIC_WEIGHTS, k=1)[0]

    if community == "Malay":
        last_name = rng.choice(MALAY_LAST_NAMES)
        first_name = (
            rng.choice(MALAY_MALE_FIRST_NAMES)
            if gender == "Male"
            else rng.choice(MALAY_FEMALE_FIRST_NAMES)
        )
    elif community == "Chinese":
        last_name = rng.choice(CHINESE_LAST_NAMES)
        first_name = (
            rng.choice(CHINESE_MALE_FIRST_NAMES)
            if gender == "Male"
            else rng.choice(CHINESE_FEMALE_FIRST_NAMES)
        )
    else:  # Indian
        last_name = rng.choice(INDIAN_LAST_NAMES)
        first_name = (
            rng.choice(INDIAN_MALE_FIRST_NAMES)
            if gender == "Male"
            else rng.choice(INDIAN_FEMALE_FIRST_NAMES)
        )

    return first_name, last_name


def build_customers(
    count: int = DEFAULT_CUSTOMER_COUNT,
    seed: int = DEFAULT_SEED,
    ref_date: date = REFERENCE_DATE,
) -> pd.DataFrame:
    """Generate a DataFrame of synthetic customers deterministically.

    Args:
        count: Number of customer records to generate.
        seed: Random seed for reproducibility.
        ref_date: Fixed reference date for age calculations and created_at timestamps.

    Returns:
        pd.DataFrame containing generated customer records.
    """
    rng = random.Random(seed)
    Faker.seed(seed)
    _ = Faker()  # Ensures Faker generator is initialized and seeded

    records = []
    base_ref_dt = datetime(ref_date.year, ref_date.month, ref_date.day, 0, 0, 0, tzinfo=timezone.utc)

    for i in range(1, count + 1):
        customer_id = f"C{i:06d}"
        gender = rng.choice(["Male", "Female"])
        first_name, last_name = generate_name(gender, rng)
        dob = generate_dob(min_age=18, max_age=65, ref_date=ref_date, rng=rng)
        state = rng.choice(MALAYSIAN_STATES)
        occupation = rng.choice(MALAYSIAN_OCCUPATIONS)

        # Deterministic created_at within past 2 years (1 to 730 days before ref_date)
        days_back = rng.randint(1, 730)
        seconds_offset = rng.randint(0, 86399)
        created_dt = base_ref_dt - timedelta(days=days_back, seconds=seconds_offset)
        created_at_str = created_dt.strftime("%Y-%m-%d %H:%M:%S%z")
        # Format timezone as +00:00 for standard ISO compatibility
        if created_at_str.endswith("+0000"):
            created_at_str = created_at_str[:-5] + "+00:00"

        records.append({
            "customer_id": customer_id,
            "first_name": first_name,
            "last_name": last_name,
            "gender": gender,
            "date_of_birth": dob.isoformat(),
            "state": state,
            "occupation": occupation,
            "created_at": created_at_str,
        })

    columns = [
        "customer_id",
        "first_name",
        "last_name",
        "gender",
        "date_of_birth",
        "state",
        "occupation",
        "created_at",
    ]
    return pd.DataFrame(records, columns=columns)


def validate_customers(
    df: pd.DataFrame,
    expected_count: int = DEFAULT_CUSTOMER_COUNT,
    ref_date: date = REFERENCE_DATE,
) -> None:
    """Validate customer DataFrame against all Phase 1 data contract rules.

    Raises:
        ValueError: If any validation rule fails.
    """
    # 1. Row count validation
    if len(df) != expected_count:
        raise ValueError(
            f"Expected exactly {expected_count} rows, found {len(df)}"
        )

    # 2. Check for null or empty values
    if df.isna().any().any():
        null_counts = df.isna().sum().to_dict()
        raise ValueError(f"Dataset contains null values: {null_counts}")

    # 3. Customer ID uniqueness and format
    expected_ids = [f"C{i:06d}" for i in range(1, expected_count + 1)]
    actual_ids = df["customer_id"].tolist()
    if actual_ids != expected_ids:
        if len(set(actual_ids)) != len(actual_ids):
            raise ValueError("customer_id column contains duplicate IDs")
        raise ValueError(
            f"customer_id does not follow sequential C000001..C{expected_count:06d} format"
        )

    # 4. Gender validation
    valid_genders = {"Male", "Female"}
    invalid_genders = set(df["gender"]) - valid_genders
    if invalid_genders:
        raise ValueError(f"Found invalid gender values: {invalid_genders}")

    # 5. State validation
    valid_states = set(MALAYSIAN_STATES)
    invalid_states = set(df["state"]) - valid_states
    if invalid_states:
        raise ValueError(f"Found invalid state values: {invalid_states}")

    # 6. Occupation validation
    valid_occupations = set(MALAYSIAN_OCCUPATIONS)
    invalid_occupations = set(df["occupation"]) - valid_occupations
    if invalid_occupations:
        raise ValueError(f"Found invalid occupation values: {invalid_occupations}")

    # 7. Date of birth & Age validation (18-65 at ref_date)
    for idx, row in df.iterrows():
        dob = date.fromisoformat(row["date_of_birth"])
        age = calculate_age(dob, ref_date)
        if not (18 <= age <= 65):
            raise ValueError(
                f"Customer {row['customer_id']} age {age} (DOB: {dob}) is out of [18, 65] range at {ref_date}"
            )

    # 8. created_at timestamp validation
    for idx, row in df.iterrows():
        ts_str = row["created_at"]
        try:
            created_dt = datetime.fromisoformat(ts_str)
            if created_dt.date() > ref_date:
                raise ValueError(
                    f"Customer {row['customer_id']} created_at {ts_str} is after reference date {ref_date}"
                )
        except Exception as e:
            raise ValueError(f"Invalid timestamp format in created_at '{ts_str}': {e}")


def write_customers_to_csv(df: pd.DataFrame, output_path: Path) -> Path:
    """Save customer DataFrame to CSV with UTF-8 encoding and LF line endings."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Write using standard csv export with LF line endings
    df.to_csv(output_path, index=False, encoding="utf-8", lineterminator="\n")
    return output_path


def main() -> None:
    """Entry point for generating and saving synthetic customers."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    output_file = repo_root / "data" / "raw" / "customers.csv"

    print("Generating synthetic customers (Phase 1)...")
    df = build_customers(
        count=DEFAULT_CUSTOMER_COUNT,
        seed=DEFAULT_SEED,
        ref_date=REFERENCE_DATE,
    )

    print("Validating customer dataset contract...")
    validate_customers(df, expected_count=DEFAULT_CUSTOMER_COUNT, ref_date=REFERENCE_DATE)

    print(f"Writing to CSV: {output_file}")
    write_customers_to_csv(df, output_file)

    print("\n--- Generation Summary ---")
    print(f"Total records generated : {len(df)}")
    print(f"Output path             : {output_file}")
    print("\nFirst 5 records:")
    print(df.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
