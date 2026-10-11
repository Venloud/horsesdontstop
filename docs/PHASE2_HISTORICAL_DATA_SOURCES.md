# Phase 2: historical market-data sources and licensing review

**Researched:** 2026-10-11. **Research only. NO new paid subscriptions. NO trading.**  
**Evidence mode:** sources below are vendor documentation, not a verified
horsesdontstop dataset. A separate GitHub Actions GET-only coverage
sample is recorded in the section below. No raw market data has been
published in the public repository.

## Decision matrix

| Provider / mode | 2022 coverage and ~10y potential | Delistings / corporate actions | Cost and legal / workflow constraint | Phase 2 decision |
|---|---|---|---|---|
| **Alpaca Basic / IEX history** | Vendor documents equities historical from 2016, free Basic tier, 200 requests/min; Oct 2026 is at the edge of ten years. Need actual sample verification; cannot assume every symbol existed then | API `adjustment=all` can account for split/dividend/spinoff price changes; `asof` resolves certain symbol renames. Neither is a complete historic universe-membership/delisting database. IEX-only volume is incomplete versus SIP. Separate corporate-action API exists, but notes possible publication delays | Existing credentials already stored as GitHub Secrets. Download/export/redistribution rights not established for public raw datasets; publish summary counts only | **Free diagnostic/development input, NOT unbiased Phase 3 scored source by itself** |
| **QuantConnect Free CLOUD research/backtesting** | Equity market history and free cloud datasets include daily data going back to 1998 in documented source coverage; free tier supports one research and one backtest node | QuantConnect equity security master includes splits, dividends, delistings and ticker mappings for ~27,500 U.S. stocks | **Free cloud analysis, NOT free local bulk export**. Vendor prohibits data extraction/redistribution outside normal licensed downloads. LOCAL data requires paid security master and price data: vendor currently lists US Equity Security Master **$600/year** (Quant Researcher) and US Equities daily per-file **$1/ticker**, other tiers/bulk more. Do not buy or scrape. | **Best zero-spend option for a SEPARATE, in-cloud independent historical benchmark**, if owner connects an account; not a drop-in legal dataset for our GitHub Python pipeline |
| **Nasdaq Data Link SHARADAR/SEP** | EOD equity prices and company/reference/corporate-action data since 1998, 21,000+ names | Delist reasons, ticker change/split/dividend events | **Premium dataset**, only a small 2018 sample is advertised free. Requires subscription and redistribution/licensing review. | **Strong paid-data candidate later, not within current $0 budget** |
| **Other unverified community sources / Yahoo/Stooq scrapers** | Historical prices may exist, but exact symbol history, availability and licensing are not confirmed by this project | Not demonstrated as survivorship-free or accurately adjusted | Avoid depending on unofficial scraping or copying third-party prices into a public repo without terms clearance | **Not approved as scored feed** |

### Primary vendor documentation, checked 2026-10-11

- Alpaca historical tier description (Basic free / IEX / since 2016):
  https://docs.alpaca.markets/us/docs/about-market-data-api
- Alpaca historical bars REST docs (**inclusive** `start` and **inclusive**
  `end`; pagination, `adjustment=all`, `asof` mapping):
  https://docs.alpaca.markets/us/reference/stockbars
- Alpaca corporate actions API and availability warnings:
  https://docs.alpaca.markets/us/reference/corporateactions-1
- QuantConnect Free research/backtest node quotas:
  https://www.quantconnect.com/docs/v2/cloud-platform/organizations/tier-features
- QuantConnect AlgoSeek 1998 and survivorship-free daily cloud data:
  https://www.quantconnect.com/docs/v2/writing-algorithms/datasets/algoseek/us-equities
- QuantConnect US equity corporate action / delisting security master:
  https://www.quantconnect.com/docs/v2/writing-algorithms/datasets/quantconnect/us-equity-security-master
- QuantConnect licensing: most cloud historical prices may not be
  redistributed, local download license separate:
  https://www.quantconnect.com/docs/v2/cloud-platform/datasets/licensing
  https://www.quantconnect.com/docs/v2/cloud-platform/security-and-ip
  https://www.quantconnect.com/docs/v2/lean-cli/datasets/quantconnect/us-equity
- Nasdaq Data Link Sharadar equity prices:
  https://data.nasdaq.com/databases/SEP/documentation

## Existing-paper-key read-only Alpaca sample probe

