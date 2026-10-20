# Trader Skill integration, complete engineering audit (2026-10-20)

**Status: SIX SKILLS INTEGRATED AS RESEARCH ONLY, no deployment or order
authorization.** Work branch:
`integrations/trader-skill-research-20261020`. Main and staged monitoring
PR #23 are untouched. This is the canonical WHY / WHAT / HOW / WHEN / WHERE /
TEST / LIMITATIONS record for the new skills.

## 1. Request and timeline, what changed when

Owner request on 2026-10-20:
1. Integrate four high-priority skills first, then two medium-priority skills.
2. Research the remaining five skills and explain what is wrong with
   replacing the existing broker using `trader-broker`.
3. Check Vandam and other owned GitHub repositories for documentation.
4. Document what, how, when, why and return a full report.

**Executed on 2026-10-20**:
- Audited upstream `Jakeschincariol/trader-skill` at exact commit
  `5189e79b9a480755a3733ac9713f5353981dbe18` and MIT `LICENSE`.
- Inspected canonical `horsesdontstop` docs: current project checklist,
  launch gate, Phase 2 signals, preregistration amendment and research
  restrictions. Inspected private Vandam `HORSESDONTSTOP_PROJECT_HANDOFF.md`,
  `HORSESDONTSTOP_FULL_BUILD_LOG.md`, and historical trading-bot research.
- Audited README-level project scope across all 21 accessible
  `Venloud` repositories. Two README files and one other repo lacked a
  standard README, reported as missing rather than inventing content.
- Created new branch from `main` safety baseline
  `e8fe58d2a9dd5a87213df56b39e8a68607dd9dfb`, not PR #23.
- Vendored original high-priority Python implementations and SKILL.md
  documents **first**, followed by medium-priority scripts, docs, and
  example inputs. Preserved upstream MIT license and commit provenance.
- Added `bot/trader_skill_research.py` for six fully allowlisted, pure
  offline operations with exact `research_only` result contracts.
- Added `tests/test_trader_skill_research.py` with offline SEC/news source
  fixtures and policy tests. Added **six project-local Claude skills**
  at `.claude/skills/hds-trader-*/SKILL.md`. Their entrypoint is the
  HorsesDontStop wrapper, NOT generic upstream order execution.
