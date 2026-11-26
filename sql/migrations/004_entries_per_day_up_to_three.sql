-- Allow up to THREE PAPER entries per New York trading day (owner request
-- 2026-11-10, for testing). Applied by the owner via the Neon integration.
--
-- Unchanged safety: every entry still needs paper_policy_authorize_entry
-- (ARMED controls, policy enabled, entry window, risk <= 1% and size within
-- policy of the LOWEST opening snapshot, no open intent, exclusive-intent
-- lock), the single-use dispatch commit, and the executor still refuses new
-- entries while any position or order is open (one position at a time).
-- The daily count is enforced by the policy function under the lock; the
-- one-intent-per-day unique index is removed because it hard-capped at one.
-- Client order IDs stay unique via the primary key (sequence-aware IDs).
BEGIN;

DROP INDEX IF EXISTS public.uq_paper_intents_trading_day;

ALTER TABLE public.paper_trading_policy
    DROP CONSTRAINT IF EXISTS paper_trading_policy_max_entries_per_day_check;
ALTER TABLE public.paper_trading_policy
    ADD CONSTRAINT paper_trading_policy_max_entries_per_day_check
    CHECK (max_entries_per_day BETWEEN 1 AND 3);

UPDATE public.paper_trading_policy
   SET max_entries_per_day = 3, updated_at = clock_timestamp()
 WHERE singleton;
INSERT INTO public.paper_audit_events(client_order_id, kind, summary)
VALUES (NULL, 'policy_changed', 'max_entries_per_day=3 by owner migration 004 (testing)');

COMMIT;

-- To return to one entry per day later (owner):
-- UPDATE public.paper_trading_policy SET max_entries_per_day = 1, updated_at = clock_timestamp() WHERE singleton;
