-- RELEASE STEP, NOT A MIGRATION. Owner applies only after migration 003 is
-- verified and the release commit is deployed. Turns on policy-authorized
-- PAPER entries; trading still requires the operator to ARM the kill switch.
BEGIN;
UPDATE public.paper_trading_policy
   SET autonomous_entries_enabled = TRUE, updated_at = clock_timestamp()
 WHERE singleton;
INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
VALUES (NULL, 'policy_changed', 'autonomous_entries_enabled=TRUE by owner release step');
COMMIT;

-- Operator login (run once; choose the password yourself, keep it in your
-- password manager, never on the trading host). Created via SQL so it does
-- not inherit neon_superuser:
-- CREATE ROLE hds_paper_operator_login LOGIN INHERIT NOSUPERUSER NOCREATEDB
--     NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '<your password>';
-- GRANT hds_paper_operator TO hds_paper_operator_login;
