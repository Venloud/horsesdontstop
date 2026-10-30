# Phase 5.8, restricted PAPER journal cutover (DRAFT, NOT DEPLOYED)

**Owner goal:** resolve the real executor journal-status forgery blocker and
advance first controlled PAPER order readiness. No trade release is authorized.
**State:** code and SQL staged on PR #41, needs isolated Postgres acceptance
and independent security review. This file is not a deployment approval.

## Why the cutover MUST depend on PR #38

The legacy executor has BOTH direct UPDATE to `paper_intents.status` /
`broker_order_id` and raw `INSERT` on `paper_intents`.
Revoking only UPDATE is **insufficient**: a direct INSERT supplying `status
='filled'` and a fabricated broker ID could impersonate a fill.

The existing independent PR #38 `sql/paper_reservation_control_guard_review_only.sql`
provides the atomic `public.paper_reserve_intent_if_enabled(...)` function,
unique trading-day constraint, and removes raw executor intent INSERT.
PR #41 Phase 5.5 PREPARE supplies independent status functions and identities.

**Required review and deployment ordering:**
1. Independently approve PR #38's reservation migration and verify
   database lock, uniqueness, restricted-role behavior on a fresh test branch.
2. Install PR #41's status PREPARE (with approved PostgreSQL privileges).
3. Make source/runtime and narrow role credentials ready before cutover.
4. Install `sql/paper_status_executor_cutover_review_only.sql` as
   `neondb_owner` **only when separately authorized**. It refuses to run
   without PR38's guard/unique index, PR41's two status functions, safe
   role inheritance, no raw intent INSERT and preservation of equity INSERT.
5. Restart/revalidate only the explicitly coordinated Windows worker after
   matching source and database are ready. Do **not** silently break
   the existing working equity collector by deploying SQL alone.
6. Verify full actual PostgreSQL denied/allowed operations before any merge,
   production deployment or broker release. This is NOT a rollout script.

PR #38 and PR #41 modify `bot/paper_execution_journal.py` and
`tests/test_paper_execution_journal.py`. PR #41 includes the relevant
PR38 guarded-reservation behavior rather than retaining the insecure main
INSERT path. Reconcile their diffs carefully; no blind auto-merge or
conflict-resolution by dropping either safety design. PR38's separate
concurrency regression scripts/migration are not duplicated by PR41.

## Implementation on PR #41

- `bot/paper_execution_journal.py`: hard post-cutover executor attestation
  checks raw INSERT, status/broker-ID/update timestamp UPDATE, audit INSERT,
  admin/membership and schema/database CREATE, privileged function grants,
  reservation function and UNKNOWN function, plus continued equity INSERT.
  `reserve_if_enabled()` uses PR38's SECURITY DEFINER reservation function;
  it never issues a direct INSERT or FOR SHARE under executor credentials.
- `bot/paper_store.py`: disable legacy direct `reserve_intent()` and
  `record_observed_state()` paths before accessing DB.
- `bot/paper_supervised_execution.py`: broker POST acknowledgment can no
  longer cause the executor to assert `submitted`. It records `unknown`
  via the narrow definer function; independent GET-only reconciliation must
  attest submitted/accepted/filled. Outcome remains one-POST only with no
  retries after error, timeout or ACK-with-DB-failure. Release constant stays
  `False` and no CLI/schedule invokes this gateway.
- `sql/paper_status_executor_cutover_review_only.sql`: transactional,
  review-only pre/post grant checks, REVOKE direct intent/audit privileges
  and execution/role ownership controls. No writes to `paper_control`.
- Updated fake and lexical tests: no direct reserve/status functions or
  broker ACK self-attestation; permission attestation refuses each unsafe
  role bit; cutover migration preconditions and revocations are present.

## Concrete isolated Neon acceptance test to run AFTER approval

**Do NOT run against production.** Use a NEW controlled test child of the
previous disposable branch, preserve existing evidence branch
`br-red-hall-b5h60v04` and PR #38's prior test branches. Synthetic
intents only, kill ON, submissions OFF, and privileged test role
selection only within authorized isolated branch.

A. First run PR38 migration on the clone with transaction/unique-index
   tests. Confirm restricted executor raw INSERT and guard EXECUTE denied,
   but full reservation function EXECUTE granted.
B. Apply PR41 PREPARE independently. Use a *separate* non-executor
   simulated reconciler role for GET-less synthetic status tests.
C. Apply PR41 CUTOVER. Demonstrate that actual restricted executor can
   no longer (i) INSERT a filled paper intent, (ii) UPDATE status,
   (iii) UPDATE broker_order_id, (iv) INSERT a fake audit event,
   (v) SET ROLE/EXECUTE independent reconciler.
D. Verify authorized executor `reserved -> unknown` atomically logs one
   corresponding audit; independent status role can progress an actual
   synthetic reserved/unknown intent using expected-state CAS, and
   cannot raw UPDATE or forge independent audit.
E. Verify duplicate, stale, backward, changed broker ID, terminal,
   absent identity and forced rollback with actual role credentials.
F. Verify role permissions and snapshot-writer compatibility without
   fabricating market-open equity. The existing PC collector must remain
   working from current production deployment.
G. Read-only production check: kill ON, submissions OFF, zero intents,
   unmodified existing opening-equity samples. **DO NOT switch trading on.**

A NOLOGIN reconciler membership adopted with `SET LOCAL ROLE` inside
isolated testing is *not* evidence of a separately authenticated deployed
service. A real standalone reconciler credential, genuine authenticated
Alpaca PAPER GET evidence, long-lived protective stop verification,
restart/watchdog and exact owner order approval are separate launch gates.

**Status semantics:** The executor cannot know the full broker truth from
a single POST acknowledgment. An acknowledged order is durably marked
`unknown` until independent reconciliation. A missing/failed marker
causes a `TradeBlocked` with a read-only broker lookup, never resubmission.

**WHY:** raw executor journal grants and raw INSERT permit fabricated
trading records. **HOW:** PR38 guard + PR41 migration + separate journal
writer identities + restricted SQL functions and offline regressions.
**DID IT WORK?** Mark real isolated cutover and role-denial tests
UNVERIFIED until executed with fresh owner permission; do not copy Phase
5.7 PREPARE results as if they prove Phase 5.8 CUTOVER.
