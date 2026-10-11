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

**Owner-approved research design, pending PR review and merge:**
[seven strategy candidates, matching actual-deposit benchmark, 15% historical
drawdown limit, independent-trade and long-history thresholds](docs/PREREGISTRATION.md).

[Audit the prior 80 research outcomes](research/TRIAL_LEDGER.csv) and read
[trial-ledger provenance / use rules](docs/TRIAL_LEDGER.md). These are
historical *backtests*, not realized profits. No scored Phase 3 comparison
should run before the detailed rules are reviewed and frozen.

**Research documentation does not enable PAPER or real-money trading.**
