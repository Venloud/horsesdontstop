-- REVIEW ONLY, DO NOT APPLY TO PRODUCTION WITHOUT SEPARATE OWNER APPROVAL.
-- Preserve the order-authorization row lock without granting the executor
-- UPDATE privileges on public.paper_control. Nothing here enables trading.
-- Apply only as the verified neondb_owner on an authorized environment,
-- after reviewing NOLOGIN definer ownership, row security and grants.
-- This creates the NOLOGIN definer role once, and is intentionally not an
-- automatic or repeatable production migration.
--
-- INSTALLATION IS A MANUAL, SEPARATE NEON MIGRATION GATE.
-- Function defaults to EXECUTE for PUBLIC, so CREATE and REVOKE MUST
-- be within the same transaction. Never expose a function granting writes.
BEGIN;

-- Restrict SECURITY DEFINER to a purpose-built, non-login role.
-- Do NOT run the definer as neondb_owner or neon_superuser, whose
-- administrative privileges and BYPASSRLS would be excessive.
-- The guard role needs UPDATE on control solely to obtain FOR SHARE;
-- the executor never inherits this role or receives UPDATE.
CREATE ROLE hds_paper_reservation_guard NOLOGIN NOINHERIT;
GRANT USAGE, CREATE ON SCHEMA public TO hds_paper_reservation_guard;
GRANT SELECT, UPDATE ON TABLE public.paper_control
    TO hds_paper_reservation_guard;
-- The database owner needs membership only to transfer function ownership.
GRANT hds_paper_reservation_guard TO neondb_owner;

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

ALTER FUNCTION public.paper_reservation_control_guard()
    OWNER TO hds_paper_reservation_guard;
-- CREATE permission is needed during the transfer, never after commit.
REVOKE CREATE ON SCHEMA public FROM hds_paper_reservation_guard;

REVOKE ALL ON FUNCTION public.paper_reservation_control_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_reservation_control_guard()
    TO hds_paper_executor;

-- This migration deliberately couples control authorization and intent INSERT.
-- No raw intent INSERT grant survives for the restricted executor.
-- Unique trading-day enforcement is a database constraint, not a Python count.
GRANT SELECT, INSERT ON TABLE public.paper_intents TO hds_paper_reservation_guard;
CREATE UNIQUE INDEX uq_paper_intents_trading_day ON public.paper_intents(trading_day);

-- A short-lived CREATE grant is needed only for ownership transfer.
GRANT CREATE ON SCHEMA public TO hds_paper_reservation_guard;
CREATE FUNCTION public.paper_reserve_intent_if_enabled(p_client_order_id text, p_strategy text, p_symbol text, p_trading_day date, p_whole_shares integer, p_expected_entry numeric, p_protective_stop numeric)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
    v_kill boolean;
    v_enabled boolean;
    v_new_id text;
BEGIN
    -- One account-wide lock for cooperating callers, row lock for switch changes.
    PERFORM pg_advisory_xact_lock(hashtext('horsesdontstop-paper-exclusive-intent'));
    SELECT kill_switch_active, submissions_enabled INTO v_kill, v_enabled
      FROM public.paper_reservation_control_guard();
    IF NOT FOUND OR v_kill IS DISTINCT FROM FALSE OR v_enabled IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'PAPER reservation blocked by control state' USING ERRCODE = '42501';
    END IF;
    IF p_trading_day IS NULL OR p_symbol IS NULL OR p_symbol !~ '^[A-Z]{1,5}

-- Post-migration read-only audit as restricted executor:
-- SELECT has_function_privilege(current_user,
--    'public.paper_reservation_control_guard()', 'EXECUTE');
-- SELECT has_table_privilege(current_user,'public.paper_control','UPDATE');
-- Keep executor UPDATE = false. Full reservation is still blocked by
-- production kill_switch_active = true / submissions_enabled = false.

       OR p_strategy IS DISTINCT FROM 'momentum_volume'
       OR p_whole_shares IS NULL OR p_whole_shares < 1
       OR p_expected_entry IS NULL OR p_expected_entry <= 0
       OR p_protective_stop IS NULL OR p_protective_stop <= 0
       OR p_protective_stop >= p_expected_entry
       OR p_client_order_id IS NULL
       OR p_client_order_id !~ ('^hds-' || to_char(p_trading_day,'YYYYMMDD') || '-'
                               || p_symbol || '-[0-9a-f]{12}

-- Post-migration read-only audit as restricted executor:
-- SELECT has_function_privilege(current_user,
--    'public.paper_reservation_control_guard()', 'EXECUTE');
-- SELECT has_table_privilege(current_user,'public.paper_control','UPDATE');
-- Keep executor UPDATE = false. Full reservation is still blocked by
-- production kill_switch_active = true / submissions_enabled = false.
) THEN
        RAISE EXCEPTION 'Invalid protected PAPER intent' USING ERRCODE = '22023';
    END IF;
    IF EXISTS(SELECT 1 FROM public.paper_intents WHERE trading_day = p_trading_day) THEN
        RAISE EXCEPTION 'Only one protected PAPER intent per trading day' USING ERRCODE = '23505';
    END IF;
    INSERT INTO public.paper_intents
      (client_order_id, strategy, symbol, trading_day, whole_shares,
       expected_entry, protective_stop)
    VALUES(p_client_order_id,p_strategy,p_symbol,p_trading_day,p_whole_shares,
           p_expected_entry,p_protective_stop)
    ON CONFLICT DO NOTHING
    RETURNING client_order_id INTO v_new_id;
    RETURN v_new_id IS NOT NULL;
END;
$function$

ALTER FUNCTION public.paper_reserve_intent_if_enabled(text,text,text,date,integer,numeric,numeric)
    OWNER TO hds_paper_reservation_guard;
REVOKE CREATE ON SCHEMA public FROM hds_paper_reservation_guard;
REVOKE ALL ON FUNCTION public.paper_reserve_intent_if_enabled(text,text,text,date,integer,numeric,numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_reserve_intent_if_enabled(text,text,text,date,integer,numeric,numeric)
    TO hds_paper_executor;

-- The executor keeps its opening-equity INSERT privilege but cannot bypass
-- authorization with direct journal INSERT or direct lock-guard calls.
REVOKE EXECUTE ON FUNCTION public.paper_reservation_control_guard() FROM hds_paper_executor;
REVOKE INSERT ON TABLE public.paper_intents FROM hds_paper_executor;

COMMIT;

-- Post-migration read-only audit as restricted executor:
-- SELECT has_function_privilege(current_user,
--    'public.paper_reservation_control_guard()', 'EXECUTE');
-- SELECT has_table_privilege(current_user,'public.paper_control','UPDATE');
-- Keep executor UPDATE = false. Full reservation is still blocked by
-- production kill_switch_active = true / submissions_enabled = false.

-- IMPORTANT: This review-only migration does not enable broker order routing.
-- The next broker gateway must perform a fresh current-control check AFTER
-- reservation commit. A past reservation is not a durable trading permit.
-- The existing opening-equity collector must accept both the pre-migration
-- and the post-migration direct-intent-INSERT role grants to avoid outage.
