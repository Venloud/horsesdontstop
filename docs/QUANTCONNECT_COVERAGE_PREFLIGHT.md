# QuantConnect coverage preflight v2, 2026-10-15

**Status:** code and offline synthetic tests only. The actual free-cloud
coverage batch has **NOT** run in QuantConnect. Do not infer data
availability or permission to score from GitHub CI.

1. In the owner's existing QuantConnect FREE cloud
   `HorsesDontStop-Research/research.ipynb`, paste the complete
   `research/quantconnect/phase2_coverage_batch.py` file into one
   Python notebook cell and execute once.
2. Preserve its entire `QC_COVERAGE_V2` text output. It contains
   **dates/counts/statuses only**, never raw prices. Check the vendor's
   derived-results sharing terms before publishing even these summaries.
3. Compare `common_etf_coverage` for `original_12` and the
   **unapproved draft** `proposed_10_unapproved`. Ten contiguous
   full calendar years of session overlap is necessary, but never
   proves point-in-time membership, delisting completeness, data
   normalization authenticity, license rights, or a market edge.

Historical bars now explicitly request `fill_forward=False` both in the
subscription and the history query. QuantConnect's documented default
would otherwise forward-fill, potentially disguising missing sessions.
A verified NYSE trading calendar is still needed to prove SPY itself
is complete, and normalization is still provider-reported rather than
independently audited.

The same cloud cell now probes `add_universe` plus
`universe_history` with a contemporaneous dollar-volume ranking for
three **short, read-only** historical samples: June 2012, June 2016
and June 2022. Those records test whether historical membership
data can be accessed without today's survivor watchlist. A result
with selected candidates is **not** proof of a full ten-year
survivorship-aware universe, or verification that every delisting
is covered. Absent methods or failed queries are recorded explicitly.

No silent fallback to RAW/ADJUSTED if TOTAL_RETURN addition fails.
The batch freezes `1998-01-01 <= session < 2026-10-01` and treats
2026 as partial, checks sampled delistings/splits/dividends, and emits
sanitized error **types only**. A failed symbol is not assumed delisted.
All QC output remains unverified until the actual cloud cell runs.

Amendment 2 is marked DRAFT and the 12-ETF source constant is
**unchanged**. This PR is not an amendment approval, no scored Phase 3
runs, no broker/Neon calls, and no trading enabled.
