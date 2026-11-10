# QQQ Opening Range Breakout: Does a Historical Trading Edge Survive?

**An empirical investigation of Opening Range Breakout profitability, out-of-sample robustness, and prop-firm feasibility.**

## 1. Research Motivation

Can a trading strategy that demonstrated strong historical profitability continue to generate positive expectancy in more recent market conditions? And even if a statistical edge persists, does that necessarily make the strategy practical for a retail trader operating under proprietary trading firm constraints?

This project was motivated by the research paper *Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits from Opening Range Breakout (ORB) Day Trading Strategy vs. Benchmark in the US Stock Market*, by Carlo Zarattini and Andrew Aziz.

The paper investigates an Opening Range Breakout strategy on QQQ and TQQQ and reports substantial historical profitability.

Rather than attempting an exact replication of the original paper, this project takes its central premise as a starting point and investigates a modified ORB strategy using more recent intraday data.

The research addresses two primary questions:

1. **Persistence of the edge:** Does a modified QQQ Opening Range Breakout strategy continue to exhibit positive expectancy, particularly on previously unseen data?
2. **Practical feasibility:** If positive expectancy is observed, is the strategy suitable for a retail trader attempting a time-constrained prop-firm evaluation with strict profit targets and loss limits?

The objective is not to identify the most profitable historical parameter combination, but to evaluate the robustness and practical limitations of a systematic trading strategy.

---

## 2. Research Design

The investigation follows a sequential quantitative research process:

1. Define a systematic and reproducible ORB trading strategy.
2. Collect and validate historical intraday market data.
3. Establish a baseline strategy and evaluate its development-period performance.
4. Explore parameter sensitivity within the development sample.
5. Conduct walk-forward analysis to examine parameter stability.
6. Evaluate the selected strategy on a separate out-of-sample period.
7. Analyze transaction costs and statistical uncertainty.
8. Simulate prop-firm evaluations using Monte Carlo methods.
9. Assess whether historical profitability translates into practical trading feasibility.

The central distinction throughout the project is between **observed historical profitability, statistical evidence of an edge, and real-world implementability**.

---

## 3. Strategy Definition

### 3.1 Opening Range Breakout

An Opening Range Breakout strategy attempts to capture directional price movements following a break above or below the high or low established shortly after the market opens.

The strategy uses 5-minute QQQ OHLCV data during regular US equity trading hours.

Both long and short positions are permitted.

### 3.2 Baseline Trading Rules

| Component | Rule |
|---|---|
| Instrument | QQQ ETF |
| Data frequency | 5-minute OHLCV |
| Opening range | 09:30–09:45 New York time |
| Long signal | First eligible candle closes above opening-range high |
| Short signal | First eligible candle closes below opening-range low |
| Signal window | 09:45–12:00 |
| Entry | Next candle's open |
| Stop-loss | Opposite side of opening range |
| Profit target | 2R |
| Forced exit | 15:00 New York time |
| Trade frequency | Maximum one trade per session |
| Same-bar stop/target ambiguity | Stop-first assumption |

The initial stop distance determines the risk unit:



```math
R_{\text{risk}} = |P_{\text{entry}} - P_{\text{stop}}|
```



Each trade's return is expressed in R-multiples:



```math
R_i = \frac{\text{Realized price P\&L}_i}{R_{\text{risk},i}}
```



This normalization allows trades with different price volatility and stop distances to be compared consistently.

The strategy uses the opening-range boundaries as reference levels rather than predicting market direction in advance.

### 3.3 Difference from the Original Paper

The original study examines a 5-minute opening-range strategy with entry based on the first candle's direction, a 10R profit target, and end-of-day liquidation.

This research deliberately modifies the trading rules by introducing:

- Close-confirmed breakouts.
- Entry at the next bar's open.
- Alternative opening-range durations.
- Alternative reward-to-risk targets.
- Restricted signal windows.
- An earlier forced exit.

Consequently, the results should be interpreted as an **independent extension inspired by the paper**, not a direct replication of its reported performance.

---

## 4. Data and Sample Construction

### 4.1 Data Source

Historical QQQ 5-minute OHLCV data were obtained through Alpaca.

The dataset covers January 2017 through September 2026.

The data were processed using the `America/New_York` timezone and restricted to regular US equity trading sessions.

Data preparation included checks for incomplete sessions, opening-range availability, and trading-calendar irregularities.

### 4.2 Development and Out-of-Sample Separation

