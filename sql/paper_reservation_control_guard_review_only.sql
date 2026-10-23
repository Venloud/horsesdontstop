-- REVIEW ONLY, DO NOT APPLY TO PRODUCTION WITHOUT SEPARATE OWNER APPROVAL.
-- Preserve the order-authorization row lock without granting the executor
-- UPDATE privileges on public.paper_control. Nothing here enables trading.
-- Apply as a trusted database owner ONLY after independently reviewing
-- SECURITY DEFINER ownership, row security, function grants and search_path.
--
-- INSTALLATION IS A MANUAL, SEPARATE NEON MIGRATION GATE.
-- Function defaults to EXECUTE for PUBLIC, so CREATE and REVOKE MUST
-- be within the same transaction. Never expose a function granting writes.
BEGIN;

CREATE FUNCTION public.paper_reservation_control_guard()
RETURNS TABLE(kill_switch_active boolean, submissions_enabled boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $guard$
BEGIN
    -- Row lock is held by the *caller transaction* until commit/rollback.
    -- UPDATE to either control flag by any session blocks until we finish
    -- durable intent reservation. Never omit this FOR SHARE.
    RETURN QUERY
        SELECT c.kill_switch_active, c.submissions_enabled
        FROM public.paper_control AS c
        WHERE c.singleton = TRUE
        FOR SHARE;
END;
$guard$;

REVOKE ALL ON FUNCTION public.paper_reservation_control_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_reservation_control_guard()
    TO hds_paper_executor;

COMMIT;

-- Post-migration read-only audit as restricted executor:
-- SELECT has_function_privilege(current_user,
--    'public.paper_reservation_control_guard()', 'EXECUTE');
-- SELECT has_table_privilege(current_user,'public.paper_control','UPDATE');
-- Keep executor UPDATE = false. Full reservation is still blocked by
-- production kill_switch_active = true / submissions_enabled = false.
