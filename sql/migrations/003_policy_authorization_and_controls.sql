-- Autonomous PAPER trading: policy-based authorization + restricted controls.
-- Applied by the privileged deployment workflow (neondb_owner, interactive).
-- Grants NO owner/table-write privilege to any login used by the bot.
--
-- 1. paper_trading_policy: owner-maintained limits, enforced inside Neon,
--    independent of the Python executor.
-- 2. paper_policy_authorize_entry(): replaces the per-trade owner approval.
--    Under the exclusive-intent lock it requires ARMED controls, an enabled
--    policy, today's verified opening-equity snapshot, per-trade risk and
--    notional within policy, the daily entry cap, no open intent and a
--    regular-session time window; then atomically writes the reserved GTC
--    intent, a single-use 120-second approval and an audit event. The
--    existing paper_commit_bounded_pilot_dispatch() still consumes the
--    approval once, re-checks controls and marks the intent UNKNOWN pre-POST.
-- 3. paper_engage_kill_switch(): PAUSE (kill ON, submissions OFF) + audit.
--    Callable by executor, reconciler and operator: anything may stop.
-- 4. paper_arm_autonomous_trading(): ARM (kill OFF, submissions ON) + audit.
--    Callable ONLY by hds_paper_operator (human-held operator login).
--    The operator login password is never stored on the trading host;
--    set-paper-trading.ps1 -Arm prompts for it each time.
BEGIN;

