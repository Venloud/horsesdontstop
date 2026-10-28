"""Effective PostgreSQL ACL checks shared by staged executor/reconciler.

SELECT only. Column grants, NOINHERIT role membership and callable definer
functions must not provide a route around the narrow journal APIs.
"""
from .paper_safety import TradeBlocked


BOUNDARY_SQL = """
SELECT /* paper_acl_boundary */
 session_user = current_user
 AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user AND rolcanlogin)
 AND NOT EXISTS (
   SELECT 1 FROM pg_roles r
   WHERE (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
          OR r.rolbypassrls OR r.rolname IN
          ('hds_paper_status_owner','hds_paper_reservation_guard','neondb_owner'))
     AND pg_has_role(current_user,r.oid,'MEMBER'))
 AND NOT has_schema_privilege(current_user,'public','CREATE')
 AND NOT has_database_privilege(current_user,current_database(),'CREATE')
 AND NOT EXISTS (
   SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN
     ('paper_intents','paper_audit_events','paper_control','paper_equity_snapshots')
     AND pg_has_role(current_user,c.relowner,'MEMBER'))
 AND NOT EXISTS (
   SELECT 1 FROM (VALUES ('public.paper_intents'),('public.paper_audit_events'),
                        ('public.paper_control')) AS t(tbl)
   WHERE has_any_column_privilege(current_user,t.tbl,'INSERT,UPDATE,REFERENCES')
      OR has_table_privilege(current_user,t.tbl,'DELETE,TRUNCATE,TRIGGER'))
 AND NOT EXISTS (
   SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND p.prosecdef
     AND has_function_privilege(current_user,p.oid,'EXECUTE')
     AND p.oid::regprocedure::text NOT IN
       ('paper_reserve_intent_if_enabled(text,text,text,date,integer,numeric,numeric)',
        'paper_mark_reserved_intent_unknown(text)',
        'paper_consume_submission_authorization(text)',
        'paper_record_reconciled_intent_status(text,text,text,text)',
        'paper_authorize_unknown_submission(text,text,text,date,integer,numeric,numeric)'))
"""


def assert_journal_acl_boundary(cursor, *, reconciler=False):
    sql = BOUNDARY_SQL
    if reconciler:
        sql += """ AND NOT EXISTS (
          SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='public' AND p.proname IN
            ('paper_reserve_intent_if_enabled','paper_mark_reserved_intent_unknown',
             'paper_consume_submission_authorization',
             'paper_authorize_unknown_submission','paper_reservation_control_guard')
            AND has_function_privilege(current_user,p.oid,'EXECUTE'))"""
    cursor.execute(sql)
    if cursor.fetchone() != (True,):
        raise TradeBlocked("Unsafe effective PAPER role/column/function privileges")