- An initial CI run before committing test fixtures returned two missing
  fixture errors. Committed those fixtures and reran; the corrected
  first complete integration commit `90ef61ac` passed **652 Python
  tests, 4 skipped, 1 warning** (Actions run
  https://github.com/Venloud/horsesdontstop/actions/runs/39465084793).
  Later documentation and Claude-skill commits are checked separately.
- No live broker, SEC or Yahoo API run was performed; vendor network
  operations were not needed to test the adaptation. No paid account,
  database mutation, secret installation or order submission occurred.

## 2. What each integrated skill actually does

| Priority | Source | Integrated entrypoint | Benefit to HDS | Explicit constraint |
|---|---|---|---|---|
| HIGH | `trader-analyst/facts.py` | `analyst` | SEC XBRL reported revenue, EPS, margins and period comparisons | Reported fundamentals, not earnings-surprise or point-in-time historical source |
| HIGH | `trader-screener/screen.py` | `screener` | Explainable, rule-based candidate filtering on supplied metrics | Missing metrics fail; cannot alter preregistered trading signals or benchmark |
| HIGH | `trader-news/news.py` | `news` | Parse current or archived Yahoo RSS and SEC Atom 8-K XML snapshots | Headlines are leads, event times and primary sources need confirmation |
| HIGH | `trader-risk/check.py` | `risk` | Independent advisory cap, cash-buffer, order-limit, and duplicate simulation | Upstream `PASS` is NOT protective-stop or HDS execution authorization |
| MEDIUM | `trader-alerts/alerts.py` | `alerts` | Informational thresholds, percent moves and notification state | Quotes may be delayed; static 2026/2027 calendar is not execution-grade |
| MEDIUM | `trader-budget/size.py` | `budget` | Hypothetical whole-share allocations with cash buffer and caps | Diversification does NOT measure risk by stop distance or drawdown |

All six are imported as **unmodified upstream Python functions** from
`third_party/trader_skill`, not reimplemented from vague prose.
The original CLI scripts are retained for provenance and optional manual
data collection. The wrapper never starts upstream broker tooling.
The user-visible wrapper returns:
`status=research_only`,
`external_data_verified=false`,
`paper_execution_authorized=false`,
`authoritative_trade_gate=false`,
`broker_orders_submitted=0`,
`vendor_upstream_commit=<pinned SHA>`.

## 3. Runtime design and commands (without live credentials)

A local data producer supplies a JSON **snapshot**.
The bridge is offline/pure and prints JSON to stdout:

```bash
python -m pytest -q tests/test_trader_skill_research.py
python -m bot.trader_skill_research --capability analyst  --input /path/to/analyst.json
python -m bot.trader_skill_research --capability screener --input /path/to/screener.json
python -m bot.trader_skill_research --capability news     --input /path/to/news.json
python -m bot.trader_skill_research --capability risk     --input /path/to/risk.json
python -m bot.trader_skill_research --capability alerts   --input /path/to/alerts.json
python -m bot.trader_skill_research --capability budget   --input /path/to/budget.json
```

Supported request envelopes (minimum shape examples):

**Screener, high:** supply company metrics and explicit rules; no
autogenerated signal or unexplained default:

```json
{
  "companies": [{"ticker":"AAPL","revenue_growth_yoy":12,"operating_margin":25}],
  "rules": {"missing":"fail",
            "rules":[{"metric":"revenue_growth_yoy","op":">=","value":10}]}
}
```

**Analyst, high:** use the real **saved** SEC companyfacts JSON, not
a fabricated ticker-only command:
`{"companyfacts":{<SEC companyfacts object>},"quarters":6}`.
The upstream source CLI can manually fetch EDGAR with an owner-supplied
`SEC_USER_AGENT` (SEC requires identifying contact), but the bridge
does not reach external services or save keys.

**News, high:** `{"ticker":"AAPL","rss_xml":"<rss>...</rss>",
"sec_atom_xml":"<feed>...</feed>"}`. Use authentic saved XML snapshots.
Verify critical filings and article links before asserting an event.

**Risk, high:**
```json
{
  "config": {"paper":true,"allow_margin":false,"allow_short":false,
             "allow_options":false,"cash_buffer_pct":10,
             "max_position_pct":40,"max_order_usd":100},
  "portfolio": {"cash":500,"positions":[]},
  "prices": {"AAPL":100},
  "proposals": [{"symbol":"AAPL","side":"buy","notional":"50",
                 "type":"market","time_in_force":"day"}],
  "date":"2026-10-20"
}
```
Output is an **advisory** with `upstream_advisory.pass/block`, never a
durable journal decision. Protected/advanced `order_class`, stop/take
profit, client order IDs, and extended-hours requests are REFUSED by this
adapter, since they require HDS's original broker and stop gate. It also
requires a real explicitly passed cash/portfolio object, and bans margin,
shorts, live accounts and options.

**Alerts, medium:**
`{"observed_at_utc":"2026-10-20T14:00:00Z",
"rules":[{"id":"watch","type":"above","symbol":"AAPL","price":150}],
"quotes":{"AAPL":{"price":160}},"state":{}}`.
The returned next_state allows deduplication in a caller-controlled
research store. **The clock and quote are informational**. The vendor
calendar covers 2026/2027 only. Do not call this for broker order timing.

**Budget, medium:** supply explicit `budget`, `weights`,
`prices`, and config `{"paper":true,"fractional":false,...}`.
Adapter enforces whole shares and prints
`non_executable_proposals`; no entry into `paper_intents`, and it
does not claim stop-distance risk or a fit with the active strategy.

All input errors produce sanitized output. The bridge does not create
local order files, call brokers, mutate Neon or publish credentials.

### Data acquisition, not automatic deployment

The upstream source `facts.py` and `news.py` can use SEC public APIs
when run explicitly and with a real SEC User-Agent. `alerts.py` can
query unofficial Yahoo or Stooq endpoints. Those are not part of an
always-on hosted HDS trading job. Source dates and availability,
request-rate/terms, licensing and staleness must be validated before
any research dataset is accepted. Only recorded, pre-approved,
point-in-time observations can be used for frozen scoring.

## 4. Why `trader-broker` is NOT the HDS broker executor

`trader-broker/ticket.py` **does not itself send broker HTTP orders**.
It generates plain-language tickets, checks a vendor risk JSON
fingerprint, stores conversational `yes` decisions in a local
`trader/state/broker.json` log and expects a separate Claude/Alpaca MCP
connector to perform the eventual `place_stock_order`. Its plugin
instructions recommend a user approval prompt for each trade.
These are sensible for supervised **manual investing workflows**, but
**not equivalent** to HDS's broker execution and protection contract:

1. **No durable database atomicity.** Local files and chat approval
   history cannot substitute for Postgres-intent journaling,
   compare-and-set state transitions, deterministic broker
   `client_order_id`, duplicate-submission recovery, or cross-worker
   leases after a host crash.
2. **No verified attached protective STOP GTC lifecycle.** The trader
   budget workflow creates generic DAY market purchases. Upstream
   risk `check.py` merely WARNS on advanced/bracket fields;
   it does not independently prove a fully filled entry and
   continuing stop child through close, partial fills, rejected
   stops, stale updates, or broker reconciliation. Alpaca's broker
   own docs document OTO/OCO/bracket differences and constraints:
   https://docs.alpaca.markets/us/docs/orders-at-alpaca .
3. **No complete HDS risk accounting.** Vendor checks cash buffers,
   order size, position fraction, same-day duplicates; they do not
   prove HDS dynamic 1% stop-distance per trade, frozen 3% daily
   drawdown, 15% peak-equity drawdown, daily opening equity,
   cash-flow reconciliation or monitored stop coverage.
4. **Not an autonomous safety supervisor.** Human "yes" and
   a UI "Allow once" are not a 24/7 dead-man alert, independent
   heartbeat, persistent protective broker order or stop-gap risk
   acceptance. An agent hallucination or lost session could
   bypass assumptions of simple JSON workflow.
5. **Different trust boundary.** An agent given an Alpaca MCP order
   tool could act outside the hardened native executor if tool
   permissions are misconfigured. HDS must not enable a second
   submission route, not even PAPER, while its own kill remains ON.
6. **Upstream software deserves a review.** Informational quotes
   are unofficial/delayed, the sample risk config is general
   investing-focused, historical filings are lagged, and an
   MIT license does not certify market or security correctness.

**Safe optional reuse:** plain-English ticket *display* design and
intent/decision fingerprints after independent HDS audit, without
creating a second agent order pathway. The project copies neither
`trader-broker` nor an Alpaca MCP order connector.

## 5. Research on the five other skills, why deferred

| Upstream option | Real benefit | Decision and timing |
|---|---|---|
| `trader-copycat` | Read SEC 13F long institutional holdings and quarter-on-quarter changes | **Research only, later.** 13F can arrive up to 45 days after quarter-end; holdings lack cash/shorts/intraperiod trades. Not point-in-time intraday entry signals. Useful for source-verified watchlist context, not auto-copied trading targets. |
| `trader-pelosi` | Read House PTRs including spouse trades and disclosure dates | **Optional research.** Public congressional trade disclosure is lagged; options and amount ranges are not exact same-day tradable signals. Expensive to verify OCR/PDF cases and weak fit to frozen strategies. |
| `trader-rebalance` | Target-weight drift and cash buffer maintenance | **Later** if HDS adopts separately approved multi-position long-only PAPER allocation. A monthly weight drift algorithm conflicts with current stop-distance sizing and frozen low-frequency research. |
| `trader-tax` | Track cost-basis lots, term, wash-sale possibilities | **Later** for real taxable holdings only, not paper profits. Requires complete broker fill history and all accounts; current project has zero confirmed PAPER executed fills. |
| `trader-broker` | Explain an order ticket and request a user approval | **Do NOT install in HDS.** Read display ideas only. Its local JSON approval is not durable HDS execution authority and opens a competing order pathway. |

Other frameworks previously researched in
Vandam `TRADING_AND_AFFILIATE_AUTOMATION_RESEARCH.md`:
**NautilusTrader** is worth a future isolated multi-asset event-driven
architecture comparison, but broker adapter and data rights need evidence;
**Freqtrade**, **Hummingbot**, and **Jesse** are crypto-oriented and do not
replace HDS's U.S. equities Alpaca PAPER controls. No such framework was
installed by this PR.

## 6. Documentation audit: all 21 connected Venloud repositories

The authenticated GitHub connection returned **21 repositories** and
their default branches. Checked canonical README.md where available,
plus relevant HorsesDontStop and private Vandam engineering docs.

| Repo | Scope from accessible README / metadata | Action |
|---|---|---|
| `horsesdontstop` | Trading research, strict PAPER safety, Neon, Alpaca | **Only destination** for research code and tests |
| `claudevandam` | Private multi-agent memory, trading build log and handoffs | Memory-only documentation; NO executable code |
| `ht-maybe2048` | Game; standard README unavailable | No trading code borrowed |
| `youtools` | YouTube utilities | No trading code borrowed |
| `horror-shorts` | Video automation | No trading code borrowed |
| `nightfiles` | No standard README located | No trading code borrowed |
| `bouriko-shorts` | Creator short-form workflow | No trading code borrowed |
| `paperclip` | General AI-agent workflow management | Conceptual orchestration only, no broker integration |
| `awesome-design-md` | Design instructions/references | Irrelevant to trading |
| `MoneyPrinterTurbo` | Media generation, despite finance-sounding name | Not a trading system |
| `OmniRoute` | AI gateway/router | Not a broker risk or execution system |
| `kindled-iron-voice` | Voice project | Irrelevant |
| `social-backfill` | Social media posting/backfill | Irrelevant |
| `OpenMontage` | Video editing | Irrelevant |
| `sidebot` | Discord bot | Notification ideas only, no secrets copied |
| `sidebot-dashboard` | Discord UI | Irrelevant |
| `forgearena` | Game | Irrelevant |
| `fi-chier` | Standard README unavailable | No relevant evidence |
| `route-sandbox` | Agent test fixture | No trading code |
| `lakoudomino` | Domino game | Irrelevant |
| `vancastech` | Portfolio website | Irrelevant |

The audit of unrelated repos was **README-level**, not a recursive
inspection of every file in every repo. Do not claim full source-code
security review across 21 repositories. Vandam's historical
`route/runs/horsesdontstop` findings were accounted for as dated
handoff provenance, not automatically exported/published.

## 7. Compatibility with the existing HorsesDontStop roadmap

Prior current HDS design and memory constraints:
- **PAPER ONLY**, starting virtual equity $500 and aspirational $10,000
  total equity, not a promise of profit.
- **1%** planned stop-distance risk per actual candidate; freeze
  alternative 2% experiments to research until explicitly approved.
  Compounding accounts for gains *and* losses below $1,000.
- Proposed **3%** daily and **15%** peak-equity drawdown stops,
  protected STOP lifecycle and independent monitor required.
- No auto deposits; monthly manual contribution ceiling $100.
- Phase 2/3 research requires verified original data provenance,
  ≥10-year point-in-time/delisting-aware coverage or an explicitly
  frozen justified exception, and no changes to frozen trials based
  on holdout returns.
- Safety merges #13 and #14 are already on `main`. External liveness
  and Neon idle worker are still in **unmerged draft PR #23**.
  QuantConnect coverage remains separate in **draft PR #22**.
- `PAPER_ORDER_SUBMISSIONS_RELEASED = False`, broker keys PAPER only,
  production Neon kill ON and submissions OFF, and no verified real
  broker-executed PAPER trades at this integration date.

The skills enhance **information intake and secondary checks**.
They do **NOT** unblock permanent hosting, independently monitored
120-second alerts, live first order, or historical backtest rights.

## 8. Testing, evidence and failure modes

New test contract:
- All six source-pinned modules load from their actual vendored paths.
- SEC XBRL fixture yields a genuine vendor `analyse()` result.
- Source RSS + SEC Atom fixtures produce headline and filing rows.
- Screener passes/fails exact rules and refuses missing-data skips.
- Risk passes small PAPER-only advisory proposals, blocks oversize
  proposals, rejects margin/short/options/live configs,
  advanced order classes, stop fields and broker client IDs.
- Budget enforces whole shares, explicit budget and PAPER only.
- Alerts fire once and return explicit deduplication state,
  not order authority, with timezone validation.
- Unknown capabilities, including `broker`, are refused.
- No API keys, runtime network call, broker submit or database
  write is required for the integration tests; CLI errors sanitized.

Use `python -m pytest -q` and the GitHub Actions `Tests` workflow
as evidence. These tests are **offline synthetic/fixture checks**.
CI is NOT proof of up-to-the-minute SEC/Yahoo uptime, AlphaVantage
vendor rights, Alpaca stop behavior, forward alpha or suitability to
release PAPER orders.

### Operational states

- **Implemented:** Six vendor files + project-local Claude skill
  wrappers + safe adapter + offline tests + documentation.
- **Not enabled:** Any automated external SEC/news fetch schedule,
  GitHub production workflow, notification subscription, Alpaca
  broker tool permission, database credential, signal promotion,
  trade release or financial transaction.
- **Later (separate approval and validation):** Vendor API
  connectivity/rate-limit and data-rights acceptance, timestamped
  immutable research snapshot storage, source provenance and
  monitoring of feed failures, and optional new non-trading
  scheduled research brief on a dedicated worker.

## 9. Source links and licensing

- Original MIT skill pack:
  https://github.com/Jakeschincariol/trader-skill/tree/5189e79b9a480755a3733ac9713f5353981dbe18
- Required copyright:
  `third_party/trader_skill/LICENSE`.
- HDS safety checklist:
  `docs/PROJECT_STATUS_CHECKLIST.md`.
- HDS strategy freeze:
  `docs/PREREGISTRATION.md`,
  `docs/PREREGISTRATION_AMENDMENT_2.md`,
  `docs/PHASE2_RESEARCH_SIGNALS.md`.
- Historical full build log (PRIVATE Vandam, do not publish contents):
  `HORSESDONTSTOP_FULL_BUILD_LOG.md`.
- SEC API policy:
  https://www.sec.gov/about/developer-resources .
- Alpaca advanced order semantics:
  https://docs.alpaca.markets/us/docs/orders-at-alpaca .

**Conclusion:** The six requested research integrations are installed
with a non-trading adapter and Claude project skills, with test coverage.
Five other skills are researched and intentionally deferred. The order
execution pathway is explicitly unchanged and remains disabled.
