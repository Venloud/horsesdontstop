-- REVIEW ONLY. Install after isolated branch acceptance and independent review.
-- Removes the fixed $5 notional ceiling while retaining exact risk-sized order
-- terms, available-cash checks in the executor, the $0.50 fee reserve, the
-- owner-only approval boundary, and single-use dispatch receipts.
BEGIN;

DO $controls$
DECLARE v_kill boolean; v_enabled boolean;
BEGIN
    SELECT kill_switch_active,submissions_enabled INTO v_kill,v_enabled
      FROM public.paper_reservation_control_guard();
    IF NOT FOUND OR v_kill IS DISTINCT FROM TRUE OR v_enabled IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'Dynamic sizing migration requires kill ON and submissions OFF'
            USING ERRCODE='42501';
    END IF;
END;
$controls$;

ALTER TABLE public.paper_pilot_approvals
    DROP CONSTRAINT IF EXISTS paper_pilot_approvals_purchase_cost_check;
ALTER TABLE public.paper_pilot_approvals
    ADD CONSTRAINT paper_pilot_approvals_purchase_cost_check
        CHECK (purchase_cost > 0);

CREATE OR REPLACE FUNCTION public.paper_reserve_bounded_pilot_intent(
    p_client_order_id text, p_strategy text, p_symbol text,
    p_trading_day date, p_whole_shares integer,
    p_expected_entry numeric, p_protective_stop numeric)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $reserve$
DECLARE v_kill boolean; v_enabled boolean; v_id text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('horsesdontstop-paper-exclusive-intent'));
    SELECT kill_switch_active,submissions_enabled INTO v_kill,v_enabled
      FROM public.paper_reservation_control_guard();
    IF NOT FOUND OR v_kill IS DISTINCT FROM TRUE OR v_enabled IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'PAPER preparation requires kill switch ON and submissions OFF' USING ERRCODE='42501';
    END IF;
    IF p_client_order_id IS NULL
       OR p_strategy IS DISTINCT FROM 'momentum_volume'
       OR p_symbol IS NULL OR p_symbol !~ '^[A-Z]{1,5}$'
       OR p_trading_day IS DISTINCT FROM (clock_timestamp() AT TIME ZONE 'America/New_York')::date
       OR p_whole_shares IS NULL OR p_whole_shares < 1
       OR p_expected_entry IS NULL OR p_expected_entry <= 0
       OR p_expected_entry::text IN ('NaN','Infinity','-Infinity')
       OR p_protective_stop IS NULL OR p_protective_stop <= 0
       OR p_protective_stop::text IN ('NaN','Infinity','-Infinity')
       OR p_protective_stop >= p_expected_entry
       OR p_client_order_id !~ ('^hds-'||to_char(p_trading_day,'YYYYMMDD')||'-'||p_symbol||'-[0-9a-f]{12}$') THEN
        RAISE EXCEPTION 'Invalid risk-limited GTC OTO intent' USING ERRCODE='22023';
    END IF;
    IF EXISTS (SELECT 1 FROM public.paper_intents
               WHERE status IN ('reserved','submitted','accepted','unknown')) THEN
        RAISE EXCEPTION 'Earlier PAPER order requires reconciliation' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.paper_intents
       (client_order_id,strategy,symbol,trading_day,whole_shares,
        expected_entry,protective_stop,time_in_force)
    VALUES (p_client_order_id,p_strategy,p_symbol,p_trading_day,p_whole_shares,
        p_expected_entry,p_protective_stop,'gtc')
    ON CONFLICT DO NOTHING RETURNING client_order_id INTO v_id;
    RETURN v_id IS NOT NULL;
END;
$reserve$;
ALTER FUNCTION public.paper_reserve_bounded_pilot_intent(text,text,text,date,integer,numeric,numeric)
    OWNER TO hds_paper_reservation_guard;
REVOKE ALL ON FUNCTION public.paper_reserve_bounded_pilot_intent(text,text,text,date,integer,numeric,numeric)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_reserve_bounded_pilot_intent(text,text,text,date,integer,numeric,numeric)
    TO hds_paper_executor;

CREATE OR REPLACE FUNCTION public.paper_approve_bounded_pilot(
    p_client_order_id text, p_symbol text, p_whole_shares integer,
    p_limit_price numeric, p_stop_price numeric, p_request_sha256 text,
    p_account_id_sha256 text, p_expires_at timestamptz)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $approve$
