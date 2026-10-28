-- REVIEW ONLY. NOT A PRODUCTION MIGRATION OR AUTOMATIC INSTALLER.
-- Phase 5.8: run ONLY after the separately reviewed PR38 reservation
-- migration AND PR41 status PREPARE migration AND matching Python deploy.
-- Existing main collector is not compatible with this change until its
-- executor permission attestation is updated; coordinate maintenance.
-- Deliberately does NOT grant credentials, change paper_control or submit
-- broker orders. Isolated owner-authorized Neon rehearsal required.
BEGIN;

-- Enforce ordering BEFORE revoking. No raw INSERT may remain: otherwise
-- an executor could manufacture 'filled' via INSERT without needing UPDATE.
DO $preflight$
BEGIN
    IF current_user <> 'neondb_owner' THEN
        RAISE EXCEPTION 'Owner-only status cutover rehearsal' USING ERRCODE='42501';
    END IF;
    IF to_regprocedure('public.paper_reserve_intent_if_enabled(text,text,text,date,integer,numeric,numeric)') IS NULL
       OR to_regprocedure('public.paper_mark_reserved_intent_unknown(text)') IS NULL
       OR to_regprocedure('public.paper_record_reconciled_intent_status(text,text,text,text)') IS NULL
       OR to_regclass('public.uq_paper_intents_trading_day') IS NULL THEN
        RAISE EXCEPTION 'PR38 reservation and PR41 status PREPARE must both exist'
            USING ERRCODE='42501';
    END IF;
    IF NOT has_function_privilege('hds_paper_executor',
       'public.paper_reserve_intent_if_enabled(text,text,text,date,integer,numeric,numeric)', 'EXECUTE')
       OR NOT has_function_privilege('hds_paper_executor',
       'public.paper_mark_reserved_intent_unknown(text)', 'EXECUTE')
       OR has_function_privilege('hds_paper_executor',
       'public.paper_record_reconciled_intent_status(text,text,text,text)', 'EXECUTE')
       OR NOT has_function_privilege('hds_paper_reconciler',
       'public.paper_record_reconciled_intent_status(text,text,text,text)', 'EXECUTE')
       OR has_function_privilege('hds_paper_reconciler',
       'public.paper_mark_reserved_intent_unknown(text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Incorrect restricted function grants before cutover'
            USING ERRCODE='42501';
    END IF;
    IF pg_has_role('hds_paper_executor','hds_paper_reconciler','MEMBER')
       OR pg_has_role('hds_paper_executor','hds_paper_status_owner','MEMBER')
       OR pg_has_role('hds_paper_executor','hds_paper_reservation_guard','MEMBER')
       OR pg_has_role('hds_paper_executor','neondb_owner','MEMBER')
       OR pg_has_role('hds_paper_executor','neon_superuser','MEMBER')
       OR has_schema_privilege('hds_paper_executor','public','CREATE')
       OR has_database_privilege('hds_paper_executor',current_database(),'CREATE') THEN
        RAISE EXCEPTION 'Executor can assume privileged functions owner'
            USING ERRCODE='42501';
    END IF;
    IF has_table_privilege('hds_paper_executor','public.paper_intents','INSERT')
       OR has_table_privilege('hds_paper_executor','public.paper_control','UPDATE')
       OR NOT has_table_privilege('hds_paper_executor',
                                'public.paper_equity_snapshots','INSERT') THEN
        RAISE EXCEPTION 'PR38 raw INSERT/control/equity ACL prerequisite failed'
            USING ERRCODE='42501';
    END IF;
END
$preflight$;

-- Drop table and per-column UPDATE separately to close all old grants.
-- A column-level UPDATE grant survives REVOKE UPDATE ON TABLE alone.
REVOKE UPDATE ON TABLE public.paper_intents FROM hds_paper_executor;
REVOKE UPDATE (status, broker_order_id, updated_at)
    ON TABLE public.paper_intents FROM hds_paper_executor;
REVOKE INSERT ON TABLE public.paper_audit_events FROM hds_paper_executor;
-- Defense in depth even when PR38's revocation was already applied.
REVOKE INSERT ON TABLE public.paper_intents FROM hds_paper_executor;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.paper_audit_events FROM hds_paper_executor;
REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.paper_intents FROM hds_paper_executor;
-- Table revocation does not remove column-specific grants. Cover every
-- column, including intent terms and audit summary/identity, not just status.
DO $columns$
DECLARE v_table text; v_columns text;
BEGIN
    FOREACH v_table IN ARRAY ARRAY['paper_intents','paper_audit_events'] LOOP
        SELECT string_agg(quote_ident(attname), ',') INTO v_columns
        FROM pg_attribute
        WHERE attrelid=format('public.%I',v_table)::regclass
          AND attnum>0 AND NOT attisdropped;
        EXECUTE format('REVOKE INSERT (%s), UPDATE (%s), REFERENCES (%s) ON TABLE public.%I FROM hds_paper_executor',
                       v_columns,v_columns,v_columns,v_table);
    END LOOP;
END
$columns$;

-- The executor must only be able to reserve through PR38 and mark an
-- ambiguous broker response UNKNOWN through PR41, never forge broker truth.
DO $verify$
BEGIN
    IF EXISTS (
        SELECT 1 FROM (VALUES ('hds_paper_executor'),('hds_paper_reconciler')) AS r(role_name)
        CROSS JOIN (VALUES ('public.paper_intents'),('public.paper_audit_events'),
                           ('public.paper_control')) AS t(tbl)
        WHERE has_any_column_privilege(r.role_name,t.tbl,'INSERT,UPDATE,REFERENCES')
           OR has_table_privilege(r.role_name,t.tbl,'DELETE,TRUNCATE,TRIGGER')
    ) THEN
        RAISE EXCEPTION 'Inherited, PUBLIC or column journal write bypass; rollback'
            USING ERRCODE='42501';
    END IF;
    IF has_table_privilege('hds_paper_executor','public.paper_intents','INSERT')
       OR has_table_privilege('hds_paper_executor','public.paper_intents','UPDATE')
       OR has_column_privilege('hds_paper_executor','public.paper_intents','status','UPDATE')
       OR has_column_privilege('hds_paper_executor','public.paper_intents','broker_order_id','UPDATE')
       OR has_column_privilege('hds_paper_executor','public.paper_intents','updated_at','UPDATE')
       OR has_table_privilege('hds_paper_executor','public.paper_audit_events','INSERT')
       OR has_table_privilege('hds_paper_executor','public.paper_control','UPDATE')
       OR has_table_privilege('hds_paper_executor','public.paper_intents','DELETE')
       OR pg_has_role('hds_paper_executor','hds_paper_reconciler','MEMBER')
       OR pg_has_role('hds_paper_executor','hds_paper_status_owner','MEMBER')
       OR pg_has_role('hds_paper_executor','hds_paper_reservation_guard','MEMBER')
       OR pg_has_role('hds_paper_executor','neondb_owner','MEMBER')
       OR pg_has_role('hds_paper_executor','neon_superuser','MEMBER')
       OR has_schema_privilege('hds_paper_executor','public','CREATE')
       OR has_database_privilege('hds_paper_executor',current_database(),'CREATE')
       OR has_function_privilege('hds_paper_executor',
          'public.paper_record_reconciled_intent_status(text,text,text,text)', 'EXECUTE')
       OR NOT has_function_privilege('hds_paper_executor',
          'public.paper_mark_reserved_intent_unknown(text)', 'EXECUTE')
       OR NOT has_function_privilege('hds_paper_executor',
          'public.paper_reserve_intent_if_enabled(text,text,text,date,integer,numeric,numeric)', 'EXECUTE')
       OR NOT has_table_privilege('hds_paper_executor',
          'public.paper_equity_snapshots','INSERT')
       OR has_column_privilege('hds_paper_reconciler','public.paper_intents','status','UPDATE')
       OR has_table_privilege('hds_paper_reconciler','public.paper_audit_events','INSERT') THEN
        RAISE EXCEPTION 'Status cutover ACL verification failed, rollback'
            USING ERRCODE='42501';
    END IF;
END
$verify$;
COMMIT;

-- This cutover does NOT authenticate a broker or prove that a stop lives
-- overnight. No paper orders or production grant changes are authorized.
