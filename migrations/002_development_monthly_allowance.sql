-- Administrator-only, dated development supplements. No recurring cap increase.
BEGIN;
CREATE TABLE brain_ops.monthly_allowances (
    environment text NOT NULL REFERENCES brain_ops.accounts CHECK (environment = 'development'),
    month date NOT NULL CHECK (extract(day FROM month) = 1),
    extra_nusd bigint NOT NULL CHECK (extra_nusd > 0 AND extra_nusd <= 20000000000),
    approval_note text NOT NULL CHECK (length(approval_note) > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (environment, month)
);
ALTER TABLE brain_ops.monthly_allowances ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain_ops.monthly_allowances FORCE ROW LEVEL SECURITY;
CREATE POLICY allowance_environment ON brain_ops.monthly_allowances
    USING (current_user = 'brain_' || environment);
GRANT SELECT ON brain_ops.monthly_allowances TO brain_development, brain_production;
COMMIT;
-- Only an administrator may insert an explicitly approved month/amount.
-- The application roles cannot create, update, delete or roll forward an allowance.
