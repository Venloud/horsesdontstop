-- REVIEW ONLY. Separate owner approval required; never auto-install.
-- AFTER PR38 reservation, PR41 PREPARE and executor CUTOVER.
-- Does not change controls, credentials, or release broker submissions.
BEGIN;
-- The consumption receipt commits on a separate connection BEFORE POST.
-- Its uniqueness survives rollback/loss of the transaction holding controls.
CREATE UNIQUE INDEX uq_paper_submission_authorization_consumed
    ON public.paper_audit_events(client_order_id)
    WHERE kind='submission_authorization_consumed';
GRANT SELECT ON TABLE public.paper_audit_events
    TO hds_paper_status_owner, hds_paper_reservation_guard;
GRANT CREATE ON SCHEMA public TO hds_paper_reservation_guard;
CREATE FUNCTION public.paper_authorize_unknown_submission(
    p_client_order_id text, p_strategy text, p_symbol text,
    p_trading_day date, p_whole_shares integer,
    p_expected_entry numeric, p_protective_stop numeric)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $authorize$
DECLARE v_kill boolean; v_enabled boolean;
BEGIN
    -- Held by the caller transaction THROUGH the single broker attempt.
    -- A concurrent control UPDATE serializes before or after this boundary.
    PERFORM pg_advisory_xact_lock(hashtext('horsesdontstop-paper-exclusive-intent'));
    SELECT kill_switch_active, submissions_enabled INTO v_kill,v_enabled
      FROM public.paper_reservation_control_guard();
    IF NOT FOUND OR v_kill IS DISTINCT FROM FALSE OR v_enabled IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'PAPER final submission blocked by controls' USING ERRCODE='42501';
    END IF;
    -- Python must FIRST commit reserved->unknown through the narrow API.
    -- It cannot reuse this reservation after a crash, failure or restart.
    IF NOT EXISTS (
        SELECT 1 FROM public.paper_intents i
        WHERE i.client_order_id=p_client_order_id AND i.status='unknown'
          AND i.broker_order_id IS NULL
          AND i.strategy=p_strategy AND i.symbol=p_symbol
          AND i.trading_day=p_trading_day AND i.whole_shares=p_whole_shares
          AND i.expected_entry=p_expected_entry AND i.protective_stop=p_protective_stop
          AND i.trading_day=(clock_timestamp() AT TIME ZONE 'America/New_York')::date
          AND i.updated_at BETWEEN clock_timestamp()-interval '5 seconds' AND clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER final reserved terms unavailable or stale' USING ERRCODE='42501';
    END IF;
    IF EXISTS (SELECT 1 FROM public.paper_audit_events
               WHERE client_order_id=p_client_order_id
                 AND kind='submission_authorization_consumed') THEN
        RAISE EXCEPTION 'PAPER submission authorization already consumed' USING ERRCODE='42501';
    END IF;
    RETURN TRUE;
END;
$authorize$;
ALTER FUNCTION public.paper_authorize_unknown_submission(text,text,text,date,integer,numeric,numeric)
    OWNER TO hds_paper_reservation_guard;
REVOKE CREATE ON SCHEMA public FROM hds_paper_reservation_guard;
REVOKE ALL ON FUNCTION public.paper_authorize_unknown_submission(text,text,text,date,integer,numeric,numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_authorize_unknown_submission(text,text,text,date,integer,numeric,numeric)
    TO hds_paper_executor;
GRANT CREATE ON SCHEMA public TO hds_paper_status_owner;
CREATE FUNCTION public.paper_consume_submission_authorization(p_client_order_id text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $consume$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.paper_intents
                   WHERE client_order_id=p_client_order_id AND status='unknown'
                     AND broker_order_id IS NULL
                     AND updated_at BETWEEN clock_timestamp()-interval '5 seconds' AND clock_timestamp()) THEN
        RAISE EXCEPTION 'PAPER submission claim is absent or stale' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.paper_audit_events(client_order_id,kind,summary)
    VALUES(p_client_order_id,'submission_authorization_consumed',
           'One attempt consumed before broker POST; never retry');
    RETURN TRUE;
END;
$consume$;
ALTER FUNCTION public.paper_consume_submission_authorization(text)
    OWNER TO hds_paper_status_owner;
REVOKE CREATE ON SCHEMA public FROM hds_paper_status_owner;
REVOKE ALL ON FUNCTION public.paper_consume_submission_authorization(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_consume_submission_authorization(text)
    TO hds_paper_executor;
COMMIT;

-- PostgreSQL and Alpaca cannot share an atomic transaction. Kill changes
-- committed BEFORE the lock must block POST; changes queued AFTER it wait
-- for the attempt. Broker timeout remains UNKNOWN, never retry. A DB lock
-- is not an emergency cancellation facility. Bound SDK network timeouts
-- and supervise in a separately approved runtime before release.
