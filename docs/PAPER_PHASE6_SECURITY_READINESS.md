# Phase 6: integrated security staging and October 29 PAPER readiness

Owner: Venloud. Target: Thursday October 29, 2026, 09:30 America/New_York.
**Verdict: BLOCKED / NO EXECUTABLE ORDER.** This is staged engineering, not a
production deployment, database cutover, order release or merge authorization.

## Integrated implementation

PR41 now includes PR38's reviewed reservation migration, real concurrency
harness and its tests from `b3a7454d4394600e6185b1075cf15b94c7710f3d`.
The overlapping Python journal and tests retain PR41's stricter post-cutover
privileges and protected atomic reservation. Do not subsequently replace them
with PR38's legacy status/audit grant expectations. Review the combined diff
against main as one coordinated security release; both PRs remain unmerged.

The executor consumes the reserved intent into committed UNKNOWN before any
POST. A new transaction calls the review-only final authorization function,
rechecks exact durable terms and fresh controls, and holds the control FOR SHARE
lock through the attempt. A kill UPDATE committed first blocks; one queued after
the lock must wait. PostgreSQL and Alpaca do not share a transaction: this is
serialization, not cancellation of an in-flight POST. Broker and DB timeouts,
crashes and lost commit responses remain UNKNOWN; NEVER retry the reserved ID.
Approval, quotes, account, exposure, buying power and entry deadline are rechecked
after lock waits. Production release remains hard-coded FALSE.
The gateway also rejects the SDK's default retrying/unbounded transport.
`make_supervised_paper_client` uses PAPER-only URLs, no redirects, no SDK or
adapter retries, and bounded connect/read inactivity timeouts. An intercepted
real alpaca-py 429 test proves one HTTP POST attempt, not merely one Python call.
These timeouts do not create a distributed atomic transaction or guarantee
emergency cancellation of a request already accepted by the broker.

The gateway independently refetches momentum-volume completed bars and the
preceding broker-confirmed exchange session. Missing, stale, duplicate or
ineligible bars block; a ranking or caller boolean is not a strategy signal.

Both nested recovery and the separate monitoring/post-fill consumer now enforce
`entry fills - verified linked stop fills = actual position`, consistent stop
status/fill counters and exact remaining coverage. Unknown/pending competing
exits and expired/unverifiable stop expiration cannot establish protection.
Partial entries, missing stops and unexplained increases/reductions fail closed.

Shared effective ACL checks cover column grants, table ownership, NOINHERIT
membership escalation, schema/database CREATE and unexpected callable definer
functions. They require an actual LOGIN with `session_user=current_user`;
owner `SET ROLE` is not independent service authentication. The cutover also
revokes all intent/audit column grants and rejects inherited/PUBLIC bypasses.
A bounded reconciler tick rebuilds pending plans from durable DB terms after
restart and avoids rewriting unchanged independently verified observations.
`python -m bot.paper_reconciler_service` is a staged, observe-only service
entrypoint using distinct `PAPER_RECONCILER_DSN`,
`ALPACA_RECONCILER_PAPER_API_KEY`, `ALPACA_RECONCILER_PAPER_SECRET_KEY` and ntfy
configuration. Its broker transport refuses every non-GET operation. Status
writing is hard-disabled by `PAPER_RECONCILIATION_SERVICE_RELEASED=False`;
`--apply-status` cannot bypass that gate. No service/task was installed or started.
After separate deployment approval, its `--continuous` mode supplies bounded
polling and critical failure alerts; existing independent supervisor acceptance
and actual credentials remain mandatory, not inferred from this entrypoint.

## Exact separately authorized isolated rehearsal scope

**Not authorized or executed.** Proposed new child name:
`hds-phase6-security-cutover-20261028`, project `misty-shadow-43891652`, parent
production `br-restless-sunset-b5s18rj3`. Cloning reads the parent; ALL writes
must target only the newly returned child ID. No existing evidence branch is
modified. Before SQL, independently verify child ID, name, parent, endpoint,
nondefault/nonprimary identity and initial kill ON/submissions OFF.

Owner approval is required for creation and this complete scope:

1. Transactional `sql/paper_reservation_control_guard_review_only.sql` (PR38):
   NOLOGIN reservation guard, narrow definer ownership, private control lock,
   atomic reservation and unique trading-day index; revoke executor raw INSERT.
2. Transactional `sql/paper_status_prepare_review_only.sql` (PR41): NOLOGIN
   status owner/reconciler, SELECT + narrow status/audit functions; PUBLIC EXECUTE
   revoked. Executor can only mark an existing reserved intent UNKNOWN.
