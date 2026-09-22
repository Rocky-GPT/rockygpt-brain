-- Add routing without changing balances, RLS, or existing operations.
BEGIN;
ALTER TABLE brain_ops.operations DROP CONSTRAINT operations_category_check;
ALTER TABLE brain_ops.operations ADD CONSTRAINT operations_category_check
    CHECK (category IN ('draft', 'review', 'routing'));
COMMIT;
