# PAPER observer incident/heartbeat integrity, staging only

**Prepared October 12, 2026. No trading release. No production migration.**

This branch builds on merged PR #11 without touching the already open strict
executor-type draft PR #13. It is intentionally limited to the read-only
monitor, incident classifier, and *unhosted* worker heartbeat candidate.

## What was unsafe to assume

Previously, `paper_supervisor_loop.valid_observation` could return true if
the report's reconciliation merely said it was consistent. That check did
**not** require a current broker observation timestamp, intact incident
classification, exact zero exposure, closed Neon kill controls, or all of the
existing incident-policy checks. A successful heartbeat therefore could have
been written after a report was stale or contained an unexpected warning.

A heartbeat is **evidence of a recent successful read-only health poll**,
not a permit to submit trades. This branch strengthens precisely that
evidence contract.

## New conservative behavior

1. The worker checks the report's attached `monitor_incident`: only
   `level=idle` and `code=idle` qualify. Missing or contradictory
   incident metadata fails.
2. The worker independently reruns `assess_read_only_report` on the whole
   report and matches its stable fingerprint. It demands an actual
   timezone-aware observation timestamp from **0 to 120 seconds old**.
   Missing/future/replayed reports never renew a healthy heartbeat.
3. The incident classifier already checks the precise broker PAPER
   endpoint, account-order/position counts, unresolved journal intents,
   uncovered stops, emergency Neon kill ON, database submissions OFF, and
   code order release OFF. The worker now inherits those checks.
4. Noninteger/bool `broker_write_operations` counts are invalid, even
   though in Python `False == 0`. Alpaca's clock-open and
   account-trading-blocked fields must also be real booleans or the
   broker/Neon read-only tick fails with a sanitized error; the alerting
   CLI treats that outcome as unverifiable and urgent.
5. The three **default-unverified** cash-flow, equity-history and exchange
   calendar proof flags are expected for a deliberately disabled
   observer. They remain **hard blockers in the executor**, and do not
   falsely turn a no-exposure read-only observation into a failed worker
   poll just for being unavailable.

Tests use only fake broker, journal, report, and heartbeat objects.
No Alpaca order POST, database control UPDATE, cloud host, scheduled
worker, owner authentication, or real PAPER stop lifecycle occurred.

## Not proven / launch blockers

- Durable 24/7 hosting and an **independently supervised** alerting path
- Live Alpaca PAPER DAY-parent/STOP-child GTC continuation over the close
- Complete historical deposit/withdrawal and peak-equity audit
- Genuine NYSE calendar and timely verified market quotes
- One-time *authenticated* explicit owner approval of exact PAPER order
- Production heartbeat restricted role and actual persisted worker health

A green unit test suite means the code's synthetic contracts held, **not**
that a persistent monitor exists. Both the code-level release switch and
Neon production kill controls remain unchanged. Keep all broker order
submissions OFF.