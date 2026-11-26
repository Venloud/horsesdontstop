-- Expired proposals must not consume the trading day's single intent slot.
-- uq_paper_intents_trading_day enforced one intent per day in ANY status, so
-- after an unapproved proposal expired no new proposal could be made that
-- day (2026-11-09 WMT). Keep one live-or-dispatched intent per day; exclude
-- only expired (never dispatched, broker-404, audited) rows.
BEGIN;
DROP INDEX IF EXISTS public.uq_paper_intents_trading_day;
CREATE UNIQUE INDEX uq_paper_intents_trading_day
    ON public.paper_intents (trading_day) WHERE status <> 'expired';
COMMIT;
