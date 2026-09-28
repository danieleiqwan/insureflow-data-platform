-- InsureFlow Phase 3 Bronze Schema DDL
-- Raw ingested data store: all business columns are TEXT, append-only,
-- no type coercion, no business constraints. Metadata tracking on every table.

CREATE SCHEMA IF NOT EXISTS bronze;

-- 0. Ingestion Audit Log
CREATE TABLE IF NOT EXISTS bronze.ingestion_log (
    batch_id            UUID            PRIMARY KEY,
    source_file         TEXT            NOT NULL,
    file_sha256         TEXT            NOT NULL,
    file_size_bytes     BIGINT          NOT NULL,
    rows_loaded         INT             NOT NULL,
    status              VARCHAR(20)     NOT NULL,
    error_message       TEXT,
    started_at          TIMESTAMPTZ     NOT NULL,
    finished_at         TIMESTAMPTZ     NOT NULL,
    CONSTRAINT chk_bronze_ingestion_log_status CHECK (status IN ('SUCCESS', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_bronze_ingestion_log_file_sha256
    ON bronze.ingestion_log(file_sha256);

-- 1. Bronze Customers
CREATE TABLE IF NOT EXISTS bronze.customers (
    _batch_id           UUID            NOT NULL,
    _source_file        TEXT            NOT NULL,
    _source_row_number  INT             NOT NULL,
    _ingested_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
    customer_id         TEXT,
    first_name          TEXT,
    last_name           TEXT,
    gender              TEXT,
    date_of_birth       TEXT,
    state               TEXT,
    occupation          TEXT,
    created_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_bronze_customers_batch_id
    ON bronze.customers(_batch_id);

-- 2. Bronze Policies
CREATE TABLE IF NOT EXISTS bronze.policies (
    _batch_id           UUID            NOT NULL,
    _source_file        TEXT            NOT NULL,
    _source_row_number  INT             NOT NULL,
    _ingested_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
    policy_id           TEXT,
    customer_id         TEXT,
    policy_type         TEXT,
    start_date          TEXT,
    end_date            TEXT,
    premium             TEXT,
    status              TEXT,
    created_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_bronze_policies_batch_id
    ON bronze.policies(_batch_id);

-- 3. Bronze Claims
CREATE TABLE IF NOT EXISTS bronze.claims (
    _batch_id           UUID            NOT NULL,
    _source_file        TEXT            NOT NULL,
    _source_row_number  INT             NOT NULL,
    _ingested_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
    claim_id            TEXT,
    policy_id           TEXT,
    facility_id         TEXT,
    claim_date          TEXT,
    claim_type          TEXT,
    claim_amount        TEXT,
    approved_amount     TEXT,
    status              TEXT,
    created_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_bronze_claims_batch_id
    ON bronze.claims(_batch_id);

-- 4. Bronze Payments
CREATE TABLE IF NOT EXISTS bronze.payments (
    _batch_id           UUID            NOT NULL,
    _source_file        TEXT            NOT NULL,
    _source_row_number  INT             NOT NULL,
    _ingested_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
    payment_id          TEXT,
    claim_id            TEXT,
    payment_date        TEXT,
    amount              TEXT,
    payment_method      TEXT,
    status              TEXT,
    created_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_bronze_payments_batch_id
    ON bronze.payments(_batch_id);

-- 5. Bronze Facilities Master (MOH Registry)
CREATE TABLE IF NOT EXISTS bronze.facilities_master (
    _batch_id           UUID            NOT NULL,
    _source_file        TEXT            NOT NULL,
    _source_row_number  INT             NOT NULL,
    _ingested_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
    index               TEXT,
    kod_fasiliti        TEXT,
    status              TEXT,
    sektor              TEXT,
    subsektor           TEXT,
    program_group       TEXT,
    negeri              TEXT,
    daerah              TEXT,
    kategori_fasiliti   TEXT,
    jenis_fasiliti      TEXT,
    nama                TEXT,
    alamat              TEXT,
    bandar              TEXT,
    poskod              TEXT,
    daerah_pentadbiran  TEXT,
    telefon             TEXT,
    emel                TEXT,
    urban_rural         TEXT,
    latitud             TEXT,
    longitud            TEXT
);

CREATE INDEX IF NOT EXISTS idx_bronze_facilities_master_batch_id
    ON bronze.facilities_master(_batch_id);
