-- Production compatibility grant for the existing Windows monitor login.
-- This role is not the heartbeat writer and receives no write permissions.
-- Apply only after checking the role's independent ACL attestation.
GRANT SELECT ON public.paper_worker_heartbeat TO hds_paper_win_monitor;
