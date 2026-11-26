## Purpose: full autonomy on Alpaca PAPER (owner directive 2026-11-26)

HorsesDontStop is a fully autonomous trading bot on Venloud's Alpaca PAPER account. It independently researches opportunities, develops and tests strategies, copies successful traders, executes trades, manages risk and reinvests profits. It opens and manages multiple positions at once and decides itself how many to hold and how much capital to allocate, based on opportunities and available funds. The owner does not approve individual trades.

Do not add restrictions that prevent realistic testing. Safety measures protect against genuine failures (wrong account or live endpoint, leaked credentials, runaway/duplicate orders, unprotected positions, broken data, crashes), not against the bot doing its job. Goal: full autonomy, realistic testing, profitable portfolio growth. Target: beat ~2x a high-yield savings rate (8-9%/yr) and SPY buy-and-hold.


## ECC task guidance (Vandam integration)

Read `.ecc/project.json` for the project-specific skills and restrictions. ECC skills are namespaced `ecc-*` in `.agents/skills` (Codex) and `.claude/skills` (Claude). The shared canonical installation is the sibling `claudevandam/integrations/ecc/`; keep repositories as siblings, or create missing links with its `ecc.py activate --project .` command after cloning.

At task intake run `python3 ../claudevandam/integrations/ecc/ecc.py select --project . --task "<task>"`, then load at most two selected skills. Run the same tool's `doctor --project .` to distinguish discovery from executable readiness. Existing project instructions, runtime source, approval gates and tests take precedence. No automatic observation hooks, separate memory service, provider changes or paid calls are enabled. A skill install is not a product acceptance test.