CREATE TABLE IF NOT EXISTS public.paper_trading_policy (
    singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    autonomous_entries_enabled boolean NOT NULL DEFAULT FALSE,
    max_trade_risk_fraction numeric NOT NULL CHECK (max_trade_risk_fraction > 0 AND max_trade_risk_fraction <= 0.02),
    max_position_fraction numeric NOT NULL CHECK (max_position_fraction > 0 AND max_position_fraction <= 1),
    max_entries_per_day integer NOT NULL CHECK (max_entries_per_day = 1)  -- one live intent/day (002 unique index),
    entry_window_start time NOT NULL,
    entry_window_end time NOT NULL CHECK (entry_window_end > entry_window_start),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
INSERT INTO public.paper_trading_policy
    (singleton, autonomous_entries_enabled, max_trade_risk_fraction,
     max_position_fraction, max_entries_per_day, entry_window_start, entry_window_end)
-- Installed DISABLED. Enabling is a separate owner release step
-- (sql/release/enable_autonomous_entries.sql) after the supervised proof.
VALUES (TRUE, FALSE, 0.01, 1.0, 1, '09:35', '15:30')
ON CONFLICT (singleton) DO NOTHING;
REVOKE ALL ON TABLE public.paper_trading_policy FROM PUBLIC;

DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hds_paper_control_owner') THEN
        CREATE ROLE hds_paper_control_owner NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hds_paper_operator') THEN
        CREATE ROLE hds_paper_operator NOLOGIN NOINHERIT;
    END IF;
END;
$roles$;
GRANT USAGE ON SCHEMA public TO hds_paper_control_owner, hds_paper_operator;
GRANT SELECT, UPDATE (kill_switch_active, submissions_enabled, updated_at)
    ON TABLE public.paper_control TO hds_paper_control_owner;
GRANT SELECT ON TABLE public.paper_trading_policy TO hds_paper_control_owner;
GRANT INSERT ON TABLE public.paper_audit_events TO hds_paper_control_owner;
GRANT USAGE ON SEQUENCE public.paper_audit_events_audit_id_seq TO hds_paper_control_owner;
GRANT hds_paper_control_owner TO neondb_owner;

-- Policy authority runs as the existing reservation guard.
GRANT SELECT ON TABLE public.paper_trading_policy TO hds_paper_reservation_guard;
GRANT SELECT ON TABLE public.paper_equity_snapshots TO hds_paper_reservation_guard;
GRANT SELECT, INSERT ON TABLE public.paper_pilot_approvals TO hds_paper_reservation_guard;
GRANT INSERT ON TABLE public.paper_audit_events TO hds_paper_reservation_guard;
GRANT USAGE ON SEQUENCE public.paper_audit_events_audit_id_seq TO hds_paper_reservation_guard;
-- Read-only visibility of the policy for monitoring.
GRANT SELECT ON TABLE public.paper_trading_policy
    TO hds_paper_win_monitor, hds_paper_executor, hds_paper_reconciler, hds_paper_operator;
GRANT SELECT ON TABLE public.paper_control TO hds_paper_operator;

CREATE OR REPLACE FUNCTION public.paper_policy_authorize_entry(
    p_client_order_id text, p_strategy text, p_symbol text,
    p_trading_day date, p_whole_shares integer,
    p_expected_entry numeric, p_protective_stop numeric,
    p_request_sha256 text, p_account_id_sha256 text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $authorize$
DECLARE
    v_kill boolean; v_enabled boolean;
    v_policy public.paper_trading_policy%ROWTYPE;
    v_now timestamptz := clock_timestamp();
    v_local timestamp := clock_timestamp() AT TIME ZONE 'America/New_York';
    v_today date := (clock_timestamp() AT TIME ZONE 'America/New_York')::date;
    v_open_equity numeric; v_risk numeric; v_cost numeric; v_entries integer;
    v_id text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('horsesdontstop-paper-exclusive-intent'));
    SELECT kill_switch_active, submissions_enabled INTO v_kill, v_enabled
      FROM public.paper_reservation_control_guard();
    IF NOT FOUND OR v_kill IS DISTINCT FROM FALSE OR v_enabled IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'PAPER trading is paused' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_policy FROM public.paper_trading_policy WHERE singleton;
    IF NOT FOUND OR v_policy.autonomous_entries_enabled IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'Autonomous PAPER entries disabled by policy' USING ERRCODE = '42501';
    END IF;
    IF extract(isodow FROM v_local) > 5
       OR v_local::time < v_policy.entry_window_start
       OR v_local::time >= v_policy.entry_window_end THEN
        RAISE EXCEPTION 'Outside policy entry window' USING ERRCODE = '42501';
    END IF;
    IF p_client_order_id IS NULL
       OR p_strategy IS DISTINCT FROM 'momentum_volume'
       OR p_symbol IS NULL OR p_symbol !~ '^[A-Z]{1,5}$'
       OR p_trading_day IS DISTINCT FROM v_today
       OR p_whole_shares IS NULL OR p_whole_shares < 1
       OR p_expected_entry IS NULL OR p_expected_entry <= 0
       OR p_expected_entry::text IN ('NaN','Infinity','-Infinity')
       OR p_protective_stop IS NULL OR p_protective_stop <= 0
       OR p_protective_stop::text IN ('NaN','Infinity','-Infinity')
       OR p_protective_stop >= p_expected_entry
       OR p_request_sha256 IS NULL OR p_request_sha256 !~ '^[0-9a-f]{64}$'
       OR p_account_id_sha256 IS NULL OR p_account_id_sha256 !~ '^[0-9a-f]{64}$'
       OR p_client_order_id !~ ('^hds-'||to_char(p_trading_day,'YYYYMMDD')||'-'||p_symbol||'-[0-9a-f]{12}$') THEN
        RAISE EXCEPTION 'Invalid risk-limited GTC OTO intent' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM public.paper_intents
               WHERE status IN ('reserved','submitted','accepted','unknown')) THEN
        RAISE EXCEPTION 'Earlier PAPER order requires reconciliation' USING ERRCODE = '42501';
    END IF;
    SELECT count(*) INTO v_entries FROM public.paper_intents
     WHERE trading_day = v_today AND status <> 'expired';
    IF v_entries >= v_policy.max_entries_per_day THEN
        RAISE EXCEPTION 'Daily PAPER entry limit reached' USING ERRCODE = '42501';
    END IF;
    -- Equity reference: today's opening window (09:30-09:35 NY). The opening
    -- collector stamps database time, but the executor role can also insert
    -- snapshots, so use the LOWEST equity in the window: an extra or inflated
    -- row can only shrink the limits, never enlarge them.
    SELECT min(s.equity) INTO v_open_equity FROM public.paper_equity_snapshots s
     WHERE (s.observed_at AT TIME ZONE 'America/New_York')::date = v_today
       AND (s.observed_at AT TIME ZONE 'America/New_York')::time >= '09:30'
       AND (s.observed_at AT TIME ZONE 'America/New_York')::time <  '09:35';
    IF v_open_equity IS NULL OR v_open_equity <= 0 THEN
        RAISE EXCEPTION 'Verified opening equity missing' USING ERRCODE = '42501';
    END IF;
    v_risk := (p_expected_entry - p_protective_stop) * p_whole_shares;
    v_cost := p_expected_entry * p_whole_shares;
    IF v_risk > v_open_equity * v_policy.max_trade_risk_fraction + 0.02 THEN
        RAISE EXCEPTION 'PAPER trade risk exceeds policy' USING ERRCODE = '42501';
    END IF;
    IF v_cost + 0.50 > v_open_equity * v_policy.max_position_fraction THEN
        RAISE EXCEPTION 'PAPER position size exceeds policy' USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.paper_intents
       (client_order_id, strategy, symbol, trading_day, whole_shares,
        expected_entry, protective_stop, time_in_force)
    VALUES (p_client_order_id, p_strategy, p_symbol, p_trading_day, p_whole_shares,
            p_expected_entry, p_protective_stop, 'gtc')
    ON CONFLICT DO NOTHING RETURNING client_order_id INTO v_id;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'PAPER intent already exists' USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.paper_pilot_approvals
      (client_order_id, symbol, whole_shares, limit_price, stop_price, purchase_cost,
       fee_reserve, request_sha256, account_id_sha256, approved_at, expires_at)
    VALUES (p_client_order_id, p_symbol, p_whole_shares, p_expected_entry,
            p_protective_stop, v_cost, 0.50, p_request_sha256, p_account_id_sha256,
            v_now, v_now + interval '120 seconds');
    INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
    VALUES (p_client_order_id, 'policy_authorized',
            format('BUY %s %s limit %s stop %s risk %s cost %s open_equity %s',
                   p_whole_shares, p_symbol, p_expected_entry, p_protective_stop,
                   v_risk, v_cost, v_open_equity));
    RETURN TRUE;