3. Transactional `sql/paper_status_executor_cutover_review_only.sql`: verify
   prerequisites, revoke direct executor journal/audit table AND column mutation
   rights, reject effective inherited/PUBLIC bypasses; retain equity INSERT.
4. Transactional `sql/paper_submission_reauthorization_review_only.sql`: one
   narrow executor-callable authorization function owned by reservation guard;
   control lock and exact freshly consumed UNKNOWN terms; no control flips.
5. Transactional `sql/paper_reconciler_service_identity_review_only.sql`: distinct
   nonadmin LOGIN inheriting ONLY the narrow reconciler capability. It sets no
   password; privately provision a separate test credential without logging it.
6. On the child only, create `public.hds_pr38_test_marker` containing the actual
   child ID; grant marker SELECT to executor and reconciler service for identity
   checks. No production marker, principal, grants or function changes.
7. Run `ops/paper_phase6_acl_acceptance.py --execute-isolated` using privately
   injected `HDS_PHASE6_TEST_BRANCH_ID`, `HDS_PHASE6_TEST_OWNER_DSN`,
   `HDS_PHASE6_TEST_EXECUTOR_DSN`, `HDS_PHASE6_TEST_RECONCILER_DSN`. All must use
   the SAME child direct endpoint; three actual login/backend identities required.
   Synthetic intent uses the test database's current NY date only to exercise
   the real final-authorization freshness gate. There are zero broker operations.
8. The harness temporarily flips ONLY CHILD controls for synthetic reservation
   and final-lock testing, restores kill ON/submissions OFF, deletes only fixtures
   verified absent beforehand, and preserves existing data. It tests raw forgery,
   ID substitution, replay/stale/terminal transitions, audit atomicity, rollback,
   equity INSERT rollback and cross-connection lock denial. Unexpected SQLSTATE,
   connection error or cleanup failure is FAILURE, never acceptance.
9. Re-run existing concurrency harness with `HDS_PR38_EXPECTED_BRANCH_ID` set to
   that same child ID and its own private child DSNs. It retains production refusal
   and requires the matching marker. Preserve sanitized evidence and all branches.

Real opening-equity window behavior must also pass after coordinated deployment;
a synthetic rolled-back equity INSERT proves ACL compatibility only. Do not
claim this unexecuted harness has established real denial or race safety.

## Current authenticated evidence and limits

Windows probe at **2026-10-28T02:28:37Z** (October 27 Eastern) used existing DPAPI
credentials in memory. Broker PAPER GETs succeeded: equity/cash/buying power each
$500; trading_blocked false; positions 0; open orders 0; market CLOSED. Authentic
Alpaca calendar reports October 29 09:30â€“16:00 New York.

Monitor-only Neon login `hds_paper_win_monitor` succeeded. Kill ON, submissions
OFF, intents 0. Current genuine opening equity: October 27 **13:30:16.216309Z**;
prior October 26 **13:30:12.072Z**. The opening-equity milestone remains complete.
Existing collector/supervisor processes and tasks were inspected, not restarted,
reinstalled or replaced. Their current long-running processes started October 25.
Task last-result `2147946720` alone does not establish failure or acceptance.

Existing heartbeat writer authenticated and passed its narrow ACL check;
Cloudflare settings validated. Last Neon heartbeat observed October 27
19:59:51.932Z, poll_succeeded true. Off-hours Neon idle can explain staleness,
but that is NOT current market-open lease or receiver acceptance. No competing
heartbeat was posted. Fresh independent Cloudflare receipt/dead-host behavior
was not observed here. One real Windows ntfy TEST was HTTP-accepted, exit 0;
actual phone receipt has not been confirmed.

Fresh read-only Neon catalog checks confirm legacy executor status UPDATE and
audit INSERT remain TRUE in production and `br-red-hall-b5h60v04`. Production
has zero intents/audits and no staged functions. The existing isolated branch
has 2 synthetic intents/audits; definer functions have intended NOLOGIN owners,
pg_catalog search paths and no PUBLIC EXECUTE. Tables belong to neondb_owner,
not executor/reconciler. Reconciler capability is NOLOGIN: no deployed independent
service identity is demonstrated. The new effective ACL SELECT executed under
the actual restricted executor login and correctly returned FALSE against legacy
grants. **No SQL migration, grant change or attack write was executed.**

## October 29 candidate and operating procedure

**BLOCKED / NO TRADE; exact quantity, entry, stop and risk are not approved.**
Current completed-bar research (October 27 bars) ranks XOM (0.19199, close 179.88)
and TSLA (0.18171, close 407.51). These are research observations, not selected
orders. There is no current open-market executable quote or October 28 completed
session evidence for October 29. Do not size a trade from these closing prices.

