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

No silent fallback to RAW/ADJUSTED if TOTAL_RETURN addition fails.
The batch freezes `1998-01-01 <= session < 2026-10-01` and treats
2026 as partial, checks sampled delistings/splits/dividends, and emits
sanitized error **types only**. A failed symbol is not assumed delisted.
All QC output remains unverified until the actual cloud cell runs.

Amendment 2 is marked DRAFT and the 12-ETF source constant is
**unchanged**. This PR is not an amendment approval, no scored Phase 3
runs, no broker/Neon calls, and no trading enabled.
