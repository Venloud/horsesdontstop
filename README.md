# horsesdontstop
Yon bot AI ki analize mache aksyon an an tan reyèl, fè trading ak lajan vityèl, chèche gwo pwofi pandan l ap limite pèt. Objektif: fè $500 tounen $10,000.

## Development status

Initial Python paper-only foundation added. See [SETUP.md](SETUP.md) for setup and limitations. Run `pip install -r requirements.txt`, copy `.env.example` to `.env` with Alpaca **paper** credentials, then `python -m bot.main --check`. No orders are submitted. Run `pytest` for risk tests.


## Current project status (2026-10-11)

**Alpaca PAPER only. Live IEX stock scanner runs; automatic paper-order
submissions are still DISABLED.** Neon production kill switch ON,
submissions OFF; no actual paper fills or trades.

**[Read the current DONE / TODO checklist](docs/PROJECT_STATUS_CHECKLIST.md)**
for completed milestones, exact tests and remaining blockers. See
[paper launch gates](docs/PAPER_LAUNCH_CHECKLIST.md),
[execution milestone](docs/PAPER_EXECUTOR_NEXT.md) and
[full private Vandam build log](https://github.com/Venloud/claudevandam/blob/main/HORSESDONTSTOP_FULL_BUILD_LOG.md)
(authorized collaborators only).

Do not use the retired GTC MARKET OTO builder; only a current-quote
DAY LIMIT OTO may be considered in a separately supervised and
explicitly authorized future paper test. GitHub cron is not
an always-on trading supervisor.


## Phase 0 strategy preregistration

**Owner-approved research rules, merged in [PR #9](https://github.com/Venloud/horsesdontstop/pull/9):**
[seven strategy candidates, matching actual-deposit benchmark, 15% historical
drawdown limit, independent-trade and long-history thresholds](docs/PREREGISTRATION.md).

[Audit the prior 80 research outcomes](research/TRIAL_LEDGER.csv) and read
[trial-ledger provenance / use rules](docs/TRIAL_LEDGER.md). These are
historical *backtests*, not realized profits. No scored Phase 3 comparison
may run until the Phase 2 data/universe/fold/seed manifest is committed. The Phase 0 candidate and pass rules are frozen.

**Research documentation does not enable PAPER or real-money trading.**


## PAPER supervisor readiness: still disabled

[Read strict overnight stop audit](docs/PAPER_GTC_STOP_READONLY_AUDIT.md),
[staged restricted worker heartbeat / temporary Neon test](docs/PAPER_WORKER_HEARTBEAT_STAGE.md)
and [free-hosting limitations](docs/PAPER_WORKER_HOSTING_DECISION.md).

This safety branch only adds **read-only verification**, a staged
restricted heartbeat role/table/lease, offline tests and a
**non-deployed, read-only** worker loop. The SQL migration has
**NOT** been applied to Neon production and the heartbeat role/DSN
is **not configured**. The loop cannot send an order. Running
a worker somewhere does not prove broker child-stop GTC coverage
or constitute independent notification monitoring. The code
release constant stays FALSE and the two Neon production
submission switches stay **kill ON / submissions OFF**.

## Phase 2: research dataset readiness

[Read the Phase 2 data-integrity plan](docs/PHASE2_DATA_INTEGRITY.md) and [historical-data source comparison](docs/PHASE2_HISTORICAL_DATA_SOURCES.md).
A local offline gate (`python -m bot.phase2_dataset_gate`) checks the
phase-0 frozen research rules, dataset file hashes, source and universe
provenance, untouched chronological holdout, statistical seeds and
matching-deposit SPY reference. The checked-in manifest is
**intentionally incomplete and must fail closed**. No new scored
trading research is authorized before actual historical datasets,
point-in-time membership and synthetic harness checks are verified.
The gate does **not** submit paper orders or real-money orders.

**Read-only source finding:** [Alpaca coverage probe](https://github.com/Venloud/horsesdontstop/actions/runs/38126272354) observed **22 daily IEX bars per sampled ticker in June 2022 and September 2026**, **0 for October 2016** (SPY/AAPL/XLK), and **22 for historical FRC in June 2022**. These short windows do not prove 10-year/delistings/total-return completeness. The data probe is now **manual-only**. The preferred free independent long-horizon option is QuantConnect *cloud* research; local bulk historical downloads require a separate license.


## Additional six Trader Skill research capabilities (staged October 20, 2026)

Six MIT source-pinned Claude/Python capabilities are integrated on the
`integrations/trader-skill-research-20261020` branch: high-priority
analyst, screener, news and advisory risk; medium-priority informational
alerts and whole-share budget sizing. All six are accessible to Claude Code
via `.claude/skills/hds-trader-*/SKILL.md` and to Python through
`bot.trader_skill_research`. No generic broker skill or execution
tool was installed. All outputs are explicitly **research_only**.

See the [complete WHY / HOW / WHEN / WHAT audit and usage examples](
docs/TRADER_SKILL_INTEGRATION_20261020.md), including reviews of the
five excluded/deferred upstream skills and all 21 owner repos.
Original MIT license, source revision, and fixture provenance are
retained under `third_party/trader_skill/`.

Offline check:
```bash
python -m pytest -q tests/test_trader_skill_research.py
python -m bot.trader_skill_research --help
```

**This integration does not change PAPER order submission, strategy
preregistration, ongoing CI schedules, Neon or broker secrets.**
