# Codex Backlog Roadmap — Findings 4–13

**Date:** 2026-08-14
**Source:** `codex_recommendations.md`
**Phase 1 spec (separate, already written):** `docs/superpowers/specs/2026-08-14-live-trading-safety-design.md`

This is the organizing document for everything the Codex review raised that Phase 1
(live-trading safety) does not address. It is **not** an implementation plan. It decomposes
findings 4–13 into four workstreams, fixes their order, and states which ones are ready to
build versus which need a design cycle first.

---

## Dependency order

```
Phase 1 (safety)  ──┐
                    ├──> A: Execution ledger ──┬──> C: Research harness ──> D: Signal redesign
B: Analysis integrity ──────────────────────── ┘
D0: Deterministic screener fixes  (independent, any time)
```

**A blocks C, which blocks D.** This is the single most important sequencing fact in the
document. C (backtesting) exists to answer "does this strategy make money", and D (signal
redesign) exists to act on that answer. Both are meaningless while the trade ledger records
*quoted* prices instead of *fill* prices — you would be validating a strategy against a P&L
series that never happened. A is therefore the foundation, not merely the next item.

**B is independent of A** and can run in parallel. It fixes correctness of the analysis
input, not of the execution record.

**D0 is independent of everything.** Small deterministic fixes with no design risk.

---

## Workstream A — Execution ledger

**Findings:** 4 (order acknowledgements treated as completed fills), 10 (remainder — exposure
from broker values rather than DB averages)

**Status:** Ready to build. Plan written: `2026-08-14-execution-ledger.md`

**Problem.** `discord_bot/bot.py:128-142` creates a trade row and a position immediately
after the broker returns an order id, with `price=self.price` — the scan-time quote. The
code says so itself at `bot.py:124`:

```
# WARNING (RISK-05 / Phase 17): GTC limit orders are recorded as positions immediately
# on broker acknowledgement, not on fill.
```

Consequences: unfilled GTC limits create phantom positions; partial fills record as full;
`/stats` win rates are computed from prices that were never paid; the sell pass can attempt
to sell shares that do not exist.

**Approach.** Introduce an `orders` table between recommendations and trades. Orders carry
broker lifecycle state; positions are built only from confirmed executions. `schwab-py`
exposes `Client.get_order(order_id, account_hash)` and a 20-value status enum
(`FILLED`, `WORKING`, `REJECTED`, `CANCELED`, `PENDING_ACTIVATION`, …), so the state machine
is determined by the broker rather than invented.

**Done when:** a GTC limit order that never fills produces no position; a partial fill
records actual filled quantity at actual fill price; `/stats` derives from execution records.

---

## Workstream B — Analysis integrity

**Findings:** 5 (ETF recommendations reuse stale analysis), 12 (LLM is not a reliable
decision boundary)

**Status:** Ready to build. Plan written: `2026-08-14-analysis-integrity.md`

**Problem.** Three separate defects sharing one code path:

1. The analyst cache key is `(ticker, headline_hash)` only (`database/queries.py:194`), with
   no TTL. `main.py`'s `compute_headline_hash` already salts the *empty* headline case with
   today's date — good instinct, but it leaves the far more common non-empty case unbounded.
   If headlines are unchanged, a `BUY` from four days ago is reused at a price 6% higher.
2. Headlines are interpolated straight into the prompt at `analyst/claude_analyst.py:136`
   and `:217` (`"\n".join(f"- {h}" for h in headlines)`) with no delimiting and no
   instruction to treat them as untrusted. A headline is attacker-influenced text.
3. The ETF path accepts an LLM `BUY` as the sole gate. `main.py:588` comments it explicitly:
   `# ETF uses BUY signal check but no technical filter`. The expense-ratio threshold only
   affects the Discord display.

**Done when:** the cache key covers the full feature snapshot plus model and prompt version
with an explicit TTL; headline text is delimited and marked untrusted; an ETF `BUY` must
also clear a deterministic technical gate.

---

## Workstream D0 — Deterministic screener fixes

**Findings:** 8 (permissive missing-data behavior), 13 (intraday volume depends on scan time)

**Status:** Ready to build. Plan written: `2026-08-14-screener-determinism.md`

**Problem.** `screener/fundamentals.py:46,53` skip the dividend-yield and earnings-growth
checks when the value is missing, so a company with no growth data passes a nominal growth
filter. `screener/technicals.py:96,119` compare the latest daily volume bar against a 20-bar
average — but near the open that bar is a partial session, so a qualifying stock is rejected
purely because the scan ran early.

