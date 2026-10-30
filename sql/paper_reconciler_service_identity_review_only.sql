-- REVIEW ONLY. Separate owner approval required on an identified test branch.
-- Never provision passwords or production identities automatically.
-- Run AFTER PR41 PREPARE. No membership is granted to the executor.
BEGIN;
CREATE ROLE hds_paper_reconciler_service LOGIN INHERIT
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- INHERIT applies only to the narrowly scoped NOLOGIN capability below.
-- The service authenticates as itself; it must never SET ROLE from owner.
GRANT hds_paper_reconciler TO hds_paper_reconciler_service;
GRANT CONNECT ON DATABASE neondb TO hds_paper_reconciler_service;
GRANT USAGE ON SCHEMA public TO hds_paper_reconciler_service;
COMMIT;

-- This intentionally sets NO password. The owner must provision and store
-- a distinct random secret through the trusted private credential channel.
-- No broker key, executor secret or owner DSN may be reused as this login.
-- Verify session_user=current_user=hds_paper_reconciler_service and actual
-- effective SELECT + reconciliation function EXECUTE only before rollout.
