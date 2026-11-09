-- PAPER reservation lifecycle: audited expiry of an unapproved proposal.
-- Run ONCE as neondb_owner on the production branch (Neon SQL editor).
--
-- Problem: a prepared GTC OTO proposal is a durable `reserved` intent. The only
-- exits were dispatch (owner approval + release) or broker reconciliation, so
-- an unapproved proposal blocked every later executor run, the reserve function
-- and the independent reconciler forever.
--
-- Fix: a new terminal status `expired`, reachable ONLY from `reserved`, ONLY
-- through this SECURITY DEFINER function, ONLY for the independent reconciler
-- role, and ONLY when ALL of these hold under the same exclusive-intent
-- advisory lock that serializes reservation and dispatch commit:
--   * the caller attests a definite broker 404 for the client order ID
--     (the Python caller performs the GET-only lookup first),
--   * no dispatch receipt exists (the order was never released to the broker),
--   * no owner approval was ever consumed, and no approval is still live,
--   * the reservation is at least 10 minutes old OR belongs to an earlier
--     New York trading day (owner approval and dispatch both require the
--     reservation's trading day to be today, so it can never be used again).
-- Every expiry writes an inseparable paper_audit_events row.
-- Expiry never sends, cancels or changes broker orders and never toggles
-- controls; it can only REMOVE a blocker, never authorize anything.
BEGIN;

ALTER TABLE public.paper_intents DROP CONSTRAINT paper_intents_status_check;
ALTER TABLE public.paper_intents ADD CONSTRAINT paper_intents_status_check
    CHECK (status IN ('reserved','submitted','accepted','filled',
                      'rejected','canceled','unknown','expired'));

-- The definer needs to read approval/dispatch evidence; it already holds
-- UPDATE(status, updated_at) on intents and INSERT on audit events.
GRANT SELECT ON TABLE public.paper_pilot_approvals TO hds_paper_status_owner;
GRANT SELECT ON TABLE public.paper_pilot_dispatch_receipts TO hds_paper_status_owner;

CREATE OR REPLACE FUNCTION public.paper_expire_unapproved_reservation(
    p_client_order_id text, p_broker_order_absent boolean)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $expire$
DECLARE
    v_status text;
    v_day date;
    v_created timestamptz;
    v_now timestamptz := clock_timestamp();
    v_today date := (clock_timestamp() AT TIME ZONE 'America/New_York')::date;
BEGIN
    IF p_client_order_id IS NULL OR length(p_client_order_id) = 0 THEN
        RAISE EXCEPTION 'Missing PAPER client order ID' USING ERRCODE = '22023';
    END IF;
    IF p_broker_order_absent IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'Expiry requires a definite broker not-found observation'
            USING ERRCODE = '42501';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('horsesdontstop-paper-exclusive-intent'));
    SELECT i.status, i.trading_day, i.created_at INTO v_status, v_day, v_created
      FROM public.paper_intents AS i
     WHERE i.client_order_id = p_client_order_id FOR UPDATE;
    IF NOT FOUND OR v_status IS DISTINCT FROM 'reserved' THEN
        RAISE EXCEPTION 'Only a reserved PAPER intent can expire' USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM public.paper_pilot_dispatch_receipts
                WHERE client_order_id = p_client_order_id) THEN
        RAISE EXCEPTION 'Dispatched PAPER intent requires broker reconciliation'
            USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM public.paper_pilot_approvals
                WHERE client_order_id = p_client_order_id
                  AND consumed_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Consumed PAPER approval requires broker reconciliation'
            USING ERRCODE = '42501';
    END IF;
    -- Not yet eligible: leave it reserved, report FALSE, write nothing.
    IF EXISTS (SELECT 1 FROM public.paper_pilot_approvals
                WHERE client_order_id = p_client_order_id
                  AND expires_at > v_now) THEN
        RETURN FALSE;
    END IF;
    IF v_day >= v_today AND v_created > v_now - interval '10 minutes' THEN
        RETURN FALSE;
    END IF;
    UPDATE public.paper_intents
       SET status = 'expired', updated_at = pg_catalog.now()
     WHERE client_order_id = p_client_order_id AND status = 'reserved';
    INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
    VALUES (p_client_order_id, 'reservation_expired_unapproved',
            'Unapproved reservation expired after broker 404; no dispatch receipt; '
            || 'trading_day=' || v_day::text || '; reserved_at=' || v_created::text);
    RETURN TRUE;
END;
$expire$;

-- New owner needs CREATE on the schema only for the ownership transfer.
GRANT CREATE ON SCHEMA public TO hds_paper_status_owner;
ALTER FUNCTION public.paper_expire_unapproved_reservation(text, boolean)
    OWNER TO hds_paper_status_owner;
REVOKE CREATE ON SCHEMA public FROM hds_paper_status_owner;
REVOKE ALL ON FUNCTION public.paper_expire_unapproved_reservation(text, boolean)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_expire_unapproved_reservation(text, boolean)
    TO hds_paper_reconciler;

COMMIT;

-- Verification (read-only):
-- SELECT p.oid::regprocedure, pg_get_userbyid(p.proowner), p.prosecdef, p.proacl
--   FROM pg_proc p WHERE p.proname = 'paper_expire_unapproved_reservation';
