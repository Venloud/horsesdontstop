-- STAGED independent liveness state, CLOUDFLARE D1 ONLY, NOT Neon.
-- NO broker positions, orders, equity, client IDs or credentials stored here.
CREATE TABLE IF NOT EXISTS liveness (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  worker_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK (sequence > 0),
  status TEXT NOT NULL CHECK (
    status IN ('read_only_poll_ok', 'closed_flat_idle', 'poll_failed')
  ),
  observed_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS alarm_state (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  code TEXT NOT NULL,
  delivered_ms INTEGER NOT NULL
);
