# Phase 5.4, PAPER journal status-integrity privilege boundary

**State: confirmed vulnerability, scoped design review only, NOT a deployed hardening.**
This document is intentionally in a **separate draft PR** and introduces **no production migrations, executable broker path, status-function grants, or changed privileges**. PR #38 continues to concern atomic reservation, kill-switch locking and narrow intent insertion.

## Scope and evidence

At PR #38 head `b3a7454d4394600e6185b1075cf15b94c7710f3d`, the actual PostgreSQL role `hds_paper_executor` has direct column-scoped UPDATE on `public.paper_intents.status`, `broker_order_id` and `updated_at`, plus direct INSERT on `public.paper_audit_events`. Its direct paper-intent INSERT and paper-control UPDATE are denied. PostgreSQL's status CHECK allows enumerated values (including `filled`) but does not verify state transitions or evidence from Alpaca. There are no user-defined intent triggers enforcing transitions or audit atomicity.

Both production pre-migration baseline and the clean migrated child branch were inspected read-only and had the same status-write permissions. Hence this weakness **predates PR #38**. A Python caller in `PostgresPaperJournal.record_observed_state()` uses `transition_allowed()` followed by direct `UPDATE` and a separate direct `INSERT` into the audit table within one transaction; `PaperExecutorJournal.mark_uncertain()` also directly updates state and inserts audit. `PaperExecutorJournal.assert_executor_permissions()` expects those rights. PostgreSQL credentials can be used independently of these Python routines.

### Reproducible proof, disposable Neon branch only

**Project:** `misty-shadow-43891652`.
**Reproducer child:** `hds-phase54-status-integrity-probe-20261026`, branch ID `br-small-morning-b5n2xw3c`, cloned from pre-existing isolated Phase 5.1 test branch `br-rapid-glitter-b53xh7rb`, **not** production. It contains two cloned synthetic intent records, initially both `reserved`, with no audit events; switch kill ON and submissions OFF.

Using `hds_paper_executor`, three test-only atomic Neon transactions were attempted; each was deliberately forced to abort via `SELECT 1/0` after a successful operation inside a PL/pgSQL DO block:
1. Directly changed cloned `reserved` to `filled` and assigned `broker_order_id='FABRICATED-NEVER-SENT'` without any broker access.
2. Directly inserted a fabricated `paper_audit_events` row claiming broker activity.
3. Directly changed `reserved -> filled -> reserved` without broker verification, demonstrating that a terminal state is not immutable in SQL.

All three reached the forced divide-by-zero rather than a permission or constraint error. Subsequent independent SELECT confirmed the sample intent remained `reserved` with NULL broker ID, **two synthetic records unchanged**, audit count **0**, kill ON and submissions OFF. **Nothing persisted**; original Phase 5.1 branch was not modified. These are valid capability demonstrations, not proofs that broker fraud actually occurred or that a deployed bot was ever compromised.

Client-order-id primary key and the PR #38 unique-trading-day index continue to enforce uniqueness of their key fields; they do **not** prevent unauthorized mutations of non-key intent status or fabricated audit events. A breach of executor credentials therefore permits a false journal state even when order-submission controls remain disabled.

**Threat severity:** MEDIUM under currently disabled/staged environment, **release-blocking before the journal can be trusted as broker truth**. The likelihood of credential compromise is not quantified. This is a *pre-existing status-integrity architecture gap*, not a newly introduced reservation migration regression. PR #38 should not be broadened or quietly merged as if all journal evidence were authenticated.

## Proposed security design, to implement in a separate later reviewed change

The correct mitigation involves **a distinct trust boundary for broker reconciliation**, not merely validating a caller-supplied string inside a PostgreSQL function.

