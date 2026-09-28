# InsureFlow — Malaysian Public Healthcare Data Sources (Phase 2A)

| Metadata | Details |
|---|---|
| **Phase** | Phase 2A — Facility Data Source Acquisition |
| **Status** | Complete / Proposal Awaiting Review |
| **Date** | 2026-09-28 |
| **Script** | [`src/ingestion/download_sources.py`](../src/ingestion/download_sources.py) |
| **Storage Destination** | `data/raw/` |

---

## 1. Overview & Objective

In Phase 1, InsureFlow created the relational foundation including the `claims` table, where `facility_id VARCHAR(50)` was created as a plain nullable column without a foreign key constraint. The architecture specification noted that `facility_id` would be linked to real Malaysian healthcare facility data once acquired.

The objective of **Phase 2A** is to:
1. Investigate real Malaysian public healthcare open datasets from official portals (**data.gov.my**, **data.moh.gov.my / KKMNOW**, and the Ministry of Health Malaysia **MoH GitHub**).
2. Download candidates into `data/raw/` using standard library tools without external dependencies.
3. Profile each dataset for grain, columns, completeness, and facility-level identifiers.
4. Propose an architectural approach for `facility_id` and the facilities reference table for **Phase 2B**.

---

## 2. Investigated Datasets Summary

