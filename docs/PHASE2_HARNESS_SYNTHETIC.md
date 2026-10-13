# Phase 2 harness self-tests, dated 2026-10-13

Preregistration §7, **offline synthetic only**. Frozen parameters remain 30
bps adverse slippage per side and 60 bps per-side stress. No scored market run.

- Synthetic one-share open-to-open probe refuses a decision if any cited
  observation occurs on/after the proposed entry event. Real data-source
  publication-time verification remains separate.
- Flat synthetic random-entry experiments have expected net cost
  `(1 - .003)/(1 + .003) - 1`, about -59.8 bps; this is a
  diagnostic, not proof about actual random market entries.
- Seeded known synthetic +1% gross edge is checked against an
  analytically calibrated 95% sampling band after entry/exit costs.
- Run 38108710763 has 80 archived **output rows**, not the pinned original
  raw IEX market bars. An authentic re-execution to rounding **remains
  BLOCKED** until the original bar snapshot, exact code revision, feed
  semantics and compatible artifact hash are recovered. Inspecting the
  output ledger is not replay evidence.

These tests do not validate Phase 3 profits, capital constraints, stops,
corporate actions, the prospective holdout, or a particular instrument.
Research manifest stays draft. No broker/Neon operations.
