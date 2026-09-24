-- Administrator-only. Raises the ceiling for one month's development supplement from $20
-- to $40 (a $50 development month), approved by the user on 2026-09-24. Supplements stay
-- dated, development-only and administrator-inserted; production still has none.
BEGIN;
ALTER TABLE brain_ops.monthly_allowances DROP CONSTRAINT monthly_allowances_extra_nusd_check;
ALTER TABLE brain_ops.monthly_allowances ADD CONSTRAINT monthly_allowances_extra_nusd_check
    CHECK (extra_nusd > 0 AND extra_nusd <= 40000000000);
COMMIT;