| Dataset Name | Source URL | Publisher | Licence & Required Attribution | Format | Grain | Rows | Update Frequency | Date Downloaded | SHA256 Checksum | Facility-level ID? |
|---|---|---|---|---|---|---:|---|---|---|:---:|
| **MOH Facilities Master Registry** (`facilities_master.csv`) | [MoH GitHub `data-resources-public`](https://raw.githubusercontent.com/MoH-Malaysia/data-resources-public/main/facilities_master.csv) | Ministry of Health Malaysia (KKM) | Government of Malaysia Open Data Terms of Use; Attribution: *"Ministry of Health Malaysia (Kementerian Kesihatan Malaysia)"* | Plain UTF-8 CSV | 1 health facility (hospital, clinic, dental clinic, office, lab) | 5,160 | Annual / Periodic (as of 31 Dec 2025) | 2026-09-28 | `99a5400f6c77d6b248edecea4fa9773f30bb9cfd0d94431cdc25fb9b443c2743` | **YES** (`KOD_FASILITI`, 100% unique) |
| **MOH Hospital Bed Utilisation** (`bedutil_facility.csv`) | [MoH GitHub `data-resources-public`](https://raw.githubusercontent.com/MoH-Malaysia/data-resources-public/main/bedutil_facility.csv) | Ministry of Health Malaysia (KKMNOW) | Government of Malaysia Open Data Terms of Use; Attribution: *"Ministry of Health Malaysia (KKMNOW)"* | Plain UTF-8 CSV | 1 public hospital | 149 | Daily / Periodic | 2026-09-28 | `499968f7cd552fcbd491457683536f669fe761cec18ee946bd08266986409bcb` | **NO** (Contains `hospital` name & state only; no code) |
| **data.gov.my Hospital Beds** (`hospital_beds.csv`) | [data.gov.my storage](https://storage.data.gov.my/healthcare/hospital_beds.csv) | Health Informatics Centre (PIK), MOH via data.gov.my | Creative Commons Attribution 4.0 International (CC BY 4.0) / Open Data Terms; Attribution: *"Ministry of Health Malaysia / data.gov.my"* | Plain UTF-8 CSV | Annual state/district bed count by hospital type (2015–2023) | 5,468 | Annual | 2026-09-28 | `3ef326b1edb7dbcac54e5140d27efb9d75be759e8c3c4cdb63b1a37b206d1d2e` | **NO** (Aggregated counts by state/district/type; no facilities) |
| **data.gov.my Hospital Beds API** (`api.data.gov.my`) | [API endpoint](https://api.data.gov.my/data-catalogue?id=hospital_beds) | Health Informatics Centre (PIK), MOH via data.gov.my | CC BY 4.0 | JSON REST API | Annual state/district bed count | 5,468 | Annual | 2026-09-28 | N/A (API mirror of CSV above) | **NO** (Identical to CSV above) |
| **MOH COVID-19 Epidemic Hospital Data** (`hospital.csv`) | [MoH GitHub `covid19-public`](https://raw.githubusercontent.com/MoH-Malaysia/covid19-public/main/epidemic/hospital.csv) | Ministry of Health Malaysia | Government of Malaysia Open Data Terms | Plain UTF-8 CSV | Daily state aggregate of COVID-19 bed/admissions | 23,000+ | Daily (historical archive) | Inspected (not downloaded) | N/A | **NO** (State-level grain: `date, state`) |
| **MOH Bed Utilisation by State** (`bedutil_state.csv`) | [MoH GitHub `data-resources-public`](https://raw.githubusercontent.com/MoH-Malaysia/data-resources-public/main/bedutil_state.csv) | Ministry of Health Malaysia (KKMNOW) | Government of Malaysia Open Data Terms | Plain UTF-8 CSV | 1 state (+ Malaysia national total) | 17 | Daily / Periodic | Inspected (not downloaded) | N/A | **NO** (State-level aggregate only) |

---

## 3. Profiles of Downloaded Datasets

### 3.1 MOH Facilities Master Registry (`facilities_master.csv`)

- **File Path**: `data/raw/facilities_master.csv`
- **File Size**: 955,590 bytes (933.19 KB)
- **Total Records**: 5,160 rows, 20 columns
- **Encoding**: UTF-8 comma-separated

#### Columns & Null Counts
| Column | Non-Null Count | Null Count | Data Type | Notes / Value Examples |
|---|---:|---:|---|---|
| `Index` | 5,160 | 0 | Integer | Sequential index 1..5160 |
| `KOD_FASILITI` | 5,160 | 0 | String | **Primary Facility Identifier** (e.g. `11-01030012`) |
| `STATUS` | 5,160 | 0 | String | Operational status: all 5,160 are `BUKA` (active) |
| `SEKTOR` | 5,160 | 0 | String | Sector: all 5,160 are `AWAM` (public) |
| `SUBSEKTOR` | 5,160 | 0 | String | `KKM` (5,145), `KPT` (10), `ATM` (5) |
| `PROGRAM_GROUP` | 0 | 5,160 | Float/NaN | Unpopulated in registry |
| `NEGERI` | 5,160 | 0 | String | State / Federal Territory |
| `DAERAH` | 5,160 | 0 | String | Administrative district |
| `KATEGORI_FASILITI` | 5,160 | 0 | String | Facility category (`KLINIK`, `HOSPITAL`, etc.) |
| `JENIS_FASILITI` | 5,160 | 0 | String | Subtype (e.g. `HOSPITAL PAKAR MINOR`, `KLINIK KESIHATAN`) |
| `NAMA` | 5,160 | 0 | String | Facility name |
| `ALAMAT` | 5,159 | 1 | String | Street address |
| `BANDAR` | 0 | 5,160 | Float/NaN | Unpopulated |
| `POSKOD` | 5,023 | 137 | Float/String | Postcode |
| `DAERAH_PENTADBIRAN`| 7 | 5,153 | String | Rarely populated |
| `TELEFON` | 536 | 4,624 | String | Phone number |
| `EMEL` | 59 | 5,101 | String | Email |
| `URBAN_RURAL` | 0 | 5,160 | Float/NaN | Unpopulated |
| `LATITUD` | 5,160 | 0 | Float | WGS84 decimal latitude (0 nulls) |
| `LONGITUD` | 5,160 | 0 | Float | WGS84 decimal longitude (0 nulls) |

#### Facility Breakdown by Category
| Category (`KATEGORI_FASILITI`) | Count | Relevance to Health Insurance Claims |
|---|---:|---|
| `KLINIK` | 2,915 | Direct match for `OUTPATIENT` claims |
| `KLINIK PERGIGIAN` | 1,682 | Direct match for `DENTAL` claims |
| `HOSPITAL` | 166 | Direct match for `INPATIENT` and `EMERGENCY` claims |
| `PEJABAT KESIHATAN` | 155 | District health admin offices (non-treatment) |
| `PEJABAT KESIHATAN PERGIGIAN` | 107 | Dental health admin offices (non-treatment) |
| `PUSAT PROMOSI KESIHATAN` | 38 | Health promotion centres |
| `INSTITUSI` | 19 | Specialised institutes (e.g. IMR, NIH) |
| `JABATAN KESIHATAN NEGERI` | 15 | State health departments |
| `MAKMAL` | 15 | Public health laboratories |
| `PEJABAT FARMASI` | 7 | Pharmacy regulatory offices |
| `LAIN-LAIN` | 41 | Other ancillary units |
| **Total** | **5,160** | **4,763 treatment-delivering sites (Hospitals + Clinics)** |

#### Geographic Coverage (States)
Covers all 13 Malaysian states and 3 Federal Territories:
Johor (589), Sarawak (560), Sabah (542), Pahang (522), Perak (492), Kelantan (429), Kedah (426), Selangor (379), Terengganu (290), Negeri Sembilan (263), Pulau Pinang (222), Melaka (169), W.P. Kuala Lumpur (138), Perlis (93), W.P. Labuan (25), W.P. Putrajaya (15), and joint border records (6).

#### Sample Records
```text
Index,KOD_FASILITI,STATUS,SEKTOR,SUBSEKTOR,PROGRAM_GROUP,NEGERI,DAERAH,KATEGORI_FASILITI,JENIS_FASILITI,NAMA,ALAMAT,BANDAR,POSKOD,DAERAH_PENTADBIRAN,TELEFON,EMEL,URBAN_RURAL,LATITUD,LONGITUD
1,11-01030012,BUKA,AWAM,KKM,,JOHOR,KLUANG,HOSPITAL,HOSPITAL PAKAR MINOR,"HOSPITAL ENCHE' BESAR HAJJAH KHALSOM",KM 5, JLN KOTA TINGGI,,86000.0,,,,2.00732,103.3472
2,11-01040013,BUKA,AWAM,KKM,,JOHOR,KOTA TINGGI,HOSPITAL,HOSPITAL TANPA PAKAR,HOSPITAL KOTA TINGGI,JALAN TUN HABAH,,81900.0,,,,1.73505,103.89951
3,11-01050014,BUKA,AWAM,KKM,,JOHOR,MERSING,HOSPITAL,HOSPITAL TANPA PAKAR,HOSPITAL MERSING,JALAN ISMAIL,,86800.0,,,,2.4297,103.84528
```

---

### 3.2 MOH Hospital Bed Utilisation (`bedutil_facility.csv`)

- **File Path**: `data/raw/bedutil_facility.csv`
- **File Size**: 9,211 bytes (9.00 KB)
- **Total Records**: 149 rows, 8 columns
- **Grain**: 1 public hospital
- **Columns**: `hospital`, `state`, `beds_nonicu`, `util_nonicu`, `beds_icu`, `util_icu`, `vent`, `util_vent`

#### Profile Findings
- Distinct hospitals: 149 distinct facility names across 16 states/federal territories.
- Null counts: `hospital` (0), `state` (0), `beds_nonicu` (40), `util_nonicu` (45), `beds_icu` (40), `util_icu` (93), `vent` (40), `util_vent` (88).
- **Facility ID Assessment**: **Does not provide standardized facility codes**. The identifier is free-form hospital name (e.g. `Hospital Sultanah Aminah`), which can match `facilities_master.csv` by name/state in future pipeline enrichment, but is insufficient as a standalone surrogate key.

---

### 3.3 data.gov.my Hospital Beds (`hospital_beds.csv`)

- **File Path**: `data/raw/hospital_beds.csv`
- **File Size**: 260,704 bytes (254.59 KB)
- **Total Records**: 5,468 rows, 5 columns
- **Grain**: Aggregated annual bed counts by state, district, and hospital sector type (`all`, `hospital_moh`, `hospital_non_moh`, `special_medical_institution`) from 2015 to 2023.
- **Columns**: `date`, `state`, `district`, `type`, `beds` (0 nulls across all columns).

#### Profile Findings
- Districts covered: 156 districts across 16 states/federal territories.
- **Facility ID Assessment**: **Contains no facility names or facility IDs**. This is an aggregate public health statistics dataset, not a facility registry. Useful for aggregate benchmark validation in analytics, but cannot be used for `claims.facility_id`.

---

## 4. Evaluation of Facility Candidates for InsureFlow

| Candidate Dataset | Facility-Level IDs? | Usable for `claims.facility_id`? | Reason |
|---|:---:|:---:|---|
| **`facilities_master.csv`** | **YES** | **YES (Recommended)** | 5,160 unique official facility codes (`KOD_FASILITI`), active status (`BUKA`), nationwide coverage, includes hospitals and clinics aligned with insurance claim types (`INPATIENT`, `OUTPATIENT`, `EMERGENCY`, `DENTAL`). |
| **`bedutil_facility.csv`** | **NO** | **NO (Enrichment only)** | Contains only 149 hospital names without standard codes. Useful later in Silver/Gold layers to enrich hospital dimensions with bed capacity. |
| **`hospital_beds.csv`** | **NO** | **NO** | Contains only state/district aggregate counts. No facility entities. |

---

## 5. Architectural Proposal for Phase 2B (Awaiting Approval)

### Option A: Derive Facilities from Real MoH Registry (`facilities_master.csv`) — **RECOMMENDED**

#### Why Option A:
1. **Authenticity**: Uses official Malaysian Ministry of Health facility codes (`KOD_FASILITI`, e.g. `11-01030012`), demonstrating genuine data engineering capability integrating real public data with simulated core systems.
2. **Domain Alignment**: Health insurance claims in InsureFlow are classified into four claim types:
   - `INPATIENT` & `EMERGENCY` → Served by `HOSPITAL` (166 facilities)
   - `OUTPATIENT` → Served by `KLINIK` (2,915 facilities) or `HOSPITAL`
   - `DENTAL` → Served by `KLINIK PERGIGIAN` (1,682 facilities)
3. **Geographic Correlation**: Each facility has precise Malaysian `NEGERI` (State), `DAERAH` (District), postcode, and decimal coordinates (`LATITUD`, `LONGITUD`). In Phase 2B, synthetic claim generators can sample facilities in the customer's home state or adjacent states, simulating realistic customer travel behavior.

#### Proposed Facilities Reference Table Schema:
In Phase 2B (or Silver layer in Phase 4), the raw 5,160 facilities can be filtered to active healthcare service facilities (hospitals and clinics) or ingested as a clean reference table:

```sql
CREATE TABLE facilities (
    facility_id         VARCHAR(15)     PRIMARY KEY, -- KOD_FASILITI (e.g. '11-01030012')
    facility_name       VARCHAR(150)    NOT NULL,    -- Cleaned title-case name
    facility_category   VARCHAR(50)     NOT NULL,    -- 'HOSPITAL', 'KLINIK', 'KLINIK PERGIGIAN'
    facility_type       VARCHAR(100)    NOT NULL,    -- e.g. 'HOSPITAL PAKAR', 'KLINIK KESIHATAN'
    subsector           VARCHAR(20)     NOT NULL,    -- 'KKM', 'KPT', 'ATM'
    state               VARCHAR(50)     NOT NULL,    -- Standardised state name
    district            VARCHAR(50)     NOT NULL,    -- District name
    postcode            VARCHAR(10),                 -- Postcode
    latitude            NUMERIC(9,6)    NOT NULL,    -- WGS84 latitude
    longitude           NUMERIC(9,6)    NOT NULL,    -- WGS84 longitude
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);
```

#### Proposed `claims.facility_id` Linkage:
- `claims.facility_id` (`VARCHAR(50)`) stores `KOD_FASILITI`.
- When `claims` are generated in Phase 2B:
  - If `claim_type IN ('INPATIENT', 'EMERGENCY')`: sample `facility_id` from facilities where `facility_category = 'HOSPITAL'`.
  - If `claim_type = 'DENTAL'`: sample `facility_id` from facilities where `facility_category = 'KLINIK PERGIGIAN'`.
  - If `claim_type = 'OUTPATIENT'`: sample `facility_id` from facilities where `facility_category IN ('KLINIK', 'HOSPITAL')`.
  - Prioritise facilities matching the policyholder's `customer.state`.

---

### Option B: Purely Synthetic Facility Registry (Fallback)

If real government codes are not preferred:
- Generate 100–200 synthetic facilities with formatted IDs: `FAC0001` … `FAC0200`.
- Assign synthetic Malaysian clinic and hospital names (e.g. *"Klinik Medika Damansara"*, *"Pusat Perubatan Pantai Indah"*).
- Synthesize random latitude/longitude within Malaysian state bounding boxes.

**Trade-off Comparison**:
- **Option A Pros**: Authentic government codes, zero synthetic distortion, demonstrates real-world public data integration, authentic GIS plotting for Power BI.
- **Option B Pros**: Perfectly controlled uniform distribution, no dependency on external schema changes.
- **Recommendation**: **Option A** is decisively superior for portfolio impact and engineering realism.

---

## 6. Next Steps & Approval Gate

1. **Awaiting User Decision**: User reviews Option A vs Option B.
2. **Phase 2B (Not Started)**: Upon approval of Option A, Phase 2B will implement:
   - Facilities extraction/clean reference generator.
   - Synthetic policy, claim, and payment generators linked to `customers` and `facilities`.