END;
$authorize$;

CREATE OR REPLACE FUNCTION public.paper_engage_kill_switch(p_reason text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $pause$
BEGIN
    UPDATE public.paper_control
       SET kill_switch_active = TRUE, submissions_enabled = FALSE, updated_at = now()
     WHERE singleton;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER control row missing' USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
    VALUES (NULL, 'kill_switch_engaged',
            left(coalesce(p_reason, 'unspecified'), 200) || ' by ' || session_user);
    RETURN TRUE;
END;
$pause$;

CREATE OR REPLACE FUNCTION public.paper_arm_autonomous_trading(p_reason text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $arm$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.paper_trading_policy
                   WHERE singleton AND autonomous_entries_enabled) THEN
        RAISE EXCEPTION 'Policy does not allow autonomous PAPER entries' USING ERRCODE = '42501';
    END IF;
    UPDATE public.paper_control
       SET kill_switch_active = FALSE, submissions_enabled = TRUE, updated_at = now()
     WHERE singleton;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER control row missing' USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
    VALUES (NULL, 'trading_armed',
            left(coalesce(p_reason, 'unspecified'), 200) || ' by ' || session_user);
    RETURN TRUE;
END;
$arm$;

-- A dispatch can be committed (intent UNKNOWN, receipt written) and then
-- abort before its single POST (freshness budget, account change, lost lease).
-- Retire such an intent ONLY when: still UNKNOWN, a dispatch receipt exists,
-- the caller attests a definite broker 404 for the client order ID, and at
-- least 15 minutes have passed since it became UNKNOWN (a real POST would be
-- visible at the broker by then). Audited; never authorizes a retry.
CREATE OR REPLACE FUNCTION public.paper_retire_unsent_dispatch(
    p_client_order_id text, p_broker_order_absent boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $retire$
DECLARE v_status text; v_updated timestamptz;
BEGIN
    IF p_broker_order_absent IS DISTINCT FROM TRUE OR p_client_order_id IS NULL THEN
        RAISE EXCEPTION 'Retirement requires a definite broker not-found observation'
            USING ERRCODE = '42501';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('horsesdontstop-paper-exclusive-intent'));
    SELECT status, updated_at INTO v_status, v_updated FROM public.paper_intents
     WHERE client_order_id = p_client_order_id FOR UPDATE;
    IF NOT FOUND OR v_status IS DISTINCT FROM 'unknown' THEN
        RAISE EXCEPTION 'Only an UNKNOWN PAPER intent can be retired' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.paper_pilot_dispatch_receipts
                    WHERE client_order_id = p_client_order_id) THEN
        RAISE EXCEPTION 'No dispatch receipt; manual reconciliation required' USING ERRCODE = '42501';
    END IF;
    IF v_updated > clock_timestamp() - interval '15 minutes' THEN
        RETURN FALSE;
    END IF;
    UPDATE public.paper_intents SET status = 'canceled', updated_at = now()
     WHERE client_order_id = p_client_order_id AND status = 'unknown';
    INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
    VALUES (p_client_order_id, 'dispatch_never_reached_broker',
            'UNKNOWN for >=15 min with broker 404; retired as canceled; never retried');
    RETURN TRUE;
END;
$retire$;

GRANT CREATE ON SCHEMA public TO hds_paper_control_owner, hds_paper_reservation_guard,
    hds_paper_status_owner;
ALTER FUNCTION public.paper_retire_unsent_dispatch(text, boolean) OWNER TO hds_paper_status_owner;
ALTER FUNCTION public.paper_policy_authorize_entry(text,text,text,date,integer,numeric,numeric,text,text)
    OWNER TO hds_paper_reservation_guard;
ALTER FUNCTION public.paper_engage_kill_switch(text) OWNER TO hds_paper_control_owner;
ALTER FUNCTION public.paper_arm_autonomous_trading(text) OWNER TO hds_paper_control_owner;
REVOKE CREATE ON SCHEMA public FROM hds_paper_control_owner, hds_paper_reservation_guard,
    hds_paper_status_owner;
REVOKE ALL ON FUNCTION public.paper_retire_unsent_dispatch(text, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_retire_unsent_dispatch(text, boolean) TO hds_paper_reconciler;

REVOKE ALL ON FUNCTION public.paper_policy_authorize_entry(text,text,text,date,integer,numeric,numeric,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.paper_engage_kill_switch(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.paper_arm_autonomous_trading(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.paper_policy_authorize_entry(text,text,text,date,integer,numeric,numeric,text,text)
    TO hds_paper_executor;
GRANT EXECUTE ON FUNCTION public.paper_engage_kill_switch(text)
    TO hds_paper_executor, hds_paper_reconciler, hds_paper_operator;
GRANT EXECUTE ON FUNCTION public.paper_arm_autonomous_trading(text) TO hds_paper_operator;

COMMIT;