| Sample | Period | Purpose |
|---|---|---|
| Development | 2017–2022 | Strategy development and parameter exploration |
| Out-of-sample | 2023–September 2026 | Final evaluation of selected strategy |

The development period contains 1,504 trading sessions.

The out-of-sample period was reserved for evaluating whether the research findings generalized beyond the development sample.

This separation is essential because a strategy may appear profitable when its parameters are selected using the same observations on which performance is measured.

---

## 5. Development-Period Research

### 5.1 Baseline Performance

The original baseline strategy was evaluated on the 2017–2022 development sample.

| Metric | Result |
|---|---:|
| Number of trades | 1,472 |
| Average R | +0.0558 |
| Total R | +82.21 |
| Profit factor | 1.127 |
| Maximum drawdown | 17.57R |

The baseline generated positive historical expectancy before transaction costs.

However, positive development-period performance alone does not establish that the strategy will remain profitable in unseen market conditions.

### 5.2 Parameter Sensitivity

A predefined parameter grid was evaluated:

| Parameter | Values |
|---|---|
| Opening-range duration | 5, 10, 15, 30, 60 minutes |
| Profit target | 1R, 1.5R, 2R, 2.5R, 3R, 4R |
| Signal cutoff | 10:30, 11:00, 12:00, 13:00 |

This produced 120 strategy configurations.

The highest development-period average R was obtained with:

- Opening range: 10 minutes.
- Profit target: 3R.
- Signal cutoff: 10:30.

This configuration was designated as the primary candidate for final out-of-sample evaluation.

Importantly, selecting the strongest configuration from a parameter grid introduces multiple-testing and selection-bias concerns. Its development performance should not be interpreted as an unbiased estimate of future expectancy.

### 5.3 Walk-Forward Analysis

A rolling walk-forward procedure was implemented to examine whether parameters selected from historical training windows continued to perform in subsequent evaluation periods.

Each fold used two years of training data followed by one year of testing.

The development-period walk-forward evaluation covered 2019–2022.

| Metric | Walk-forward result |
|---|---:|
| Trades | 771 |
| Average R | +0.0832 |
| Total R | +64.18 |
| Profit factor | 1.174 |
| Maximum drawdown | 28.47R |

Walk-forward testing provided additional information about parameter instability and strategy behavior across changing market conditions.

However, because the broader parameter investigation was conducted within the development period, these retrospective walk-forward results should not be treated as fully independent evidence.

---

## 6. Final Out-of-Sample Evaluation

The final out-of-sample evaluation used data from January 2023 through September 2026.

Three approaches were compared:

**Primary strategy:** Fixed 10-minute opening range, 3R target, and 10:30 signal cutoff.

**Baseline strategy:** Fixed 15-minute opening range, 2R target, and 12:00 signal cutoff.

**Walk-forward strategy:** Annual parameter selection using a rolling two-year training window.

### 6.1 Out-of-Sample Results

| Metric | Primary | Baseline | Walk-forward |
|---|---:|---:|---:|
| Trades | 906 | 928 | 789 |
| Win rate | 42.60% | 46.66% | 45.12% |
| Average R | +0.0742 | +0.0701 | +0.0763 |
| Total R | +67.26 | +65.05 | +60.19 |
| Profit factor | 1.146 | 1.156 | 1.161 |
| Maximum drawdown | 30.76R | 25.54R | 13.47R |

*All results above are before transaction costs.*

All three approaches generated positive out-of-sample average R.

However, the primary strategy did not substantially outperform the baseline in expectancy, while exhibiting a larger maximum drawdown.

This suggests that the observed edge may be associated with the broader ORB framework rather than a uniquely superior parameter combination.

### 6.2 Performance Across Years

| Year | Primary total R | Baseline total R | Walk-forward total R |
|---|---:|---:|---:|
| 2023 | +20.77 | +15.66 | +20.77 |
| 2024 | +18.90 | +29.12 | +26.36 |
| 2025 | +35.29 | +22.04 | +14.89 |
| 2026* | −7.70 | −1.78 | −1.83 |

*2026 results are through September 30.*

The negative results in 2026 highlight the possibility of changing market conditions and deterioration in strategy performance.

A positive aggregate expectancy can conceal significant variation across individual years.

---

## 7. Robustness and Transaction Cost Analysis

### 7.1 Transaction Cost Sensitivity

Transaction costs were modeled as a fixed round-trip dollar cost per share.

For each trade:



```math
R_{\text{net},i} = R_{\text{gross},i} - \frac{C}{D_i}
```



Where:

- $C$ is the assumed round-trip cost per share.
- $D_i$ is the trade's initial stop distance.

