-- InsureFlow Phase 5 Gold Schema DDL
-- Dimensional (star) model sourced from silver.*.
-- ADR-016: Natural keys used as PKs for all dimension tables.
--
-- Naming conventions:
--   Constraints: chk_gold_*, fk_gold_*, (PKs named by table default)
--   Indexes:     idx_gold_*
--
-- Gold trusts Silver's contract: no enum CHECK constraints duplicated here.
-- Money: NUMERIC(12,2). FKs: ON DELETE RESTRICT. Index all FK columns.

CREATE SCHEMA IF NOT EXISTS gold;

-- ─── DIMENSION: dim_date ──────────────────────────────────────────────────────
-- date_key is the date itself (DATE type). Using DATE rather than INT YYYYMMDD
-- avoids integer arithmetic in BI tools and is supported natively by PostgreSQL
-- and Power BI (see ADR-016).
CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_key        DATE            PRIMARY KEY,
    full_date       DATE            NOT NULL,
    year            SMALLINT        NOT NULL,
    quarter         SMALLINT        NOT NULL,
    month           SMALLINT        NOT NULL,
    month_name      VARCHAR(10)     NOT NULL,
    day             SMALLINT        NOT NULL,
    day_of_week     SMALLINT        NOT NULL,   -- 0=Sunday … 6=Saturday (ISO: 1=Mon, but kept 0=Sun for Power BI compatibility)
    day_name        VARCHAR(10)     NOT NULL,
    is_weekend      BOOLEAN         NOT NULL
);

-- ─── DIMENSION: dim_customer ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_customer (
    customer_id     VARCHAR(7)      PRIMARY KEY,
    first_name      VARCHAR(100)    NOT NULL,
    last_name       VARCHAR(100)    NOT NULL,
    gender          VARCHAR(10)     NOT NULL,
    date_of_birth   DATE            NOT NULL,
    age             SMALLINT        NOT NULL,   -- computed at load time relative to 2026-01-01
    state           VARCHAR(50)     NOT NULL,
    occupation      VARCHAR(100)    NOT NULL
);

-- ─── DIMENSION: dim_policy ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_policy (
    policy_id       VARCHAR(8)      PRIMARY KEY,
    customer_id     VARCHAR(7)      NOT NULL,
    policy_type     VARCHAR(50)     NOT NULL,
    start_date      DATE            NOT NULL,
    end_date        DATE            NOT NULL,
    premium         NUMERIC(12,2)   NOT NULL,
    status          VARCHAR(20)     NOT NULL,
    CONSTRAINT fk_gold_dim_policy_customer_id FOREIGN KEY (customer_id)
        REFERENCES gold.dim_customer(customer_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_gold_dim_policy_customer_id ON gold.dim_policy(customer_id);

-- ─── DIMENSION: dim_facility ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_facility (
    facility_id         VARCHAR(15)     PRIMARY KEY,
    facility_name       VARCHAR(200)    NOT NULL,
    facility_category   VARCHAR(50)     NOT NULL,
    facility_type       VARCHAR(100)    NOT NULL,
    state               VARCHAR(60)     NOT NULL,
    district            VARCHAR(60)     NOT NULL
);

-- ─── FACT: fact_claims ────────────────────────────────────────────────────────
-- customer_id is denormalized from dim_policy for easier state/demographic filtering
-- without an extra join.
CREATE TABLE IF NOT EXISTS gold.fact_claims (
    claim_id            VARCHAR(9)      PRIMARY KEY,
    policy_id           VARCHAR(8)      NOT NULL,
    customer_id         VARCHAR(7)      NOT NULL,
    facility_id         VARCHAR(15)     NOT NULL,
    claim_date_key      DATE            NOT NULL,
    claim_type          VARCHAR(50)     NOT NULL,
    claim_amount        NUMERIC(12,2)   NOT NULL,
    approved_amount     NUMERIC(12,2),
    status              VARCHAR(20)     NOT NULL,
    CONSTRAINT fk_gold_fact_claims_policy_id FOREIGN KEY (policy_id)
        REFERENCES gold.dim_policy(policy_id) ON DELETE RESTRICT,
    CONSTRAINT fk_gold_fact_claims_customer_id FOREIGN KEY (customer_id)
        REFERENCES gold.dim_customer(customer_id) ON DELETE RESTRICT,
    CONSTRAINT fk_gold_fact_claims_facility_id FOREIGN KEY (facility_id)
        REFERENCES gold.dim_facility(facility_id) ON DELETE RESTRICT,
    CONSTRAINT fk_gold_fact_claims_claim_date_key FOREIGN KEY (claim_date_key)
        REFERENCES gold.dim_date(date_key) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_gold_fact_claims_policy_id      ON gold.fact_claims(policy_id);
CREATE INDEX IF NOT EXISTS idx_gold_fact_claims_customer_id    ON gold.fact_claims(customer_id);
CREATE INDEX IF NOT EXISTS idx_gold_fact_claims_facility_id    ON gold.fact_claims(facility_id);
CREATE INDEX IF NOT EXISTS idx_gold_fact_claims_claim_date_key ON gold.fact_claims(claim_date_key);

-- ─── FACT: fact_payments ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.fact_payments (
    payment_id          VARCHAR(9)      PRIMARY KEY,
    claim_id            VARCHAR(9)      NOT NULL,
    payment_date_key    DATE            NOT NULL,
    amount              NUMERIC(12,2)   NOT NULL,
    payment_method      VARCHAR(20)     NOT NULL,
    status              VARCHAR(20)     NOT NULL,
    CONSTRAINT fk_gold_fact_payments_claim_id FOREIGN KEY (claim_id)
        REFERENCES gold.fact_claims(claim_id) ON DELETE RESTRICT,
    CONSTRAINT fk_gold_fact_payments_payment_date_key FOREIGN KEY (payment_date_key)
        REFERENCES gold.dim_date(date_key) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_gold_fact_payments_claim_id          ON gold.fact_payments(claim_id);
CREATE INDEX IF NOT EXISTS idx_gold_fact_payments_payment_date_key  ON gold.fact_payments(payment_date_key);
