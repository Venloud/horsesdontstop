# November 6 PAPER quote rejection

## Finding

The 2026-11-06 09:46:07.878 ET executor receipt passed all six supervisor
checks and stopped during live quote/risk evaluation. It did not reserve an
intent or send a broker order. The original receipt did not preserve the
candidate symbol or quote payload, so the exact response consumed by the
executor cannot be recovered.

A read-only reconstruction used the same completed-session momentum candidate
selection, the receipt timestamp, the November 5 completed session, and
authenticated Alpaca PAPER IEX quote history. It identified WMT as the top
candidate (score 0.40170). The nearest archived IEX quote was timestamped
2026-11-06 14:46:07.501112 UTC, 0.377 seconds before the receipt: bid $121.00,
ask $124.82, displayed sizes 100 x 100, spread 310.80 bps. The configured
spread ceiling was 75 bps. This is strong evidence that the rejection was
appropriate, but the archived quote is not claimed to be the exact latest
quote response returned by the original executor.

The candidate selector also returned KO, PFE, AAPL, F, JPM, MSFT, and XOM.
Their nearest reconstructed IEX spreads were 11.68, 6.87, 30.33, 7.99,
689.66, 3.20, and 329.21 bps respectively. None had an ask at or below the
$5 whole-share purchase cap: the lowest ask was F at $12.52. Thus, even the
tight-spread alternatives did not qualify for this bounded pilot. The spread
limit and purchase cap were not relaxed.

## Repair

The live executor now records a sanitized evaluation for every strategy-ranked
candidate: symbol and strategy score, quote timestamp and age, bid/ask and
sizes, calculated spread, configured spread ceiling, and a fixed safe
eligibility reason. Quote-local failures (including excessive spread and an
unaffordable whole share) skip to the next strategy candidate. Systemic,
account, market-session, or evidence failures still fail closed. A durable
reservation remains limited to the first candidate that clears every existing
gate. The runtime receipt records evaluated candidates without credentials,
account identifiers, or raw service errors.

## Verification and limits

Regression coverage demonstrates that a 310.8 bps quote is rejected against
the unchanged 75 bps limit, a tight-spread but $12.52 share is unaffordable
under the unchanged $5 cap, and a later eligible synthetic candidate can be
prepared after an earlier wide-spread candidate is rejected. Synthetic tests
do not prove that a live Monday candidate will qualify. Re-evaluate fresh
market-session signals, quotes, account state, controls, and risk gates before
any proposal. No reservation or order is authorized by this evidence.