1. **Separate identities:** Keep the already scoped `hds_paper_executor` for atomic reservation and equity snapshots. Introduce a separately provisioned, strictly authenticated broker-reconciliation principal with independent credentials that are never distributed to the reservation worker. Prefer a NOLOGIN function-owner role for narrow SECURITY DEFINER routines; only the trusted reconciler receives EXECUTE on broker-state transitions. A role with both executor and reconciler credentials defeats separation.
2. **Database-enforced transitions:** Implement an atomic function taking client ID, expected old state and newly reconciled status, locking the row `FOR UPDATE`, verifying an explicit transition graph, and refusing terminal-state rewrites, client-ID mismatches, absent intents and stale state. Record audit and transition in the **same transaction**. Pin `search_path`, schema-qualify objects, REVOKE PUBLIC EXECUTE atomically, deny direct executor `UPDATE` on status/broker order ID/update timestamp and direct audit INSERT, and test the actual role grants, not just SQL strings.
3. **Conservative uncertain-submit flow:** Implement a narrowly scoped, atomic `reserved -> unknown` function usable by executor solely after uncertainty (no `filled` or `accepted` grant). Record the audit in that function. Do not allow an uncertain outcome to automatically authorize any new submit.
4. **Broker evidence and provenance:** PostgreSQL cannot itself establish that a claimed `filled` state came from Alpaca. Only the trusted reconciler should poll the PAPER endpoint, verify actual account, symbol, client ID, broker order ID, side, quantity, status, OTO parent/stop legs and timestamps, and then request the database transition. A database function supplied arbitrary strings by a compromised reconciler is not proof of external evidence. Use separate credentials, protected deployment and audit provenance where appropriate; still treat broker/reconciler compromise as a residual risk.
5. **Safe staged rollout, never an automatic SQL deployment:** Introduce new functions and reconciler identity on an isolated Neon clone, add denial tests against executor and other roles, update Python journal caller contracts (especially `mark_uncertain`, `record_observed_state` and `assert_executor_permissions`), verify opening-equity still works, and *only after code and all roles are ready* revoke legacy column UPDATE/direct audit INSERT. Otherwise journal operations would break on permission errors. Test both pre/post-migration compatibility, crash/timeout rollback, concurrent state transitions and all existing supervisor paths. Keep PR #38 independent and unchanged.
6. **Broker submission authorization is a separate milestone:** `submit_paper_once_supervised` still needs fresh kill-switch and authorization after reservation commit, plus independent protective-stop verification, reconnect/restart recovery and a healthy always-on supervisor. State-function hardening does not solve that.

### Required new acceptance cases

- Executor direct `UPDATE status='filled'` and `broker_order_id` **denied 42501**. Direct audit `INSERT` denied.
- Executor cannot directly invoke reconciliation transition function or call private guard.
- Executor's narrowly scoped `mark_unknown` operation succeeds only from `reserved`, with atomic audit; replay fails without new audit.
- Trusted reconciler's database function enforces legal transitions `reserved -> submitted -> accepted/filled`, fail-closes on stale/concurrent updates, cannot change terminal state or forge a missing client ID, audit always corresponds one-for-one to transitions.
- Replay, duplicate broker ID, mismatched order, unauthenticated caller and false broker-evidence edge cases denied; demonstrate that external broker proof must be checked **outside** the database and acknowledge its trust assumptions.
- Production-equivalent opening-equity recording and PR #38 single-day reservation both remain working in a **new dedicated isolated branch**; unavailable network evidence means tests are NOT marked passed.
- Zero mutation of production, preservation of both existing isolated Neon branches; cleanup and kill-switch recheck after all tests.

### Why this is a separate draft PR

Implementing the design requires coordinated role, SQL, reconciliation-service credential isolation, journal caller refactoring and safe rollout order. Simply REVOKE-ing direct privileges in PR #38 breaks existing Python status and audit writes. A privileged `SECURITY DEFINER` function callable by the same executor, accepting any broker status supplied by that executor, **would not solve broker-evidence authenticity**. This document proposes a reviewable boundary without mislabeling it implemented.

## PR #38 decision

Reservation-only migration is separately supported by prior live concurrency tests and a clean-branch migration rehearsal. This specific vulnerability predates PR #38 and **does not itself disprove the atomic reservation / one-per-day / kill-switch transaction invariants**. For PR #38, a technically plausible disposition is to keep status integrity as a **strict future broker-launch gate**, conditional on an independent second-party reviewer explicitly accepting that scope. It is **not** grounds to authorize trading or automatically merge PR #38. There are still no GitHub review submissions verifying independent human sign-off as of this assessment.

**PR #38 recommendation: NEEDS REVIEW, remains DRAFT and UNMERGED.** Neither this document nor green CI can substitute for independent approval.

**Estimated engineering completion: hold 86%.** No new production hardening or release capability has been completed, and progress estimates are not certifications.

**One next action:** obtain a separate security review of this proposed role/evidence architecture and explicit scope disposition for PR #38 before any owner merge or production migration decision.
