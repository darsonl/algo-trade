# Design: Strategy Validation — Forward Shadow Log + Mechanical Backtest

**Date:** 2026-08-21
**Status:** Draft — awaiting review
**Milestone:** v1.5 (candidate)
**Source:** `HANDOFF-2026-08-21.md` item A ("no backtest and no forward sample — now the
largest item by far"); Codex methodology review, 2026-08-21 (`task-mt326cyu-r2y5cp`)
**Supersedes for this topic:** the Phase-3 research-harness bullets in
`codex_recommendations.md` and `plans/2026-08-14-codex-backlog-roadmap.md`, both of which
describe a fuller harness this design deliberately does not build.

---

## Summary

The repo has 1105 passing tests and no evidence the strategy makes money. Every safety
mechanism bounds *how badly a trade can go wrong*; none of them is evidence it is worth
making. This design adds the two instruments that can produce such evidence, and — as much
as it adds them — constrains what they are allowed to claim.

The central finding driving the design is that **the deployed strategy cannot be backtested**,
and this is a data-source fact rather than a scoping choice. Entry requires three gates in
series:

| Gate | Reconstructable historically? | Evidence |
|---|---|---|
| Fundamental (`passes_fundamental_filter`) | **No** | `Ticker.info` is a snapshot with no history; `quarterly_income_stmt` returns **5 quarters** (measured, AAPL, 2026-08-21). There is no historical `trailingPE` series. |
| LLM analyst on 5 headlines | **No** | yfinance returns **10 current** items, no archive. And model weights postdate any plausible window, so the analyst may *know the outcome* — a contamination no data source fixes and no chronological split cures. |
| Technical (`passes_technical_filter`) | **Yes** | 10y free OHLCV (2513 bars for AAPL). RSI/MA50/volume/MACD fully reconstructable. |

Two of three gates are unavailable. Therefore:

* **Subsystem A (backtest)** measures **only the technical gate and the RSI/MACD exit**. It is
  a study of one component, not of the strategy, and the design goes to some length to make
  that impossible to forget.
* **Subsystem B (shadow log)** records the *whole* pipeline forward, including the parts no
  retrospective test can reach: the analyst's actual verdict, the human's actual click, the
  guard outcomes, and the realised prices.

**Build order is B then A.** B accrues evidence only with calendar time — every day it is not
running is a day of sample permanently lost — while A can be built at any point against ten
years of history that is not going anywhere. B is also the smaller subsystem. This ordering is
the opposite of the two subsystems' apparent importance and is deliberate.

### What changed after external review

Codex reviewed the older Phase-3 wishlist rather than this design, and three of its five
"fatal flaws" address scope already excluded here. Notably it converged independently on two
of the same exclusions: *"exclude the current LLM from retrospective P&L"* and *"restrict
research to price-only deterministic rules and label it as testing a different strategy."*

Three findings did land and are incorporated:

1. **Estimand confusion** — measuring one gate is not measuring the strategy, and a footnote
   will not survive six months. Made structural (§3).
2. **Data snooping** — tuning thresholds after seeing results manufactures an overfit and
   leaves no trace. Preregistration added (§5).
3. **Fill realism** — "fill at next open" misrepresents a GTC limit order policy. Explicit
   fill model with a no-fill state added (§4.4).

---

## 1. Non-goals

This design does **not**:

* backtest the fundamental filter, the analyst, or the ETF path;
* produce an equity curve, CAGR, Sharpe, Sortino, alpha, or beta (§3.2 — these are not
  merely omitted, the code to compute them is not written);
* simulate the 12-guard preflight table, capital reservation, or portfolio saturation.
  Per-signal statistics were chosen precisely to avoid that path dependence, which Codex
  correctly identifies as unreproducible by an independent-row replay;
* change any live trading behaviour. Both subsystems are read-only with respect to orders.

---

## 2. Subsystem B — Forward shadow log (build first)

### 2.1 Purpose

Record every candidate the pipeline evaluates, with the full information set as of the
decision, so that later analysis can compute honest denominators. Codex's point stands:
a conversion rate is meaningless unless the denominator contains every screened candidate,
every analyst skip, every technical rejection, every human non-response, every guard refusal
and every unfilled order.

### 2.2 Schema

Two new tables in the existing SQLite database.

**`shadow_observations`** — one row per (ticker, scan), written at the point the candidate
*leaves* the pipeline, whatever the reason:

    id, session_date, observed_at, ticker, scan_kind ('stock'|'etf'),
    stage_reached, outcome, reject_reason,
    fundamentals_json, technicals_json, headlines_json, macro_json,
    analyst_provider, analyst_model, analyst_signal, analyst_confidence,
    analyst_prompt_sha256, analyst_raw_response, cache_hit,
    recommendation_id, reference_price

`stage_reached` is an ordered enum (`universe` → `fundamental` → `analyst` → `technical` →
`recommended`) and `outcome` records what happened there. Together they make the funnel
reconstructable without inference.

`analyst_prompt_sha256` and `analyst_model` are stored because the analyst cache
(`analyst_cache`) keys only on the headline hash and records neither — so a cached verdict
today cannot be attributed to the model that produced it. Research data must not inherit that
gap.

**`shadow_outcomes`** — forward price marks, filled in later by a scheduled job:

    observation_id (FK), horizon ('1w'|'1m'|'3m'|'6m'), as_of, price, return_pct,
    benchmark_price, benchmark_return_pct

Benchmark is SPY over the identical window, so every observation carries its own
market-relative result and no later analysis has to re-derive one.

### 2.3 Integration constraints

* **It must never break a scan.** Every write is wrapped and swallowed, and failures are
  logged, following `send_ops_alert`'s precedent. A research recorder that can abort a scan is
  a liability, not an instrument.
* **It records; it never gates.** No shadow-log read may influence a recommendation.
* **It runs in dry run.** Dry-run scans produce genuine screening decisions; only the order is
  simulated. Excluding them would discard most of the available sample.
* Hooks go at each exit point in `_run_scan_locked` / `_run_scan_etf_locked`, plus the
  approve/reject handlers for the human action and latency.

**Known cost:** the handoff records that adding a guard to the scan loop broke 22 tests that
patch `queries` function-by-function, and that two harnesses append patches at the end
specifically to keep positional mock indices stable. Adding call sites here will hit the same
tests. The remedy is an autouse fixture rather than a mid-list insertion; `test_main.py` is
also at Python's 20-block nesting limit, so `with A, B:` is not available there.

---

## 3. Subsystem A — Mechanical backtest

### 3.1 Scope

Technical entry gate + RSI/MACD exit, current S&P 500 membership, ten years.

Universe is the **full** constituent list, not `get_top_sp500_by_fundamentals`. That ranking
scores by *today's* EPS and ROE, which would layer a second, larger hindsight bias on top of
membership survivorship.

### 3.2 The estimand is enforced in code, not in a caveat

Every report names, in its header, what it measures and what it does not: *"the technical gate
in isolation, on a survivorship-biased universe; NOT the deployed strategy, which additionally
requires a fundamental filter and an LLM analyst verdict."*

`backtest/stats.py` contains **no function that computes CAGR, Sharpe, Sortino, alpha or
beta.** This is the enforcement. A caveat can be dropped when a number is copied into a
message; an absent function cannot be called. Permitted outputs:

* per-trade return distribution (median, IQR, deciles) — not the mean alone;
* win rate and payoff ratio, with a bootstrap interval;
* the **spread** versus an equal-weight buy-and-hold of the identical universe over the
  identical window, which is the only comparison in which survivorship bias substantially
  cancels;
* trade count, and the count of unclosed positions, always reported together.

### 3.3 Structure

| Module | Responsibility |
|---|---|
| `backtest/data.py` | Fetch + disk-cache 10y OHLCV per ticker (`backtest_cache/`, gitignored). |
| `backtest/engine.py` | Walk the calendar per ticker; emit entry/exit/no-fill events via the **production** filters. |
| `backtest/fills.py` | Limit-order fill model (§4.4). |
| `backtest/stats.py` | Pure statistics, restricted per §3.2. |
| `backtest/report.py` | Rendering, including the mandatory estimand header. |
| `scripts/run_backtest.py` | CLI. |

**One targeted change to existing code:** extract the dict-building half of
`fetch_technical_data` into a pure `technical_snapshot(hist) -> dict`, leaving
`fetch_technical_data = _fetch_history + technical_snapshot`. Live behaviour is unchanged and
the backtest then calls the identical math. A backtest that reimplements the strategy tests
the reimplementation and drifts silently the first time either side changes.

---

## 4. Bias controls

Each of these is a specific way this design could otherwise emit a fake edge.

### 4.1 Point-in-time indicator computation

Signals for day *T* are computed from `hist[:T]` only. Because `compute_rsi`, `compute_macd`,
`ma50` and `avg_volume` all read from the **end** of the series (`tail(n)`, `iloc[-1]`),
slicing to *T* makes every value as-of *T* by construction.

### 4.2 The trailing window must match live — measured, not assumed

Live computes on `period="3mo"` (~63 bars). RSI (Wilder's) and MACD are **recursive**, so their
values depend on how much history precedes them. Measured on AAPL, 2026-08-21:

| window (bars) | RSI | MACD | signal |
|---|---|---|---|
| **63 (what live sees)** | **46.1213** | **-1.7791** | **-1.4157** |
| 120 | 46.3886 | -1.5988 | -1.1513 |
| 250 / 500 / 1000 / 2514 | 46.3904 | -1.5991 | -1.1517 |

Values converge by ~120 bars; only live's short window is distinct. MA50 is
window-independent above 50 bars, as expected. The differences are small but they sit next to
threshold comparisons (`rsi > sell_rsi_threshold`, `macd_line < signal_line`) where a small
delta flips a boolean. **The backtest therefore slices 63 bars, and a test pins its values
equal to live's on identical data.**

*Observation for the live system, out of scope here: at 63 bars the 26-period MACD EMA has had
~2.4 spans to converge, so live's MACD is computed on a not-fully-warmed EMA. Consistent, but
it differs measurably from textbook values, and anyone checking by hand will find the
discrepancy and suspect a bug.*

### 4.3 Execution timing

Signal on day *T*'s close → order live from day *T+1*. Entering at *T*'s close would trade on
a number only known after the bell.

### 4.4 Fill model, with a real no-fill state

Live buys are **GTC limits** at `ask × (1 + APPROVAL_SLIPPAGE_BUFFER_PCT/100)`, rounded up;
sells are **DAY marketable limits** through the bid. "Fill at next open" would misrepresent
both.

Daily OHLC cannot recover queue position, so the model is explicitly conservative and its
assumptions are printed with every report:

* **Buy:** limit = *T+1* open × (1 + buffer). Filled only if *T+1*'s **low ≤ limit**; fill
  price is the limit, never better. Unfilled orders persist per GTC for a bounded number of
  sessions, then expire. **A no-fill is recorded as a no-fill**, never silently dropped and
  never assumed filled.
* **Sell:** DAY order; filled only if the day's **high ≥ limit**, else it expires and retries
  on the next signal day, matching live behaviour.
* No partial fills are modelled. This is a known simplification and is stated in the report.

### 4.5 Unclosed positions

The live strategy has **no stop-loss and no time-based exit**. A position whose RSI/MACD exit
never fires is held indefinitely. In the backtest such positions are marked to the final bar
and reported **separately** as unclosed, never merged into the closed-trade statistics.
Dropping them would remove precisely the losers that never recovered.

### 4.6 Survivorship

Current membership is used, per the owner's decision. Every report prints the bias **and its
direction** — results are optimistic — as a header, not a footnote. The buy-and-hold benchmark
is drawn from the identical universe so the bias largely cancels in the spread, which is why
§3.2 makes the spread the headline number and absolute return unavailable.

---

## 5. Preregistration

Before the first run, `backtest/PREREGISTRATION.md` is committed recording:

* frozen thresholds — `max_rsi=70.0`, `min_volume_ratio=0.5`, `sell_rsi_threshold=70.0`,
  MA window 50, `MIN_HISTORY_BARS=51`, trailing window 63 bars;
* universe definition, date range, and benchmark;
* the exact metric list from §3.2;
* the fill assumptions from §4.4.

A test asserts the runtime configuration matches the preregistration, so a later threshold
tweak **fails loudly** rather than silently producing a better-looking number. Without this,
the harness becomes a machine for discovering whichever variant looks best in hindsight, and
nothing in the output would reveal that it had.

Changing a threshold afterwards is legitimate — it is a *new* preregistration and a *new* run,
recorded as such, not an edit to the old one.

---

## 6. Testing

TDD throughout, per the repo's convention. The load-bearing tests:

* **indicators match live** — `technical_snapshot` on a 63-bar slice equals
  `fetch_technical_data` on the same data (§4.2);
* **no lookahead** — appending future bars to the series must not change a previously emitted
  signal. This is the test that would catch an accidental centred window or a full-series
  computation;
* **known-outcome fixture** — a hand-computed synthetic price series with the expected trades
  worked out by hand, in the style of `test_screener_technicals.py`;
* **no-fill is recorded** — a bar whose low never reaches the limit produces a no-fill row,
  not a trade;
* **unclosed positions are excluded from closed-trade statistics**;
* **preregistration mismatch fails** (§5);
* **the shadow log never aborts a scan** — a write that raises leaves the scan's outcome
  unchanged;
* **the shadow log records rejects, not just recommendations** — a candidate rejected at the
  fundamental stage still produces a row.

Per the handoff, mutation-test the load-bearing ones: two tests passed vacuously last cycle and
both were found only by mutating the code and watching nothing fail. Commit a checkpoint first
— `git checkout <file>` reverts to HEAD and has already silently deleted uncommitted work once.

---

## 7. What this will and will not establish

**Will:** whether the technical gate, in isolation, has any edge over buy-and-hold on the same
universe — cheaply, deterministically and repeatably. This can *falsify* the core: if the gate
plus exit underperforms a passive hold of the identical names, the two gates that cannot be
tested are decorating something that does not work, and that is worth knowing before any
further investment.

**Will not:** establish that the deployed strategy is profitable. That claim requires the
fundamental filter, the analyst and the human approver, and only Subsystem B can gather
evidence about those — over months, not in one run.

Codex's blunt verdict was that a forward paper-trading log is the more honest evidence for this
system. That is accepted, and is why B is built first.

**The standing constraint is unchanged: until B has accumulated a real sample, every
recommendation remains an unvalidated research lead.**
