-- InsureFlow Phase 4A Silver Schema DDL
-- Conformed, typed, and constrained warehouse tables in the `silver` schema.
-- Absorbs and supersedes the initial public.* relational design (ADR-014).

CREATE SCHEMA IF NOT EXISTS silver;

-- 0. Facilities Table
-- Sourced from bronze.facilities_master (MOH Malaysia registry)
-- ADR-010: Real facility codes (KOD_FASILITI) as PK.
-- ADR-011: facility_category and subsector are NOT NULL without hardcoded CHECK constraints.
CREATE TABLE IF NOT EXISTS silver.facilities (
    facility_id         VARCHAR(15)     PRIMARY KEY,
    facility_name       VARCHAR(200)    NOT NULL,
    facility_category   VARCHAR(50)     NOT NULL,
    facility_type       VARCHAR(100)    NOT NULL,
    subsector           VARCHAR(10)     NOT NULL,
    state               VARCHAR(60)     NOT NULL,
    district            VARCHAR(60)     NOT NULL,
    postcode            VARCHAR(10),
    latitude            NUMERIC(9,6)    NOT NULL,
    longitude           NUMERIC(9,6)    NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- 1. Customers Table
CREATE TABLE IF NOT EXISTS silver.customers (
    customer_id     VARCHAR(7)      PRIMARY KEY,
    first_name      VARCHAR(100)    NOT NULL,
    last_name       VARCHAR(100)    NOT NULL,
    gender          VARCHAR(10)     NOT NULL,
    date_of_birth   DATE            NOT NULL,
    state           VARCHAR(50)     NOT NULL,
    occupation      VARCHAR(100)    NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT chk_silver_customers_gender CHECK (gender IN ('Male', 'Female'))
);

-- 2. Policies Table
CREATE TABLE IF NOT EXISTS silver.policies (
    policy_id       VARCHAR(8)      PRIMARY KEY,
    customer_id     VARCHAR(7)      NOT NULL,
    policy_type     VARCHAR(50)     NOT NULL,
    start_date      DATE            NOT NULL,
    end_date        DATE            NOT NULL,
    premium         NUMERIC(12,2)   NOT NULL,
    status          VARCHAR(20)     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT fk_silver_policies_customer_id FOREIGN KEY (customer_id)
        REFERENCES silver.customers(customer_id) ON DELETE RESTRICT,
    CONSTRAINT chk_silver_policies_dates CHECK (end_date >= start_date),
    CONSTRAINT chk_silver_policies_premium CHECK (premium >= 0),
    CONSTRAINT chk_silver_policies_policy_type CHECK (
        policy_type IN ('MEDICAL', 'HOSPITALIZATION', 'CRITICAL_ILLNESS', 'PERSONAL_ACCIDENT')
    ),
    CONSTRAINT chk_silver_policies_status CHECK (
        status IN ('ACTIVE', 'EXPIRED', 'CANCELLED', 'LAPSED')
    )
);

CREATE INDEX IF NOT EXISTS idx_silver_policies_customer_id ON silver.policies(customer_id);

-- 3. Claims Table
CREATE TABLE IF NOT EXISTS silver.claims (
    claim_id        VARCHAR(9)      PRIMARY KEY,
    policy_id       VARCHAR(8)      NOT NULL,
    facility_id     VARCHAR(15)     NOT NULL,
    claim_date      DATE            NOT NULL,
    claim_type      VARCHAR(50)     NOT NULL,
    claim_amount    NUMERIC(12,2)   NOT NULL,
    approved_amount NUMERIC(12,2),
    status          VARCHAR(20)     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT fk_silver_claims_policy_id FOREIGN KEY (policy_id)
        REFERENCES silver.policies(policy_id) ON DELETE RESTRICT,
    CONSTRAINT fk_silver_claims_facility_id FOREIGN KEY (facility_id)
        REFERENCES silver.facilities(facility_id) ON DELETE RESTRICT,
    CONSTRAINT chk_silver_claims_amounts CHECK (
        claim_amount >= 0 AND (
            approved_amount IS NULL OR (
                approved_amount >= 0 AND approved_amount <= claim_amount
            )
        )
    ),
    CONSTRAINT chk_silver_claims_claim_type CHECK (
        claim_type IN ('OUTPATIENT', 'INPATIENT', 'EMERGENCY', 'DENTAL')
    ),
    CONSTRAINT chk_silver_claims_status CHECK (
        status IN ('SUBMITTED', 'APPROVED', 'PARTIALLY_APPROVED', 'REJECTED')
    )
);

CREATE INDEX IF NOT EXISTS idx_silver_claims_policy_id ON silver.claims(policy_id);
CREATE INDEX IF NOT EXISTS idx_silver_claims_facility_id ON silver.claims(facility_id);

-- 4. Payments Table
CREATE TABLE IF NOT EXISTS silver.payments (
    payment_id      VARCHAR(9)      PRIMARY KEY,
    claim_id        VARCHAR(9)      NOT NULL,
    payment_date    DATE            NOT NULL,
    amount          NUMERIC(12,2)   NOT NULL,
    payment_method  VARCHAR(20)     NOT NULL,
    status          VARCHAR(20)     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT fk_silver_payments_claim_id FOREIGN KEY (claim_id)
        REFERENCES silver.claims(claim_id) ON DELETE RESTRICT,
    CONSTRAINT chk_silver_payments_amount CHECK (amount >= 0),
    CONSTRAINT chk_silver_payments_method CHECK (
        payment_method IN ('BANK_TRANSFER', 'CHEQUE', 'CARD', 'E_WALLET')
    ),
    CONSTRAINT chk_silver_payments_status CHECK (
        status IN ('PENDING', 'COMPLETED', 'FAILED')
    )
);

CREATE INDEX IF NOT EXISTS idx_silver_payments_claim_id ON silver.payments(claim_id);

-- 5. Rejected Rows Table
-- Stores unparseable or constraint-violating rows from Bronze transformation
CREATE TABLE IF NOT EXISTS silver.rejected_rows (
    source_table        TEXT            NOT NULL,
    source_batch_id     UUID,
    source_row_number   INT,
    reject_reason       TEXT            NOT NULL,
    raw_row             JSONB           NOT NULL,
    rejected_at         TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_silver_rejected_rows_table ON silver.rejected_rows(source_table);
CREATE INDEX IF NOT EXISTS idx_silver_rejected_rows_batch ON silver.rejected_rows(source_batch_id);
