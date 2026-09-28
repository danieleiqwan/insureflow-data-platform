-- InsureFlow Phase 1 Relational Schema
-- Source-system model: customers, policies, claims, payments

DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS claims CASCADE;
DROP TABLE IF EXISTS policies CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

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

-- 3. Claims Table
CREATE TABLE claims (
    claim_id        VARCHAR(9)      PRIMARY KEY,
    policy_id       VARCHAR(8)      NOT NULL,
    facility_id     VARCHAR(50),
    claim_date      DATE            NOT NULL,
    claim_type      VARCHAR(50)     NOT NULL,
    claim_amount    NUMERIC(12,2)   NOT NULL,
    approved_amount NUMERIC(12,2),
    status          VARCHAR(20)     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT fk_claims_policy_id FOREIGN KEY (policy_id)
        REFERENCES policies(policy_id) ON DELETE RESTRICT,
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
