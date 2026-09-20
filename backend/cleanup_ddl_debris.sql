-- ============================================================
-- File 7: silver.silver_customer_engagement_features
-- 5 drops. Indexes had no duplication; only FK and two CHECK
-- groups (avg_time_to_completion, view_to_completion_rate) did.
-- Postgres treats "X >= 0" and "X IS NULL OR X >= 0" as identical
-- (a NULL result in a CHECK always passes), so these really are
-- the same rule three times over, not just similar rules.
-- ============================================================

ALTER TABLE silver.silver_customer_engagement_features
    DROP CONSTRAINT IF EXISTS fk_silver_customer_engagement_features_customer_id;
    -- keeping: fk_scef_customer_id

ALTER TABLE silver.silver_customer_engagement_features
    DROP CONSTRAINT IF EXISTS chk_scef_avg_time_nonneg;
ALTER TABLE silver.silver_customer_engagement_features
    DROP CONSTRAINT IF EXISTS chk_scef_avg_time_to_completion_nonneg;
    -- keeping: chk_scef_avg_time_to_completion

ALTER TABLE silver.silver_customer_engagement_features
    DROP CONSTRAINT IF EXISTS chk_scef_rate_range;
ALTER TABLE silver.silver_customer_engagement_features
    DROP CONSTRAINT IF EXISTS chk_scef_view_to_completion_rate;
    -- keeping: chk_scef_view_to_completion_rate_nonneg


-- ============================================================
-- File 8: silver.silver_offer_response_features
-- This one accumulated far more (33 objects -> 13), matching how
-- many more rounds it went through. Indexes: two columns each got
-- indexed 4 times under 4 different naming conventions.
-- ============================================================

DROP INDEX IF EXISTS silver.idx_silver_offer_response_features_completion_rate;
DROP INDEX IF EXISTS silver.ix_offer_response_features_completion_rate;
DROP INDEX IF EXISTS silver.ix_sorf_completion_rate;
-- keeping: idx_offer_response_features_completion_rate

DROP INDEX IF EXISTS silver.idx_silver_offer_response_features_offer_type;
DROP INDEX IF EXISTS silver.ix_offer_response_features_offer_type;
DROP INDEX IF EXISTS silver.ix_sorf_offer_type;
-- keeping: idx_offer_response_features_offer_type

ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS fk_silver_offer_response_features_offer;
ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS fk_silver_offer_response_features_offer_id;
    -- keeping: fk_offer_response_features_offer_id

ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_silver_offer_response_features_avg_reward_paid_nonneg;
    -- keeping: chk_offer_response_features_avg_reward_nonneg

ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_silver_offer_response_features_completed_nonneg;
    -- keeping: chk_offer_response_features_completed_nonneg

ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_silver_offer_response_features_received_nonneg;
    -- keeping: chk_offer_response_features_received_nonneg

ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_silver_offer_response_features_viewed_nonneg;
    -- keeping: chk_offer_response_features_viewed_nonneg

-- Combined received/viewed/completed checks: all three are pure
-- duplicates of the four single-column checks above EXCEPT one -
-- chk_silver_offer_response_features_counts also adds
-- "offers_viewed <= offers_received AND offers_completed <=
-- offers_received", which is NOT covered anywhere else. That
-- specific invariant was verified true against real data during
-- file 8's review, but was never part of any actually-approved
-- draft - it's a leftover from a rejected attempt, not a missed
-- requirement. Dropping all three below on the assumption you want
-- clean rather than defensive; if you'd rather keep that one extra
-- safety net, skip the middle DROP line.
ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_offer_response_features_counts_nonneg;
ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_silver_offer_response_features_counts;
ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_sorf_nonneg_counts;

ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_silver_offer_response_features_completion_rate_range;
ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_sorf_completion_rate_range;
    -- keeping: chk_offer_response_features_completion_rate_range

-- Combined completion_rate+view_rate checks: pure duplication of
-- the two separate range checks (completion_rate range +
-- view_rate range) already kept below - enforcing both together
-- guarantees nothing beyond enforcing each independently.
ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_silver_offer_response_features_rates;
ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_offer_response_rates_valid;
ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_offer_response_features_rates_range;

ALTER TABLE silver.silver_offer_response_features
    DROP CONSTRAINT IF EXISTS chk_silver_offer_response_features_view_rate_range;
    -- keeping: chk_offer_response_features_view_rate_range

-- Not touched: chk_offer_response_features_difficulty_nonneg,
-- _reward_nonneg, _duration_nonneg. These aren't duplicates of
-- anything - they're a separate, deliberate redundancy with
-- silver_offers's own constraints on the same columns (harmless,
-- not what "debris" means here), so left alone.
