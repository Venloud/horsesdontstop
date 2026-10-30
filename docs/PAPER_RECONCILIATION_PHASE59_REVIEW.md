# Phase 5.9 reconciliation quantity integrity

Review-only PR #41; no deployment or order authorization.

The nested OTO audit previously accepted five entry fills, one held share,
and an unchanged five-share stop. It checked only whether exposure exceeded
fills and whether stop coverage was at least exposure. Neither check explained
the missing four shares or prevented an oversized exit.

The audit now requires the linked stop quantity to equal entry fills and
entry fills minus stop fills to equal live exposure. Stop status must agree
with its fill counter: an active unfilled stop has zero fills, a partially
filled stop has strictly intermediate fills, and a filled stop is complete.
Only independently fetched nested broker evidence can explain a reduction.
Manual sales, resized stops, inconsistent snapshots and unexplained changes
remain blocked; no inference from an executor ACK or caller explanation.
Existing partial-entry, missing-stop, GTC and transition gates remain intact.
Fully closed positions remain ineligible for journal promotion to filled.

Regression coverage includes the exact 5/1/5 journal rejection, unexplained
reductions, oversized exposure, resized stops, inconsistent fill counters,
partial entries, missing stops, and an independently linked partial stop fill
that explains the reduced position while retaining exact remaining coverage.
All evidence is synthetic; this is not proof of a deployed broker service.

Use the existing `.venv\Scripts\python.exe -m pytest -q` on Windows.
The global interpreter lacks the project dependency context. Sandbox cache
permissions and Node child-process `spawn EPERM` require normal host execution;
do not remove tests or alter security assertions to bypass them.
CI continues to use Python 3.11 and the unchanged full Python/Node suites.

Still required: separately approved isolated Neon ACL cutover rehearsal,
restricted-role denial/rollback and legitimate equity/reservation evidence,
independent review and PR38/41 coordination, explicit merge/deployment approval,
a separate trusted read-only reconciler identity and genuine broker evidence,
restart/watchdog/stop lifecycle acceptance, and separate exact PAPER order
authorization. No Neon grants, merges, submissions or Alpaca writes in this work.
