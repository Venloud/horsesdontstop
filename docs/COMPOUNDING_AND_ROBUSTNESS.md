# Compounding and robustness audit

## Compounding: from the very first profitable trade

**There is no $1,000 activation threshold.** The research simulator
begins at $500 and automatically reinvests *realized* gains after every
closed profitable trade. If equity/cash grows from $500 to $520, the
next 1% risk allocation is $5.20 (or $10.40 at 2% risk); if it falls
to $480, the next risk allocation is $4.80 (or $9.60). These are
position risk budgets, NOT required deposits or guaranteed loss limits.

The same shared cash balance limits whole-share purchasing power. No
leverage, automatic deposits, or real-money transactions are enabled.
Losses lower buying power too. Unrealized gains do not increase buying
power in this single-position model until an exit closes the trade.
This is a cash-account approximation, not a broker margin model.

- Profits are **not** withdrawn at $1,000, held aside, or reset.
- The existing **$100/month maximum optional contribution** remains a
  separate owner-controlled deposit policy. Current backtests simulate
  $0 contributions and never attempt to fund the actual account.
- The $10,000 *total equity* goal remains a stretch target. At that
  milestone, a $5,000 withdrawal could only be considered after the
  user's express approval, in a future real-account plan.
- The research journal now includes `entry_cash`, `exit_cash`,
  `risk_budget`, and a `first_1000_equity_day` milestone. It is
  possible for marked equity to briefly exceed $1,000 without a
  realized exit; this milestone is a mark-to-market observation.

## More demanding tests

Use **Actions > Robustness Audit (No Trades) > Run workflow**, or let
the workflow run monthly on the first calendar day at 16:10 UTC.

- Data: 1095 calendar days of split-adjusted Alpaca **IEX-only** daily
  bars, up to 15 handpicked stocks across technology, financials,
  energy, consumer sectors, and healthcare, plus sector ETFs and SPY.
- Four **nonoverlapping ~105-session historical windows** after at
  least 260 earlier sessions to warm indicators.
- Four predeclared strategies × 1%/2% risk × 10/30 bps adverse fill
  slippage × four windows = **64 independent what-if tests**.
- Every experiment starts at $500 **only to make windows comparable**,
  then compounds realized P&L after its very first closed trade.
  The four ending balances MUST NOT be added or represented as the
  growth path of one continuously running account.
- Same one-position, whole-share, shared-$500 rules from the baseline
  portfolio research engine.
- Comparison includes worst daily-close drawdown, return, number of
  trades, and SPY price-only reference. Exit execution has adverse
  slippage, an initial 3% stop and trailing 5% stop, including possible
  loss beyond planned risk on gap-downs.
- Synthetic unit tests verify positive profits increase the next
  trade's cash and risk budget *before $1000*, and losses decrease
  those amounts.

## Interpreting the results

The earlier 2% trend-following holdout profit of $169.56 on a simulated
$500 allocation was a promising observation, **not evidence of a
repeatable edge**. Because its result already influenced which strategy
gets attention, this new robustness run cannot honestly be called a
fresh, untouched out-of-sample test.

Survivorship bias remains. Data is from Alpaca IEX instead of complete
national consolidated prices/volume. Historical splits are adjusted
simplistically; actual historical share conversions and dividend
cashflows are not modeled. OHLC bars omit intraday order sequencing,
liquidity, commissions/other fees, detailed slippage and corporate
actions. Trailing-stop orders do not guarantee fills at stop prices.
Drawdown is measured from daily marked equity, not intraday worst case.

**No trade orders have been enabled** and this workflow has no Alpaca
TradingClient. Persistent data, reliable order management, safe
reconciliation, comprehensive risk controls, and forward paper tests
are still required before any unattended paper orders.

The project is experimental research, not a source of predictable
profits or investment advice.