| Round-trip cost | Net Average R | Net Total R |
|---|---:|---:|
| $0.00 | +0.0742 | +67.26 |
| $0.02 | +0.0638 | +57.84 |
| $0.04 | +0.0535 | +48.43 |
| $0.06 | +0.0431 | +39.02 |
| $0.10 | +0.0223 | +20.20 |

The strategy retained positive average R under the evaluated cost assumptions, but the estimated edge decreased substantially as costs increased.

These scenarios represent hypothetical execution costs rather than verified live brokerage fills.

### 7.2 Bootstrap Robustness

Moving block bootstrap was used to estimate uncertainty in average R while partially preserving short-term dependence between consecutive trading observations.

The analysis evaluated multiple block lengths and transaction-cost scenarios.

Under a 4-cent round-trip cost assumption, the estimated net average R was approximately:


```math
\bar{R}_{\text{net}} = 0.0535
```


The 95% bootstrap confidence interval included zero.

Therefore, while positive net expectancy was observed, the available sample did not provide sufficiently strong statistical evidence to exclude zero expectancy at the conventional two-sided 5% significance level under the chosen bootstrap procedure.

This distinction is central to the study:

**A positive backtested average return is not equivalent to a statistically established, persistent trading edge.**

### 7.3 Drawdown Uncertainty

Bootstrap simulations were also used to investigate possible maximum drawdowns under resampled return sequences.

These results suggested that drawdowns materially larger than the historical observed maximum were plausible within the resampling model.

However, bootstrap simulations cannot fully represent previously unseen market regimes, execution failures, or structural changes in market behavior.

---

## 8. Prop-Firm Feasibility Study

### 8.1 Motivation

A strategy can generate positive expected returns while remaining unsuitable for a trading evaluation with strict capital and time constraints.

The research therefore examined whether the observed QQQ ORB return distribution could support successful completion of a hypothetical prop-firm challenge.

### 8.2 Simulation Framework

The Monte Carlo model used historical out-of-sample trade outcomes, adjusted for an assumed 4-cent round-trip cost.

| Parameter | Assumption |
|---|---|
| Profit target | +$2,500 |
| Maximum loss | −$1,500 |
| Risk per trade | $25, $50, $75, $100, $150 |
| Position sizing | Fixed dollar risk |
| Initial sampling | IID empirical bootstrap |
| Robustness sampling | Moving block bootstrap |
| Loss limit | Static, relative to starting equity |
| Trailing drawdown | Not modeled |

The equity process is represented by:


```math
X_n = r \sum_{i=1}^{n} R_i
```


Where $r$ is the fixed dollar risk per trade.

A simulation passes when equity first reaches the profit target and fails when it first reaches the loss limit.

### 8.3 Finite-Horizon Monte Carlo Results

To evaluate whether positive historical expectancy translates into practical prop-firm feasibility, we simulated challenge completion over 30, 60, and 90 trade opportunities.

The following tables report the estimated probability of reaching the $2,500 profit target before breaching the $1,500 static loss limit, under different fixed-dollar risk levels.

**IID Bootstrap — Pass Probability**

| Risk per Trade | 30 Trades | 60 Trades | 90 Trades |
|---|---:|---:|---:|
| $25 | 0.00% | 0.00% | 0.00% |
| $50 | 0.00% | 0.00% | 0.06% |
| $75 | 0.00% | 0.32% | 1.88% |
| $100 | 0.12% | 2.72% | 8.36% |
| $150 | 3.32% | 14.84% | 26.15% |

**Moving Block Bootstrap — Pass Probability**

| Risk per Trade | 30 Trades | 60 Trades | 90 Trades |
|---|---:|---:|---:|
| $25 | 0.00% | 0.00% | 0.00% |
| $50 | 0.00% | 0.00% | 0.02% |
| $75 | 0.00% | 0.22% | 1.64% |
| $100 | 0.08% | 2.24% | 7.35% |
| $150 | 2.62% | 13.34% | 24.78% |

*Simulation assumptions: 20,000 Monte Carlo paths per sampling method, 4-cent round-trip transaction cost per QQQ share, fixed dollar risk per trade, and static profit/loss barriers. The moving block bootstrap uses blocks of 10 consecutive historical trades. Horizons represent trade opportunities rather than calendar days. These figures should be checked against the saved simulation output before publication.*


The results reveal a substantial difference between positive historical expectancy and practical challenge completion.

