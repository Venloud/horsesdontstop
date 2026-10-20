# Vendored Trader Skill, MIT, source-pinned

**Integrated:** 2026-10-20. Upstream repository:
https://github.com/Jakeschincariol/trader-skill

**Exact upstream tree/commit:** `5189e79b9a480755a3733ac9713f5353981dbe18`.
This directory contains unmodified upstream Python scripts and their
original Claude SKILL.md descriptions for **six requested skills**, plus
offline source fixtures and upstream example JSON. The original MIT LICENSE
(including required copyright attribution) is preserved alongside them.

## Original files copied into this tree

High priority:
- `trader-analyst/facts.py`, `SKILL.md`
- `trader-screener/screen.py`, `SKILL.md`, rules example
- `trader-news/news.py`, `SKILL.md`
- `trader-risk/check.py`, `SKILL.md`

Medium priority:
- `trader-alerts/alerts.py`, `SKILL.md`, alerts example
- `trader-budget/size.py`, `SKILL.md`, config and targets examples

Source-derived test fixtures come from upstream `tests/fixtures/`.
Some source CLI commands fetch Yahoo, SEC, or Stooq data when run
**directly**; these scripts are not a trusted automatic order source and
were not connected to any scheduled broker job.

## How HorsesDontStop consumes this code

`python -m bot.trader_skill_research --capability <name> --input FILE.json`

The wrapper imports only the six allowlisted **pure data functions**
from these source scripts. It supplies an explicit snapshot input and
produces a `research_only` result with
`paper_execution_authorized=false`, `authoritative_trade_gate=false`,
and `broker_orders_submitted=0`.

Additionally, six project-local Claude skills at
`.claude/skills/hds-trader-*/SKILL.md` each direct Claude to this
non-executing wrapper rather than to the upstream generic agent chain.
No new API keys, Claude connector permissions or broker endpoint
connections are required for offline mode.

**NOT IMPORTED:** `trader-broker/ticket.py`, `trader-copycat`,
`trader-pelosi`, `trader-tax`, `trader-rebalance`.
Those five were reviewed, not installed. No order connector, approval
log, direct `trader/orders.json` feed, or trade submission module was
copied into the repo.

## License and future update procedure

Keep this entire `LICENSE` and the upstream copyright notice when
redistributing any portion. For future upgrades, compare the pinned
Git tree and the upstream new revision, review each changed line for
network access, schema drift, command execution or order writes, and
rerun unit and integration tests. Do not auto-update upstream on
deployment. Vendored code is third-party, not a promise of reliability.
Do not use scraped/delayed quotes to place trades.

Canonical engineering decision and 21-repository documentation audit:
`docs/TRADER_SKILL_INTEGRATION_20261020.md`.
