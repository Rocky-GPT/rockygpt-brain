-- Apply once as an administrator, never with the campus retrieval role.
BEGIN;
CREATE SCHEMA brain_ops;
REVOKE ALL ON SCHEMA brain_ops FROM PUBLIC;
CREATE ROLE brain_development NOLOGIN;
CREATE ROLE brain_production NOLOGIN;

CREATE TABLE brain_ops.accounts (
    environment text PRIMARY KEY CHECK (environment IN ('development', 'production')),
    cap_nusd bigint NOT NULL CHECK (cap_nusd > 0 AND cap_nusd <= 10000000000),
    paused boolean NOT NULL DEFAULT false
);
INSERT INTO brain_ops.accounts (environment, cap_nusd)
VALUES ('development', 10000000000), ('production', 10000000000);

CREATE TABLE brain_ops.operations (
    environment text NOT NULL REFERENCES brain_ops.accounts,
    operation_id uuid NOT NULL,
    request_id text NOT NULL,
    category text NOT NULL CHECK (category IN ('draft', 'review')),
    admitted_month date NOT NULL,
    charged_month date,
    reserved_nusd bigint NOT NULL CHECK (reserved_nusd > 0),
    cost_nusd bigint CHECK (cost_nusd >= 0),
    state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved', 'uncertain', 'settled')),
    metadata jsonb NOT NULL,
    usage jsonb,
    error_code text,
    provider_response_id text,
    returned_model text,
    elapsed_ms integer,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (environment, operation_id),
    CHECK ((state = 'settled') = (cost_nusd IS NOT NULL AND charged_month IS NOT NULL))
);
CREATE INDEX operations_month ON brain_ops.operations(environment, charged_month);
CREATE INDEX operations_unsettled ON brain_ops.operations(environment) WHERE state <> 'settled';

CREATE TABLE brain_ops.turns (
    environment text NOT NULL REFERENCES brain_ops.accounts,
    request_id text NOT NULL,
    summary jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (environment, request_id)
);
ALTER TABLE brain_ops.turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain_ops.turns FORCE ROW LEVEL SECURITY;
CREATE POLICY turn_environment ON brain_ops.turns
    USING (current_user = 'brain_' || environment)
    WITH CHECK (current_user = 'brain_' || environment);

ALTER TABLE brain_ops.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain_ops.accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE brain_ops.operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain_ops.operations FORCE ROW LEVEL SECURITY;
CREATE POLICY account_environment ON brain_ops.accounts
    USING (current_user = 'brain_' || environment)
    WITH CHECK (current_user = 'brain_' || environment);
CREATE POLICY operation_environment ON brain_ops.operations
    USING (current_user = 'brain_' || environment)
    WITH CHECK (current_user = 'brain_' || environment);

GRANT USAGE ON SCHEMA brain_ops TO brain_development, brain_production;
GRANT SELECT, UPDATE(paused) ON brain_ops.accounts TO brain_development, brain_production;
GRANT SELECT, INSERT, UPDATE ON brain_ops.operations TO brain_development, brain_production;
GRANT SELECT, INSERT, UPDATE ON brain_ops.turns TO brain_development, brain_production;
COMMIT;
-- Provision distinct login roles/credentials per environment outside this migration.
-- Grant each login ONLY its matching role above. The gateway SET ROLE is checked
-- by PostgreSQL, so a development login cannot select the production balance.
-- Neither role receives access to campus data or the ability to change budget caps.
