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
