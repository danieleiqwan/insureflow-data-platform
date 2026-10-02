-- InsureFlow Phase 4A Initialization Script
-- Retires the default public schema per ADR-014 (superseding ADR-006 and ADR-013).
-- Medallion architecture layers reside in dedicated schemas:
-- - bronze: raw ingested data (sql/bronze.sql)
-- - silver: typed, validated, and constrained data (sql/silver.sql)
-- - gold:   analytical models (future phase)

DROP SCHEMA IF EXISTS public CASCADE;