Python: `bot/phase2_alpaca_coverage_probe.py`.  
GitHub workflow: `.github/workflows/phase2-marketdata-coverage.yml`.  
Only four fixed, tiny daily **IEX** calendar windows are queried:
`SPY/AAPL/XLK` in Oct 2016, June 2022, September 2026 and `FRC`
in June 2022 (an illustrative once-listed historical ticker).
Requests are GETs against `data.alpaca.markets` using existing
**PAPER market-data keys**; no Alpaca trading client, broker submit,
Neon, API tokens in logs, or exported OHLCV datasets. Prints only
per-symbol row counts plus first/last available calendar dates.

**Initial observation:** Oct 2016 request responded successfully but
contained **zero IEX daily bars** for all three sample symbols.
This does *not* prove Alpaca Basic has no data from 2016 for every
symbol/feed; it means this account+IEX+time-window combination did not
produce the needed historical rows. Treat as a material 10-year
coverage concern.

**IMPORTANT correction:** Early development probe incorrectly assumed
an *exclusive* `end` date, causing valid June 2022 inclusive
end-date bars to be rejected as out-of-window. Alpaca explicitly
documents an **inclusive** `end` date. Corrected and unit-tested;
never cite that earlier failure as proof 2022 coverage is absent.
Newest evidence must be read from GitHub Actions after the fix.

**Confirmed successful sanitized GitHub GET-only run:**
https://github.com/Venloud/horsesdontstop/actions/runs/38126272354

| Requested source window (start/end inclusive) | SPY | AAPL | XLK | FRC |
|---|---:|---:|---:|---:|
| 2016-10-01 through 2016-11-01, raw IEX daily | 0 | 0 | 0 | Not queried |
| 2022-06-01 through 2022-07-01, raw IEX daily | 22 | 22 | 22 | 22 (separately queried) |
| 2026-09-01 through 2026-10-01, raw IEX daily | 22 | 22 | 22 | Not queried |

All observed 2022 series had first/last bar
**2022-06-01 / 2022-07-01**; the recent samples had
**2026-09-01 / 2026-10-01**. The successful workflow
ran **15 focused offline tests first**, then used the
already configured Alpaca PAPER market-data credentials.
It retrieved bar counts/dates but did **not** publish prices,
submit brokerage orders or write Neon journal records.

**Interpretation:** The 2022 and recent periods are genuinely
available under this account's IEX entitlement; the observed
2016 gap means the free IEX feed **does not yet demonstrate
our approved continuous ten-year history**, even though
Alpaca advertises historical data since 2016 in general.
FRC 2022 availability is one limited historical-ticker
sanity check, **NOT** evidence that a delisting-complete
historical universe is present. Do not extrapolate
full-calendar completeness or SIP market-volume integrity
from these four short windows.

**Coverage probe is NOT a scoring or ingestion tool.** One month with
daily bars is not proof that entire ten-year history, delistings,
historical sector membership, accurate dividends or total-return
benchmark is available. FRC lack of bars could reflect symbol
mapping, feed/trading history or other unknowns; neither presence
nor absence certifies no survivorship bias.

## Concrete $0 decision and next experiment

1. Complete GET-only Alpaca coverage sample and record actual outcomes
   with a run URL. Do NOT assume 2016 historical IEX coverage without
   rows. Do not keep resampling every day or change the research
   parameter grid based on coverage availability.
2. **Prioritize a separate QuantConnect Free CLOUD investigation**,
   not a data export. Its security master could allow point-in-time
   symbol/delisting-aware **independent** benchmark backtests across
   1998–2026 if the desired assets and research quotas are supported.
   Connecting/signing up requires explicit owner action; do not
   copy Alpaca or Neon API keys to QuantConnect. Respect free-tier
   limits and vendor extraction restrictions.
3. Until we can license or validate a trustworthy local membership
   and corporate-action history, `research/phase2_manifest.json`
   must remain `status=draft`; Phase 3 **scored holdout is BLOCKED**.
   Synthetic strategy/harness unit tests and read-only research
   infrastructure work may continue.
4. If free cloud research is insufficient, compare **paid** datasets
   transparently with the user before any card/subscription. Current
   approval for free hosting does not authorize buying market data.

**Never turn data coverage metadata into a claim that an actual
paper/live strategy has earned money.** The original 80 trial ledger
remains marked `legacy_exploratory_preinspected`. Production Neon
kill switch TRUE, submissions FALSE and hard-coded order release
FALSE remain untouched.