At $75 risk per trade, the strategy's estimated pass probability under IID sampling was only 1.88% within 90 trade opportunities, despite having produced considerably higher completion probabilities in simulations with much longer horizons.

Increasing risk per trade improved short-horizon completion probabilities but also increased exposure to the maximum loss threshold.

The moving block bootstrap produced broadly similar results, suggesting that the main feasibility limitation was not eliminated by preserving short-term dependence between trade outcomes.

**The key finding is that a strategy can exhibit positive historical expectancy while still having a low probability of reaching a fixed profit target within a limited number of trading opportunities.**

### 8.4 Interpretation

The Monte Carlo analysis illustrates that positive historical expectancy does not automatically translate into a high probability of completing a time-constrained evaluation.

For a relatively low-frequency strategy, the number of available trading opportunities becomes an important practical limitation.

The simulation is intentionally simplified. It does not incorporate actual prop-firm-specific rules, trailing drawdown, daily loss restrictions, intraday unrealized P&L limits, contract-size constraints, or verified futures execution costs.

The results should therefore be interpreted as a feasibility study under explicitly defined hypothetical conditions, not as actual challenge pass probabilities for any particular firm.

---

## 9. Limitations

This study has several important limitations.

**Strategy modification rather than replication.** The tested trading rules differ materially from those used in the original paper. The research cannot directly confirm or refute the paper's reported returns.

**Historical data dependence.** The findings depend on the accuracy, completeness, and representativeness of historical intraday QQQ data.

**Execution assumptions.** OHLC-based backtesting cannot fully reconstruct intrabar execution, queue position, market impact, stop-order slippage, or all gap-through-stop scenarios.

**Parameter selection bias.** Evaluating multiple parameter combinations can produce apparently attractive results by chance.

**Statistical uncertainty.** Positive observed expectancy is not proof of a persistent positive population expectancy.

**Market regime dependence.** Results may change as volatility, liquidity, market structure, and investor behavior evolve.

**Instrument mismatch.** The underlying research uses QQQ ETF data. It does not establish equivalent performance for Nasdaq futures, CFDs, or leveraged ETFs.

**Idealized position sizing.** The Monte Carlo model assumes fixed dollar risk per trade without enforcing integer shares or futures contract sizes.

**Simplified prop-firm rules.** Static loss limits and fixed horizons do not reproduce every real-world evaluation rule.

**Bootstrap limitations.** Historical resampling does not generate entirely new market regimes or guarantee realistic future tail behavior.

---

## 10. Conclusion

This project began with a simple question: does an Opening Range Breakout strategy inspired by previously published research continue to exhibit positive expectancy in more recent market conditions?

The empirical results suggest that a modified QQQ ORB strategy retained positive historical average R during the 2023–2026 out-of-sample period.

However, several qualifications materially affect the interpretation of this finding.

First, transaction costs reduced the observed edge. Second, bootstrap confidence intervals included zero, indicating that the evidence for persistent positive expectancy remained statistically inconclusive. Third, the strategy experienced meaningful drawdowns and deterioration during part of the evaluation period.

Finally, the prop-firm simulations demonstrated the importance of considering capital constraints, risk per trade, and the number of available trading opportunities rather than evaluating a strategy solely through average returns.

**The central conclusion is that positive historical expectancy is necessary but not sufficient for establishing practical trading viability.**

A strategy must also demonstrate sufficient statistical robustness, execution feasibility, manageable drawdowns, and compatibility with the trader's operational constraints.

The research therefore provides evidence of a potentially interesting ORB signal, but not a validated live-trading system or a demonstrated prop-firm advantage.

---
## 11. Repository Structure

The repository is organized around a sequential quantitative research workflow, from historical data preparation to final out-of-sample validation and prop-firm feasibility analysis.

```text
qqq-opening-range-breakout/
│
├── src/
│   ├── download_data.py
│   ├── prepare_data.py
│   ├── orb_strategy.py
│   ├── audit_trades.py
│   ├── parameter_sweep.py
│   ├── compare_strategies.py
│   ├── walk_forward.py
│   ├── final_oos.py
│   ├── robustness_analysis.py
│   ├── prop_monte_carlo.py
│   └── final_prop_feasibility.py
│
├── tests/
│   └── test_orb_strategy.py
│
├── data/
│   ├── raw/                 # Not tracked in Git
│   └── processed/           # Not tracked in Git
│
├── results/
│   └── tables/
│       ├── data_quality_report.csv
│       ├── parameter_sweep_results.csv
│       ├── walk_forward_summary.csv
│       ├── final_oos_summary.csv
│       ├── final_oos_annual.csv
│       ├── robustness_costs.csv
│       ├── robustness_bootstrap.csv
│       ├── prop_mc_summary.csv
│       └── final_prop_feasibility.csv
│
├── README.md
├── requirements.txt
└── .gitignore
```

