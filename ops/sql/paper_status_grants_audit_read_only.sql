-- PAPER STATUS INTEGRITY, READ-ONLY PRIVILEGE AUDIT.
-- Diagnostic only, does NOT fix role grants or enable trading.
-- Explicitly select the target Neon branch in the client before execution.
-- Never supply production credentials in logs or paste them into GitHub.
-- One SELECT, safe for live read-only examination.
SELECT
    current_database() AS database_name,
    current_user AS inspection_role,
    has_table_privilege('hds_paper_executor','public.paper_intents','INSERT')
        AS executor_can_insert_intents,
    has_table_privilege('hds_paper_executor','public.paper_intents','DELETE')
        AS executor_can_delete_intents,
    has_column_privilege('hds_paper_executor','public.paper_intents','status','UPDATE')
        AS executor_can_update_status,
    has_column_privilege('hds_paper_executor','public.paper_intents','broker_order_id','UPDATE')
        AS executor_can_update_broker_order_id,
    has_column_privilege('hds_paper_executor','public.paper_intents','updated_at','UPDATE')
        AS executor_can_update_updated_at,
    has_table_privilege('hds_paper_executor','public.paper_audit_events','INSERT')
        AS executor_can_insert_audit,
    has_table_privilege('hds_paper_executor','public.paper_control','UPDATE')
        AS executor_can_toggle_controls,
    has_table_privilege('hds_paper_executor','public.paper_equity_snapshots','INSERT')
        AS executor_can_record_equity,
    pg_has_role('hds_paper_executor','neondb_owner','MEMBER')
        AS executor_is_database_owner_member,
    EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid='public.paper_intents'::regclass
          AND NOT tgisinternal
    ) AS intent_has_custom_triggers;