DECLARE v_cost numeric;
BEGIN
    IF p_client_order_id IS NULL OR p_symbol IS NULL
       OR p_symbol !~ '^[A-Z]{1,5}$'
       OR p_whole_shares IS NULL OR p_whole_shares < 1
       OR p_limit_price IS NULL OR p_limit_price <= 0
       OR p_limit_price::text IN ('NaN','Infinity','-Infinity')
       OR p_stop_price IS NULL OR p_stop_price <= 0
       OR p_stop_price::text IN ('NaN','Infinity','-Infinity')
       OR p_stop_price >= p_limit_price
       OR p_request_sha256 IS NULL OR p_request_sha256 !~ '^[0-9a-f]{64}$'
       OR p_account_id_sha256 IS NULL OR p_account_id_sha256 !~ '^[0-9a-f]{64}$'
       OR p_expires_at IS NULL
       OR p_expires_at <= clock_timestamp()
       OR p_expires_at > clock_timestamp() + interval '120 seconds' THEN
        RAISE EXCEPTION 'Invalid risk-limited PAPER approval' USING ERRCODE='22023';
    END IF;
    v_cost := p_limit_price * p_whole_shares;
    IF v_cost <= 0 OR v_cost::text IN ('NaN','Infinity','-Infinity') THEN
        RAISE EXCEPTION 'Invalid exact PAPER purchase cost' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.paper_intents i
        WHERE i.client_order_id=p_client_order_id AND i.status='reserved'
          AND i.symbol=p_symbol AND i.whole_shares=p_whole_shares
          AND i.expected_entry=p_limit_price AND i.protective_stop=p_stop_price
          AND i.time_in_force='gtc'
          AND i.trading_day=(clock_timestamp() AT TIME ZONE 'America/New_York')::date
    ) THEN
        RAISE EXCEPTION 'No matching reserved GTC OTO intent' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.paper_pilot_approvals
      (client_order_id,symbol,whole_shares,limit_price,stop_price,purchase_cost,
       fee_reserve,request_sha256,account_id_sha256,approved_at,expires_at)
    VALUES(p_client_order_id,p_symbol,p_whole_shares,p_limit_price,p_stop_price,
       v_cost,0.50,p_request_sha256,p_account_id_sha256,clock_timestamp(),p_expires_at);
    RETURN TRUE;
END;
$approve$;
ALTER FUNCTION public.paper_approve_bounded_pilot(text,text,integer,numeric,numeric,text,text,timestamptz)
    OWNER TO neondb_owner;
REVOKE ALL ON FUNCTION public.paper_approve_bounded_pilot(text,text,integer,numeric,numeric,text,text,timestamptz)
    FROM PUBLIC, hds_paper_executor, hds_paper_reconciler,
         hds_paper_heartbeat, hds_paper_win_monitor;

CREATE OR REPLACE FUNCTION public.paper_commit_bounded_pilot_dispatch(
    p_client_order_id text, p_request_sha256 text,
    p_account_id_sha256 text, p_attempt_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $dispatch$
DECLARE v_kill boolean; v_enabled boolean; v_approval public.paper_pilot_approvals%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('horsesdontstop-paper-exclusive-intent'));
    SELECT kill_switch_active, submissions_enabled INTO v_kill,v_enabled
      FROM public.paper_reservation_control_guard();
    IF NOT FOUND OR v_kill IS DISTINCT FROM FALSE OR v_enabled IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'PAPER dispatch blocked by controls' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_approval FROM public.paper_pilot_approvals
      WHERE client_order_id=p_client_order_id FOR UPDATE;
    IF NOT FOUND OR v_approval.consumed_at IS NOT NULL
       OR v_approval.expires_at <= clock_timestamp()
       OR v_approval.request_sha256 IS DISTINCT FROM p_request_sha256
       OR v_approval.account_id_sha256 IS DISTINCT FROM p_account_id_sha256
       OR p_attempt_id IS NULL
       OR v_approval.purchase_cost IS DISTINCT FROM
          (v_approval.limit_price * v_approval.whole_shares)
       OR v_approval.fee_reserve IS DISTINCT FROM 0.50 THEN
        RAISE EXCEPTION 'PAPER approval absent, inconsistent, expired or consumed' USING ERRCODE='42501';
    END IF;
    IF EXISTS (SELECT 1 FROM public.paper_intents
               WHERE client_order_id<>p_client_order_id
                 AND status IN ('reserved','submitted','accepted','unknown')) THEN
        RAISE EXCEPTION 'Earlier PAPER order requires reconciliation' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.paper_intents i
        WHERE i.client_order_id=p_client_order_id AND i.status='reserved'
          AND i.symbol=v_approval.symbol AND i.whole_shares=v_approval.whole_shares
          AND i.expected_entry=v_approval.limit_price
          AND i.protective_stop=v_approval.stop_price AND i.time_in_force='gtc'
          AND i.trading_day=(clock_timestamp() AT TIME ZONE 'America/New_York')::date
    ) THEN
        RAISE EXCEPTION 'Reserved GTC order differs from owner approval' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.paper_pilot_dispatch_receipts
       (client_order_id,attempt_id,request_sha256)
    VALUES(p_client_order_id,p_attempt_id,p_request_sha256);
    UPDATE public.paper_pilot_approvals SET consumed_at=clock_timestamp()
      WHERE client_order_id=p_client_order_id AND consumed_at IS NULL;
    PERFORM public.paper_mark_reserved_intent_unknown(p_client_order_id);
    RETURN TRUE;
END;
$dispatch$;
ALTER FUNCTION public.paper_commit_bounded_pilot_dispatch(text,text,text,uuid)
    OWNER TO hds_paper_reservation_guard;
REVOKE CREATE ON SCHEMA public FROM hds_paper_reservation_guard;
REVOKE ALL ON FUNCTION public.paper_commit_bounded_pilot_dispatch(text,text,text,uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_commit_bounded_pilot_dispatch(text,text,text,uuid)
    TO hds_paper_executor;

-- No table-level/column-level grants are added. Executor, reconciler,
-- heartbeat, and Windows monitor roles remain unable to write approvals.
COMMIT;