*The results directory contains additional trade-level data, diagnostic outputs, and intermediate research tables. The structure above highlights the principal research outputs.*

### 11.1 Data Acquisition and Preparation

| Script | Purpose |
|---|---|
| `download_data.py` | Downloads historical QQQ intraday data using Alpaca. |
| `prepare_data.py` | Prepares the dataset for backtesting and performs data-quality checks. |

Raw and processed market data are excluded from the public repository. Users wishing to reproduce the analysis must obtain suitable historical data independently.

### 11.2 Strategy Development

| Script | Purpose |
|---|---|
| `orb_strategy.py` | Implements the baseline Opening Range Breakout strategy. |
| `audit_trades.py` | Supports trade-level inspection and validation. |
| `parameter_sweep.py` | Evaluates combinations of opening-range duration, profit target, and signal cutoff. |
| `compare_strategies.py` | Compares strategy configurations and summarizes their performance. |

### 11.3 Validation and Robustness

| Script | Purpose |
|---|---|
| `walk_forward.py` | Performs rolling train/test evaluation within the development period. |
| `final_oos.py` | Evaluates selected strategies on the reserved out-of-sample period. |
| `robustness_analysis.py` | Studies transaction-cost sensitivity and bootstrap uncertainty. |

### 11.4 Prop-Firm Feasibility

| Script | Purpose |
|---|---|
| `prop_monte_carlo.py` | Simulates fixed-risk challenge outcomes using historical trade returns. |
| `final_prop_feasibility.py` | Evaluates challenge completion probabilities over 30, 60, and 90 trade opportunities using IID and moving block bootstrap. |

### 11.5 Testing

`tests/test_orb_strategy.py` contains tests for the ORB strategy implementation.

The tests are intended to help verify the correctness of the research code. Passing software tests does not establish statistical validity or future profitability.

## 12. Reproducibility

The research was implemented in Python. The code is structured to separate data preparation, strategy development, out-of-sample evaluation, statistical robustness, and Monte Carlo simulations.

### 12.1 Environment Setup

Clone the repository:

```bash
git clone https://github.com/mericck/qqq-opening-range-breakout.git
cd qqq-opening-range-breakout
```

Create a Python virtual environment:

```bash
python -m venv .venv
```

Activate the environment on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the project dependencies:

```bash
pip install -r requirements.txt
```

### 12.2 Research Execution Order

Once the required historical market data and credentials are available, the intended research workflow is:

```bash
# 1. Data acquisition and preparation
python -m src.download_data
python -m src.prepare_data

# 2. Baseline strategy
python -m src.orb_strategy

# 3. Parameter research
python -m src.parameter_sweep
python -m src.compare_strategies

# 4. Walk-forward evaluation
python -m src.walk_forward

# 5. Final out-of-sample evaluation
python -m src.final_oos

# 6. Robustness
python -m src.robustness_analysis

# 7. Prop-firm simulations
python -m src.prop_monte_carlo
python -m src.final_prop_feasibility
```

The scripts are presented in their logical research order. Execution may require configuration of Alpaca credentials, availability of historical data, and creation of expected output directories.

### 12.3 Reproducing Reported Results

The `results/tables/` directory contains the numerical outputs used in the research.

Key files include:

- `final_oos_summary.csv`: Final out-of-sample strategy comparison.
- `final_oos_annual.csv`: Year-by-year out-of-sample performance.
- `robustness_costs.csv`: Transaction-cost sensitivity.
- `robustness_bootstrap.csv`: Bootstrap uncertainty estimates.
- `prop_mc_summary.csv`: Longer-horizon Monte Carlo results.
- `final_prop_feasibility.csv`: Finite-horizon prop-firm challenge results.

Reproduction requires matching the data source, adjustment conventions, session filtering, strategy rules, execution assumptions, and simulation parameters used in the original experiments.

Differences in any of these components may lead to different results.

## 13. References

Zarattini, C., & Aziz, A. (2025 version). *Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits from Opening Range Breakout (ORB) Day Trading Strategy vs. Benchmark in the US Stock Market.*

Research implementation: Python, pandas, NumPy, and historical QQQ intraday market data sourced through Alpaca.

---

*This repository is for educational and quantitative research purposes only. Historical and simulated performance does not guarantee future trading results.*
