-- STAGED ONLY. DO NOT APPLY TO NEON PRODUCTION WITHOUT OWNER APPROVAL.
-- Tested on an isolated Neon branch. No order submissions or switch flips.
--
-- Heartbeat table has only one lease for the entire PAPER worker group.
-- A restart uses a NEW process-instance ID; a duplicate or old sequence
-- must never update the heartbeat row merely to appear alive.
CREATE TABLE IF NOT EXISTS public.paper_worker_heartbeat (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    worker_id TEXT NOT NULL CHECK (
        worker_id ~ '^[A-Za-z0-9_.:-]{8,64}$'
    ),
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    poll_succeeded BOOLEAN NOT NULL DEFAULT FALSE
);

-- After review, provision a SEPARATE non-admin SQL role:
--   hds_paper_heartbeat (LOGIN, no neon_superuser membership, no CREATE)
-- and give it only INSERT, SELECT and column-limited UPDATE on this table.
-- DO NOT give it UPDATE on public.paper_control, or INSERT/UPDATE/DELETE
-- on public.paper_intents or public.paper_audit_events.
--
-- Suggested scoped grants, only AFTER independent privilege review:
--
-- GRANT USAGE ON SCHEMA public TO hds_paper_heartbeat;
-- GRANT SELECT, INSERT ON public.paper_worker_heartbeat TO hds_paper_heartbeat;
-- GRANT UPDATE (worker_id, sequence, observed_at, poll_succeeded)
--   ON public.paper_worker_heartbeat TO hds_paper_heartbeat;
-- GRANT SELECT ON public.paper_worker_heartbeat TO hds_paper_monitor;
--
-- DO NOT grant to the database owner or add credentials to this repo.
-- Never use the executor DSN as a heartbeat writer or vice versa.