**Done when:** missing values are tracked explicitly and rejected or penalized by documented
policy rather than silently passing; volume comparison uses the last completed bar; every
signal records its data timestamp and market-session state.

---

## Workstream C — Research harness  ⚠️ NEEDS DESIGN FIRST

**Finding:** 6 (no evidence either strategy generates alpha)

**Status:** **Not plannable yet.** This is a greenfield subsystem larger than everything
currently in the repo. It requires its own brainstorm → spec → plan cycle.

**Why it cannot be planned now.** A task-level plan would have to invent answers to design
questions that materially change the architecture. Writing one would produce plausible
fiction, not an executable plan.

**Design questions that must be answered before planning:**

1. **Data source for point-in-time fundamentals.** yfinance serves *current* data; it cannot
   tell you what trailing P/E was on 2023-04-11. Without point-in-time data there is no
   honest backtest. Options: a paid vendor (Sharadar, Norgate, Polygon), scraping SEC
   filings, or accepting a documented look-ahead bias and reporting it. This single answer
   determines whether the harness is a weekend or a quarter of work.
2. **Historical S&P 500 constituents.** Current membership creates survivorship bias.
   Same buy-or-build question.
3. **How does the LLM participate in a backtest?** Replaying 500 tickers × 3 years of
   headlines through Claude is expensive and non-deterministic. Options: exclude the LLM and
   backtest only the deterministic filters (cheap, honest, tests less); cache a fixed
   LLM decision per (ticker, date); or drop the LLM to an explanation layer as Codex
   recommends in finding 12, which makes the deterministic core the thing being validated.
4. **Event-driven or vectorized?** Event-driven models order rejection and slippage
   realistically but is slower to write. Vectorized is fast but hides execution reality.
5. **Where does the harness live?** Same repo, or a separate research project consuming the
   same screener modules?

**Recommendation when we get here:** answer question 3 first — it likely collapses into
finding 12's recommendation (LLM as explanation, not gate), which makes the whole harness
dramatically simpler and cheaper.

---

## Workstream D — Signal redesign  ⚠️ NEEDS DESIGN AND RESEARCH FIRST

**Findings:** 7 (universe ranking financially weak), 9 (exit mechanism does not protect
losing positions), 11 (ETF watchlist has overlapping exposure)

**Status:** **Not plannable yet.** Depends on C — redesigning signals without a way to
measure them is how you overfit to intuition.

**Why it cannot be planned now.** "Use scale-independent factors such as earnings yield,
FCF yield, ROIC, gross profitability" is a research program, not a task list. Which factors,
weighted how, normalized within what sector scheme, validated against what — none of that is
answerable without the harness from C.

**One exception worth noting: finding 9 (no stop-loss) is arguably urgent and separable.**
The current exit requires RSI above threshold AND MACD bearish — a profit-taking pattern. A
position that declines steadily never becomes overbought and so never reaches the sell
analysis at all. There is no maximum-loss stop anywhere in the system. If you begin trading
real money after Phase 1, you hold positions with no downside exit.

A simple deterministic stop (hard percentage loss, or ATR-multiple) does not require the
research harness to justify — it is risk management, not alpha generation. **Consider
promoting this to run alongside Workstream A.** Flagged rather than planned because the
threshold choice is yours and the design deserves its own short brainstorm.

**Design questions for the rest of D:**

1. Which factor set, and validated how? (blocked on C)
2. Sector-neutral or absolute ranking?
3. Missing-data policy: reject, impute, or penalize?
4. ETF overlap: measured by holdings overlap, return correlation, or hand-maintained
   category tags?
5. Do exits get validated jointly with entries as a unified strategy? (Codex says yes;
   this requires C)

---

## Summary

| Workstream | Findings | Status | Blocked by |
|---|---|---|---|
| A — Execution ledger | 4, 10 | Plan ready | Phase 1 |
| B — Analysis integrity | 5, 12 | Plan ready | nothing |
| D0 — Screener determinism | 8, 13 | Plan ready | nothing |
| D9 — Protective stop | 9 | Needs short brainstorm | nothing (promote?) |
| C — Research harness | 6 | Needs design cycle | A |
| D — Signal redesign | 7, 11 | Needs design + research | C |

**Standing caveat from finding 6:** none of this work produces evidence that the strategy is
profitable. Phase 1 makes the bot safe to operate; A makes its records truthful; B makes its
inputs honest; C is the first step that could tell you whether it is worth operating at all.
