-- InsureFlow Phase 2B Relational Schema
-- Source-system model: facilities, customers, policies, claims, payments
-- Supersedes Phase 1 schema; init scripts run only on an empty volume.

DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS claims CASCADE;
DROP TABLE IF EXISTS policies CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS facilities CASCADE;

-- 0. Facilities Table (Phase 2B)
--    Source: MOH Malaysia facilities_master.csv (KOD_FASILITI)
--    ADR-010: Real facility codes are used as the PK so that claims.facility_id
--             carries a traceable, government-issued identifier rather than a
--             surrogate key. Supersedes ADR-007.
CREATE TABLE facilities (
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
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT chk_facilities_category CHECK (
        facility_category IN (
            'HOSPITAL', 'KLINIK', 'KLINIK PERGIGIAN',
            'PEJABAT KESIHATAN', 'PEJABAT KESIHATAN PERGIGIAN',
            'LAIN-LAIN', 'PUSAT PROMOSI KESIHATAN',
            'INSTITUSI', 'JABATAN KESIHATAN NEGERI', 'MAKMAL', 'PEJABAT FARMASI'
        )
    ),
    CONSTRAINT chk_facilities_subsector CHECK (subsector IN ('KKM', 'KPT', 'ATM'))
);

-- 1. Customers Table
CREATE TABLE customers (
    customer_id     VARCHAR(7)      PRIMARY KEY,
    first_name      VARCHAR(100)    NOT NULL,
    last_name       VARCHAR(100)    NOT NULL,
    gender          VARCHAR(10)     NOT NULL,
    date_of_birth   DATE            NOT NULL,
    state           VARCHAR(50)     NOT NULL,
    occupation      VARCHAR(100)    NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT chk_customers_gender CHECK (gender IN ('Male', 'Female'))
);

-- 2. Policies Table
CREATE TABLE policies (
    policy_id       VARCHAR(8)      PRIMARY KEY,
    customer_id     VARCHAR(7)      NOT NULL,
    policy_type     VARCHAR(50)     NOT NULL,
    start_date      DATE            NOT NULL,
    end_date        DATE            NOT NULL,
    premium         NUMERIC(12,2)   NOT NULL,
    status          VARCHAR(20)     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT fk_policies_customer_id FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id) ON DELETE RESTRICT,
    CONSTRAINT chk_policies_dates CHECK (end_date >= start_date),
    CONSTRAINT chk_policies_premium CHECK (premium >= 0),
    CONSTRAINT chk_policies_policy_type CHECK (
        policy_type IN ('MEDICAL', 'HOSPITALIZATION', 'CRITICAL_ILLNESS', 'PERSONAL_ACCIDENT')
    ),
    CONSTRAINT chk_policies_status CHECK (
        status IN ('ACTIVE', 'EXPIRED', 'CANCELLED', 'LAPSED')
    )
);

CREATE INDEX idx_policies_customer_id ON policies(customer_id);

-- 3. Claims Table (Phase 2B: facility_id is NOT NULL FK to facilities)
CREATE TABLE claims (
    claim_id        VARCHAR(9)      PRIMARY KEY,
    policy_id       VARCHAR(8)      NOT NULL,
    facility_id     VARCHAR(15)     NOT NULL,
    claim_date      DATE            NOT NULL,
    claim_type      VARCHAR(50)     NOT NULL,
    claim_amount    NUMERIC(12,2)   NOT NULL,
    approved_amount NUMERIC(12,2),
    status          VARCHAR(20)     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT fk_claims_policy_id FOREIGN KEY (policy_id)
        REFERENCES policies(policy_id) ON DELETE RESTRICT,
    CONSTRAINT fk_claims_facility_id FOREIGN KEY (facility_id)
        REFERENCES facilities(facility_id) ON DELETE RESTRICT,
    CONSTRAINT chk_claims_amounts CHECK (
        claim_amount >= 0 AND (
            approved_amount IS NULL OR (
                approved_amount >= 0 AND approved_amount <= claim_amount
            )
        )
    ),
    CONSTRAINT chk_claims_claim_type CHECK (
        claim_type IN ('OUTPATIENT', 'INPATIENT', 'EMERGENCY', 'DENTAL')
    ),
    CONSTRAINT chk_claims_status CHECK (
        status IN ('SUBMITTED', 'APPROVED', 'PARTIALLY_APPROVED', 'REJECTED')
    )
);

CREATE INDEX idx_claims_policy_id ON claims(policy_id);
CREATE INDEX idx_claims_facility_id ON claims(facility_id);

-- 4. Payments Table
CREATE TABLE payments (
    payment_id      VARCHAR(9)      PRIMARY KEY,
    claim_id        VARCHAR(9)      NOT NULL,
    payment_date    DATE            NOT NULL,
    amount          NUMERIC(12,2)   NOT NULL,
    payment_method  VARCHAR(20)     NOT NULL,
    status          VARCHAR(20)     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT fk_payments_claim_id FOREIGN KEY (claim_id)
        REFERENCES claims(claim_id) ON DELETE RESTRICT,
    CONSTRAINT chk_payments_amount CHECK (amount >= 0),
    CONSTRAINT chk_payments_method CHECK (
        payment_method IN ('BANK_TRANSFER', 'CHEQUE', 'CARD', 'E_WALLET')
    ),
    CONSTRAINT chk_payments_status CHECK (
        status IN ('PENDING', 'COMPLETED', 'FAILED')
    )
);

CREATE INDEX idx_payments_claim_id ON payments(claim_id);
