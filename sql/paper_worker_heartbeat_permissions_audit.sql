-- READ-ONLY inspection. Safe to run separately on TEMP and PROD.
-- Does not create roles, apply grants, insert heartbeats or touch order state.
-- A missing role/table is reported as false, not treated as success.
WITH expected_role(role_name) AS (
    VALUES ('hds_paper_monitor'::text), ('hds_paper_heartbeat'::text)
)
SELECT
    e.role_name,
    r.oid IS NOT NULL AS role_exists,
    COALESCE(r.rolcanlogin, FALSE) AS has_login,
    COALESCE(r.rolsuper OR r.rolcreaterole OR r.rolcreatedb
             OR r.rolreplication OR r.rolbypassrls, FALSE) AS elevated_role,
    COALESCE(pg_has_role(r.oid, 'neon_superuser'::regrole, 'MEMBER'), FALSE)
        AS member_of_neon_superuser,
    to_regclass('public.paper_worker_heartbeat') IS NOT NULL AS heartbeat_table_exists,
    COALESCE(has_schema_privilege(r.oid, 'public', 'USAGE'), FALSE)
        AS can_use_public_schema,
    COALESCE(has_table_privilege(r.oid, to_regclass('public.paper_worker_heartbeat'), 'SELECT'), FALSE)
        AS heartbeat_select,
    COALESCE(has_table_privilege(r.oid, to_regclass('public.paper_worker_heartbeat'), 'INSERT'), FALSE)
        AS heartbeat_insert,
    COALESCE(has_table_privilege(r.oid, to_regclass('public.paper_worker_heartbeat'), 'UPDATE'), FALSE)
        AS heartbeat_update,
    COALESCE(has_table_privilege(r.oid, to_regclass('public.paper_worker_heartbeat'), 'DELETE'), FALSE)
        AS heartbeat_delete,
    COALESCE(has_table_privilege(r.oid, to_regclass('public.paper_control'), 'UPDATE'), FALSE)
        AS control_update,
    COALESCE(has_table_privilege(r.oid, to_regclass('public.paper_intents'), 'INSERT'), FALSE)
        AS intent_insert
FROM expected_role e
LEFT JOIN pg_roles r ON r.rolname = e.role_name
ORDER BY e.role_name;
