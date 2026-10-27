-- REVIEW ONLY, NOT INSTALLED OR APPROVED FOR PRODUCTION.
-- Phase 5.5: stage a separate journal state-write trust boundary.
-- This PREPARE script MUST NOT be run as part of PR #38, and does NOT
-- revoke the legacy executor writes. A later explicit CUTOVER is needed.
-- Read the design and migrations before any isolated Neon rehearsal.
--
-- No credentials are provisioned for hds_paper_reconciler by this script.
-- In particular, do NOT grant this role to hds_paper_executor.
-- A reconciler that simply trusts caller-supplied broker status or ID
-- is insecure: verify the actual PAPER broker state outside PostgreSQL.
BEGIN;

CREATE ROLE hds_paper_status_owner NOLOGIN NOINHERIT;
CREATE ROLE hds_paper_reconciler NOLOGIN NOINHERIT;

GRANT USAGE, CREATE ON SCHEMA public TO hds_paper_status_owner;
GRANT USAGE ON SCHEMA public TO hds_paper_reconciler;
GRANT SELECT, UPDATE (status, broker_order_id, updated_at)
    ON TABLE public.paper_intents TO hds_paper_status_owner;
GRANT INSERT ON TABLE public.paper_audit_events TO hds_paper_status_owner;
GRANT USAGE ON SEQUENCE public.paper_audit_events_audit_id_seq
    TO hds_paper_status_owner;
-- Neon owner requires membership to transfer function ownership.
GRANT hds_paper_status_owner TO neondb_owner;

-- An executor reporting an ambiguous broker POST may ONLY move an
-- existing reserved intent to unknown, with an inseparable audit row.
-- This grants NO ability to mark filled, accepted, submitted or safe.
CREATE FUNCTION public.paper_mark_reserved_intent_unknown(p_client_order_id text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $mark_unknown$
DECLARE
    v_current text;
BEGIN
    IF p_client_order_id IS NULL OR length(p_client_order_id) = 0 THEN
        RAISE EXCEPTION 'Missing PAPER client order ID' USING ERRCODE = '22023';
    END IF;
    SELECT i.status INTO v_current
      FROM public.paper_intents AS i
     WHERE i.client_order_id = p_client_order_id FOR UPDATE;
    IF NOT FOUND OR v_current IS DISTINCT FROM 'reserved' THEN
        RAISE EXCEPTION 'PAPER unknown transition is not permitted'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.paper_intents
       SET status = 'unknown', updated_at = pg_catalog.now()
     WHERE client_order_id = p_client_order_id;
    INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
    VALUES(p_client_order_id, 'submission_outcome_unknown',
           'Broker response unavailable; no blind retry');
    RETURN true;
END;
$mark_unknown$;

ALTER FUNCTION public.paper_mark_reserved_intent_unknown(text)
    OWNER TO hds_paper_status_owner;
REVOKE ALL ON FUNCTION public.paper_mark_reserved_intent_unknown(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_mark_reserved_intent_unknown(text)
    TO hds_paper_executor;

-- Broker-reconciled journal transitions belong to an independent
-- credential and service, NOT to the order-reserving executor.
-- This function checks state-machine integrity and writes an atomic
-- audit record. It CANNOT by itself prove a response came from Alpaca.
CREATE FUNCTION public.paper_record_reconciled_intent_status(
    p_client_order_id text, p_expected_status text, p_new_status text,
    p_broker_order_id text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $reconcile$
DECLARE
    v_current text;
    v_existing_broker_id text;
    v_permitted boolean;
BEGIN
    IF p_client_order_id IS NULL OR length(p_client_order_id) = 0
       OR p_expected_status IS NULL OR p_new_status IS NULL THEN
        RAISE EXCEPTION 'Missing reconciled PAPER status parameters'
            USING ERRCODE = '22023';
    END IF;
    SELECT i.status, i.broker_order_id
      INTO v_current, v_existing_broker_id
      FROM public.paper_intents AS i
     WHERE i.client_order_id = p_client_order_id FOR UPDATE;
    IF NOT FOUND OR v_current IS DISTINCT FROM p_expected_status THEN
        RAISE EXCEPTION 'Stale or absent PAPER intent for reconciliation'
            USING ERRCODE = '42501';
    END IF;

    v_permitted := CASE v_current
        WHEN 'reserved' THEN p_new_status IN ('submitted','unknown','rejected')
        WHEN 'submitted' THEN p_new_status IN ('accepted','filled','unknown','rejected','canceled')
        WHEN 'accepted' THEN p_new_status IN ('filled','unknown','rejected','canceled')
        WHEN 'unknown' THEN p_new_status IN ('submitted','accepted','filled','rejected','canceled')
        ELSE FALSE
    END;
    IF v_permitted IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'PAPER order state transition not allowed'
            USING ERRCODE = '42501';
    END IF;

    -- Existing broker ID cannot be switched to a different order.
    -- A real PAPER reconciler must verify the ID, account, symbol,
    -- side, shares, and all protective stop legs via broker GET.
    IF v_existing_broker_id IS NOT NULL
       AND p_broker_order_id IS NOT NULL
       AND v_existing_broker_id IS DISTINCT FROM p_broker_order_id THEN
        RAISE EXCEPTION 'Reconciled broker order identity changed'
            USING ERRCODE = '42501';
    END IF;
    IF p_new_status IN ('submitted','accepted','filled')
       AND (pg_catalog.coalesce(p_broker_order_id,v_existing_broker_id) IS NULL
            OR pg_catalog.length(pg_catalog.btrim(
                pg_catalog.coalesce(p_broker_order_id,v_existing_broker_id))) = 0) THEN
        RAISE EXCEPTION 'Reconciled PAPER broker identity missing'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.paper_intents
       SET status = p_new_status,
           broker_order_id = pg_catalog.coalesce(p_broker_order_id,broker_order_id),
           updated_at = pg_catalog.now()
     WHERE client_order_id = p_client_order_id;
    INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
    VALUES(p_client_order_id, 'status_reconciled', p_new_status);
    RETURN true;
END;
$reconcile$;

ALTER FUNCTION public.paper_record_reconciled_intent_status(text,text,text,text)
    OWNER TO hds_paper_status_owner;
REVOKE ALL ON FUNCTION public.paper_record_reconciled_intent_status(text,text,text,text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_record_reconciled_intent_status(text,text,text,text)
    TO hds_paper_reconciler;

-- No remaining CREATE permission for SECURITY DEFINER's owner.
REVOKE CREATE ON SCHEMA public FROM hds_paper_status_owner;
COMMIT;

-- A subsequent owner-authorized migration must:
-- (1) deploy and prove the trusted, independent read-only broker reconciler,
-- (2) refactor executor mark_uncertain to use paper_mark_reserved_intent_unknown,
-- (3) refactor reconciler status recording to this protected function,
-- (4) only THEN revoke executor's direct UPDATE and audit INSERT rights.
-- No existing production broker order path is released by this file.