After separately approved security integration/deployment and real runtime
acceptance, at the target open independently recheck calendar, current baseline,
contribution-adjusted full equity/cash-flow history, risk limits, fresh completed
signal, quote, cash/buying power, no exposure/order/unresolved intent, watchdog,
control flags and exact owner-approved terms. Present ONE exact BUY LIMIT/OTO
proposal (or NO TRADE), including whole shares, capped entry, stop, 1% budget,
planned stop-distance loss and cash impact. Stop gaps can exceed planned loss.
Approval must be exact and fresh; no previous-evening proposal is executable.

The current DAY OTO request does not independently specify a GTC child. Actual
overnight lifecycle remains unproved; fail the readiness gate rather than change
order semantics or add an unapproved replacement/cancel path. See official
[Alpaca order documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca).

For UNKNOWN/partial/missing/rejected/canceled protection or failed supervision:
block new submissions, send a critical alert, use broker GETs by client ID and
durable terms, preserve evidence, and have the owner inspect the PAPER dashboard.
Never retry, auto-cancel, replace, flatten, toggle controls or create a second
entry as an improvised recovery. Emergency broker action requires its own approval.

Remaining acceptance: authorized isolated rehearsal; independent review; coordinated
PR38/41 merge/deployment approval; distinct trusted reconciler credentials and
supervision; genuine GTC/partial/exit/restart lifecycle; complete audited equity and
cash-flow evidence; fresh external receiver and market-open monitoring acceptance;
then separate explicit submission-control and exact first-order authorization.


## Verified session update, October 28, 2026

Venloud authorized eligible integration, isolated migrations, and verified cleanup
for this session. This supersedes the earlier proposed new-branch rehearsal:
existing child `br-misty-dawn-b5dbnr90` was reused after baseline inspection.
Production was not migrated. Its legacy executor status/audit grants remain a
release blocker. Independent security review and a verified coordinated runtime
rollout/rollback remain prerequisites to production cutover.

Actual restricted-login acceptance passed on the reused isolated branch: five
executor forgery attempts denied with SQLSTATE 42501, atomic reservation and
uniqueness, independently committed single-use authorization receipt, real
cross-connection control lock, status/audit rollback and equity-writer rollback.
Four-backend reservation concurrency passed (one winner, three daily-uniqueness
denials). Final isolated controls were kill ON, submissions OFF, zero synthetic
intents and audits. No broker operations occurred. Private catalog snapshots,
migration hashes and complete sanitized harness outputs are preserved in Vandam.

The authorization receipt now survives rollback of the final lock transaction;
replay is denied by a partial unique audit index. A broker acknowledgement still
does not establish filled quantity or protective-stop coverage. No trade is
ready: actual overnight stop lifecycle, trusted deployed reconciler, fresh
market-open monitoring and exact owner-approved order remain required.


## Phase7 independent engineering review and residual release gates

A separate non-author agent reviewed baseline2c1e56e and subsequent repairs.
The requested independent AI engineering review is complete; this is not human
signoff or permission to trade. The same authorization connection is checked
again after final broker reads, then approval/quote/deadline freshness is checked
after that round trip. Actual isolated PostgreSQL backend termination was
observed before the send boundary, and the durable consumption receipt survived.
A backend can still disappear after its final acknowledgement and before the
separate broker POST. That distributed race is mitigated, not atomically solved;
order submission remains hard-disabled pending an explicitly reviewed release
policy and all other gates. No SQL or grants changed in production.

Monitor reconciliation now rejects expired, malformed and naive stop-expiry
evidence. Post-fill confirmation already enforced expiry. Genuine overnight
broker lifecycle remains unproved; no real order was used to validate it.

Stable runtime received only reviewed monitoring files (supervisor loop,
external-liveness client, alert module, reconciliation, Windows launcher), with
hash manifest and rollback archive in Vandam. Collector files and all running
processes were preserved. Repeated actual diagnostics left the Neon lease
identical, sent one critical failure alert and suppressed its duplicate.
The running continuous process retains its previously imported code.

External diagnostic GET currently returns404 because the new Worker route has
not been deployed; Cloudflare deployment credentials/session access are not
available in this environment. Original historical HTTP_4XX cannot be recovered
as an exact status. Closed-flat offhours intentionally skips Neon pulses, so the
stored failed/stale lease cannot certify continuous runtime health. Production
cutover remains blocked on external deployment and verified runtime rollout,
including compatible opening-equity permissions and restricted service identity.
