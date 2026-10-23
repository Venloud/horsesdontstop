# Windows PAPER equity collector staging

This is a separate persistent Windows process using the existing
`bot.paper_opening_equity_daemon`; it does not replace the read-only
supervisor, Windows DPAPI supervisor launcher, or Linux systemd templates.

## One-time setup

A **separately provisioned, independently audited** restricted Neon executor
DSN is required, never an admin, heartbeat, or monitor DSN. It must satisfy
`PaperExecutorJournal.assert_executor_permissions()`. Do not create such a role
from an admin session without a separate role/GRANT review. Store only on the
Windows PC by running
`powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\ops\windows\set-paper-equity-executor-credential.ps1`.
It prompts with hidden input. Never paste the DSN in chat or logs.
Other DPAPI files are the ones already provisioned for the supervisor.

After confirming your Windows timezone is **Eastern Time**, register the task:
`powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\ops\windows\install-paper-opening-equity-task.ps1`.
This starts a continuous 15-second collector at user logon and weekdays 09:20
**Windows local time**. Windows must be powered on, signed in (locked is fine),
connected to internet, and not sleeping. Task restarts failed processes up to
three times one minute apart. A shut-down PC cannot collect an opening.

The collector's existing private ntfy integration attempts critical alerts
when the opening baseline is missing or cannot be checked after 09:35, or when
an alert transport fails. No delivery is guaranteed until tested on your device.
Check Task Scheduler history and application logs for startup/restart failure.
A task-level failure before the Python collector initializes has no guaranteed
remote notification. This is still temporary PC hosting, not cloud 24/7 uptime.

## Financial invariants

No broker order submissions, cancellations, or replacements. Only actual
broker-observed PAPER equity can be inserted, and `record_opening_equity`
enforces Neon DB-clock 09:30:00–09:34:59 New York time. There is no late
backfill and no fabricated history. The release flag stays False, kill switch
stays ON, submissions stay OFF. The Linux service continues to use the SAME
Python module, so moving to a cloud host requires only host-specific wiring.

Do not confuse a successfully installed task with a verified snapshot. After
the next true NYSE opening, run the existing read-only supervisor
`-Diagnose` and confirm `independent_assessment: PASS`. Failed opening
capture should remain BLOCKED for that trading day.

Task removal: `Unregister-ScheduledTask -TaskName
'HorsesDontStop-PAPER-OpeningEquity' -Confirm:$false`. This does not delete
the separately encrypted DPAPI credential or other supervisor files.

## Monitoring tasks (2026-10-23)

Three Windows tasks, one job each. None can place, cancel or replace orders.

| Task | What it is | Schedule |
| --- | --- | --- |
| `HorsesDontStop-PAPER-OpeningEquity` | collector (above) | logon + weekdays 09:20 |
| `HorsesDontStop-PAPER-Supervisor` | **continuous** read-only supervisor: full Alpaca+Neon poll and restricted lease pulse every 30 s while the market is open or anything is exposed; broker-verified closed+flat ticks skip Neon but still post the Cloudflare liveness beat | logon + weekdays 09:10, one instance |
| `HorsesDontStop-PAPER-Diagnostic` | **one-shot** read-only diagnostic | weekdays 09:21 (pre-open) and 09:36 (full) |

Register or update the two monitoring tasks (replaces the hand-made
diagnostic task of the same name, never duplicates it):
`powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\ops\windows\install-paper-monitoring-tasks.ps1`

The diagnostic no longer writes the heartbeat lease or posts a Cloudflare
beat. Earlier versions did, which left a one-off lease that turned stale and
made Cloudflare page the phone every hour. Lease and liveness now belong to
the continuous supervisor alone, and the diagnostic only reads them.

Pre-open (before 09:30 New York) stages: `required_env`,
`opening_equity_collector`, `continuous_supervisor_process` (exactly one root
python process each), `alpaca_account`, `alpaca_positions_orders`,
`neon_journal`, `neon_control`, `heartbeat_writer_role`,
`external_liveness_config`, `nyse_calendar` (today is a session).
The full run adds `observation_freshness`, `reconciliation`,
`independent_assessment` (fails `MISSING_OPENING_EQUITY` without today's
baseline) and `continuous_supervisor_lease` (lease pulsed within 120 s by a
successful poll). Exit code 0 means every stage passed; 2 means anything failed.

Outputs in `%LOCALAPPDATA%\horsesdontstop`:
- `diagnostic-log.jsonl`: one JSON line per run of fixed stage tokens, rotated at 512 KB.
- `diagnostic-alert-state.json`: one ntfy alert per unchanged failure set per
  New York day. The receipt is written only after ntfy accepts, so a failed
  delivery is retried on the next run.

Test the phone path once: `.\ops\windows\run-paper-supervisor.ps1 -TestAlert`.
