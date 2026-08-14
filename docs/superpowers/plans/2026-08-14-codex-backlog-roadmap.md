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
Phase 1 (safety) ──> A: Execution ledger ──> Forward validation (Codex Phase 5)
                                                      ^
C: Research harness (needs point-in-time data) ──> D: Signal redesign
B: Analysis integrity   (independent, any time)
D0: Screener determinism (independent, any time)
```

**Corrected 2026-08-14 after external review.** An earlier version of this document claimed
"A blocks C" — that backtesting could not begin until the trade ledger recorded real fills.
That was wrong. A point-in-time backtest simulates its own hypothetical fills from historical
bars; it never reads the production `trades` table. **C's real blocker is point-in-time
fundamentals data**, identified in C's own section below.

What A actually gates is **forward validation** (Codex Phase 5) — comparing live results
against backtest expectations. That comparison is meaningless while live P&L is computed
from quoted rather than filled prices. So A is a prerequisite for trusting live results, not
for producing backtest results.

**A also gates nothing else.** B, C, and D0 are independent of it and of each other. The only
hard chain is **C → D**: redesigning signals without a way to measure them is how you overfit
to intuition.

**Execution-order caution.** B, D0, and A all modify `config.py` and `main.py`. Running them
in parallel will produce mechanical merge conflicts even though they are semantically
independent. Sequence them, or expect to resolve conflicts by hand.

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
exposes `Client.get_order(order_id, account_hash)` and a broker-defined status enum, so the
state machine is determined by the broker rather than invented.

> **Dependency warning.** `requirements.txt:172` and `requirements.in:12` pin
> `schwab-py==1.4.0`, but the machine this was planned on has **1.5.1** installed. Every API
> fact used in these plans (`get_order`, `get_quote`, the status enum and its member count)
> was verified against 1.5.1. **Before executing Workstream A, either bump the pin to 1.5.1
> and regenerate the lock with `uv pip compile`, or re-verify each API against 1.4.0.** A
> clean `pip install -r requirements.txt` today would install a version these plans were not
> checked against.

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
measure them is how you overfit to intuition. This is the one genuine hard dependency in the
document.

**Why it cannot be planned now.** "Use scale-independent factors such as earnings yield,
FCF yield, ROIC, gross profitability" is a research program, not a task list. Which factors,
weighted how, normalized within what sector scheme, validated against what — none of that is
answerable without the harness from C.

### Finding 9 — protective stop: WON'T FIX (decided 2026-08-14)

Codex recommends adding a maximum-loss stop, trailing exits, and time stops, on the grounds
that the RSI+MACD exit is a profit-taking pattern that never fires on a steadily declining
position.

**Decision: no stop-loss will be added.** The strategy is long-term hold, not short-term
gain. A stop would force realization of exactly the drawdowns a buy-and-hold thesis intends
to ride through, and it is inconsistent with the entry screen, which selects for dividend
yield, moderate P/E, and earnings growth — an income and quality screen, not a momentum one.

Recorded here so future reviews do not re-raise it as an open gap. It is a deliberate
strategy choice, not an oversight.

**Open coherence question this raises.** If positions are held long term, the existing sell
pass — RSI > 70 AND MACD bearish, a short-horizon overbought-reversal trigger — is the
component that does not fit the thesis. The system currently has no downside exit and a
sensitive upside exit, which is the opposite of what a long-term holder usually wants. Worth
deciding separately whether the sell pass should be narrowed to thesis-breaking events
(dividend cut, earnings collapse) or removed. Not planned; flagged.

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
| A — Execution ledger | 4, 10 | Plan revised (v2) | Phase 1; schwab-py pin |
| B — Analysis integrity | 5, 12 | Plan revised (v2) | nothing |
| D0 — Screener determinism | 8, 13 | Plan revised (v2) | nothing |
| D9 — Protective stop | 9 | **Won't fix — by design** | — |
| C — Research harness | 6 | Needs design cycle | point-in-time data source |
| D — Signal redesign | 7, 11 | Needs design + research | C |
| Sell-pass coherence | — | Open question | strategy decision |

**Standing caveat from finding 6:** none of this work produces evidence that the strategy is
profitable. Phase 1 makes the bot safe to operate; A makes its records truthful; B makes its
inputs honest; C is the first step that could tell you whether it is worth operating at all.
