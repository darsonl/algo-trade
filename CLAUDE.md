# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# ALWAYS work inside .venv. It is pinned to Python 3.12 to match CI, and it is
# the only environment where requirements.txt is installable — see below.
uv venv --python 3.12 .venv                 # first time only
uv pip install --python .venv/Scripts/python.exe -r requirements.txt

# Update dependencies: edit requirements.in (direct deps only), then regenerate
# the fully-pinned lock with uv. Never hand-edit requirements.txt.
#   uv pip compile requirements.in --universal --python-version 3.11 -o requirements.txt

# Run the bot (opens browser on first run for Schwab OAuth2)
.venv/Scripts/python.exe main.py

# Run all tests
.venv/Scripts/python.exe -m pytest

# Run a single test file
pytest tests/test_screener_technicals.py

# Run tests with verbose output
pytest -v

# Run a single test by name
pytest tests/test_analyst_claude.py::test_parse_buy_signal -v
```

### Why the venv is not optional

The machine's only system Python is **3.14**, and the lock pins `pandas==2.2.3`, which
publishes no cp314 wheel (cp39–cp313 only). Installing the lock into system Python therefore
cannot succeed, and `pip install` outside the venv silently resolves *forward* instead — which
is how 36 of 77 pins came to drift, including pandas to a major version ahead (3.0.1),
`anthropic` 0.40.0 → 0.86.0, and `pytest-asyncio` across its 0.x → 1.x breaking change.

That drift meant local tests and CI were two different experiments, and it hid a real bug:
`get_positions` referenced `schwab.Client`, which does not exist in schwab-py 1.5.1, so
reconciliation raised on every call for months. Nothing caught it — CI was green because no
test exercised the function, and `main.py` swallowed the exception into a log warning.

`.venv` on 3.12 matches CI exactly (verified: 75 pins, 0 drifted). Keep it that way.
**Never `pip install` into system Python for this project.**

## Architecture

**Algo Trade** is an automated stock screener that posts Claude AI-generated BUY recommendations to Discord for human approval before executing trades via the Schwab API.

### Execution Flow

```
python main.py
  → Config.validate() (fast-fail if Schwab/Discord/Anthropic keys missing)
  → DB init (SQLite, creates tables if absent)
  → Discord bot + APScheduler start
  → Daily cron at SCAN_HOUR:SCAN_MINUTE (default 9:00 AM)
      → run_scan():
          → drain the ops-alert outbox, re-alert on stuck approvals
          → sweep_terminal_recommendations(): ask the broker what each open
            order became; retire the recommendation when it is terminal, follow
            the chain when it was REPLACED. Runs BEFORE the universe is built.
          → expire stale recommendations (>24h)
          → build universe: partition_watchlist() splits watchlist.txt into (stocks, etfs)
                            + S&P 500 from Wikipedia (top 10 by EPS+ROE, 24h cached)
          → for each ticker (skip if recommended today or has open position):
              1. yfinance fundamentals → fundamental filter (P/E, yield, growth)
              2. yfinance news headlines (5 max) → Claude API → BUY/HOLD/SKIP signal
              3. yfinance technicals → technical filter (RSI, MA50, volume)
              4. Write recommendation to DB, post Discord embed with Approve/Reject buttons
          → sell pass (after buy pass): iterate open positions
              → check_exit_signals (RSI > threshold AND MACD bearish)
              → analyze_sell_ticker → SELL/HOLD signal
              → Post red Discord embed with SellApproveRejectView
      → User clicks Approve → place Schwab limit order (skipped unless EXECUTION_MODE=live)
      → /scan_etf command → run_scan_etf(): ETF-only path, skips fundamental filter,
                            uses build_etf_prompt, posts ETF recommendations
```

### Module Responsibilities

| Module | Responsibility |
|---|---|
| `config.py` | Dataclass config loaded from `.env`; `validate()` called at startup |
| `main.py` | Orchestration, scheduler setup, `should_recommend()`, `run_scan()`, `run_reconciliation()`, `sweep_terminal_recommendations()` |
| `screener/universe.py` | Watchlist loading, S&P 500 fetch, deduplication |
| `screener/fundamentals.py` | yfinance fundamental fetch + threshold filter |
| `screener/technicals.py` | RSI (Wilder's, 14-period), MA50, volume filter |
| `screener/macro.py` | Prompt enrichment: SPY 1m/1y trend, VIX level, 52-week range position. All formatters are pure; `fetch_macro_context()` swallows failures and returns `None` values so a macro outage never blocks a scan |
| `screener/exit_signals.py` | Two-gate sell signal: RSI > sell_rsi_threshold AND MACD bearish |
| `screener/positions.py` | `get_position_summary` — live yfinance price + P&L% per open position |
| `analyst/claude_analyst.py` | Prompt building, API call (primary + fallback provider), signal parsing |
|  | Also: `build_sell_prompt`/`analyze_sell_ticker`, `build_etf_prompt`/`analyze_etf_ticker` |
| `analyst/news.py` | Fetch 5 headlines per ticker from yfinance |
| `discord_bot/bot.py` | `TradingBot` (discord.Client), slash commands, Approve/Reject buttons |
| `discord_bot/embeds.py` | Recommendation embed formatting (green/yellow/red) |
| `database/models.py` | SQLite schema: `recommendations` + `trades` tables |
| `database/queries.py` | CRUD for recommendations/trades, expiration, dupe check |
| `schwab_client/auth.py` | OAuth2 via `schwab-py`, token stored at `schwab_token.json` |
| `schwab_client/orders.py` | Market buy order construction, position parsing |
| `schwab_client/reconcile.py` | Pure DB-vs-broker position diff (`diff_positions`) + report formatting |
| `risk/kill_switch.py` | Durable, cross-process trading halt: persisted state, fail-closed reads, `submission_gate()`, audited transitions |
| `risk/preflight.py` | The 12-guard approval table (`evaluate_trade`, `check_authorization`) + the `Quote`/`TradeRequest`/`Decision`/`BrokerSnapshot` types. Pure: no network, no DB, no clock, no mocks needed |
| `schwab_client/quotes.py` | Validated bid/ask (`parse_quote`, `fetch_quote`) + `marketable_sell_limit` pricing |
| `schwab_client/order_payload.py` | Pure Schwab-payload parsing: fills, replacements, `parse_working_orders` + `TERMINAL_BROKER_STATUSES`, `parse_candidate_orders`, `map_broker_status` + `SWEEP_TERMINAL_BROKER_STATUSES` |
| `risk/scan_lock.py` | One scan at a time across BOTH scan paths: `scan_lock()` (one asyncio.Lock per running loop) + `scan_in_progress()`. Outside `main.py` for the same reason as `resolution.py` |
| `risk/resolution.py` | Ambiguous submissions: `report_unknown_submissions` (candidate search, report-only) + `alert_stuck_orders`. Lives outside `main.py` because `discord_bot.bot` needs the report and `main` already imports `TradingBot` |

### Key Design Decisions

- **Two-stage filtering**: Fundamental filter runs before calling Claude (cheap check first), technical filter runs after Claude approves (avoids technical fetch on skipped tickers).
- **Dry-run by default, through ONE variable**: `EXECUTION_MODE` (`dry_run` | `live` | `simulated`) replaced `DRY_RUN` + `PAPER_TRADING`. `paper_trading` was read in exactly one place — a startup warning — and gated **nothing**, while *reading* like a safety layer; Schwab's Trader API has no paper endpoint at all. `dry_run` survives as a **derived, assignable** field (`__post_init__` sets it to `execution_mode != "live"`) rather than a read-only property, because 55 test sites set it to stay off live Schwab and a property would silently strip that protection from any site that was missed.

  **The migration is loud**: `validate()` raises if `DRY_RUN` or `PAPER_TRADING` appear in `os.environ` at all, naming the mode the old settings map to. Silently deriving the new value would reintroduce exactly the unopted-into safety this removes. An unrecognised mode fails startup rather than being guessed at, and anything that is not exactly `live` derives `dry_run=True`, so a typo fails closed twice over.

  **The sink requires BOTH signals to agree** (`_assert_live_execution` in `_call_place_order`, ahead of the kill-switch read). A disagreement fails closed in both directions: `execution_mode='dry_run'` with `dry_run=False` is caught by the first clause, and a test that sets only `dry_run=True` on a live config by the second. This is what makes "structurally incapable of ordering outside live mode" true rather than aspirational — same argument as the kill switch living at the same sink.
- **24-hour recommendation expiry**: Stale records are expired at the start of each scan. The `should_recommend()` function in `main.py` is the single source of truth for dupe prevention. **The expiry predicate is `expires_at <= now`, and it must stay `<=`**: `claim_recommendation_tx` treats a row as live only while `expires_at > now`, so the two are complements. With `<` there was a one-second limbo where a row was neither claimable nor expirable — the Approve button refusing a recommendation that still read `pending`, with nothing in the log to explain it. `expire_stale_recommendations` takes an optional `instant` like every other time-dependent query, and pinning it matters here more than usual: let the clock move and the second ticks over, `<` becomes true on its own, and the test proves nothing.
- **Pure functions for testability**: `should_recommend()`, `configure_scheduler()`, prompt builders, and filter functions are all pure/stateless to enable unit testing without mocking the Discord client or Schwab API.
- **The analyst fallback must be a DIFFERENT MODEL, and it is measured, not assumed**: Gemini enforces quota **per project and per model**, so a second API key buys nothing — the fallback tier's entire value is being a different model. `degenerate_fallback_reason()` warns once per scan when the fallback resolves to the primary, which is one deleted `.env` line away: `_run_with_fallbacks` resolves each tier as `config.<tier>_model or _DEFAULT_MODELS[provider]`, so a gemini fallback with no explicit model silently becomes the primary and retries the quota that just refused it.

  **Measured 2026-08-21** against the real API through the app's own OpenAI-compat path and parser: `gemini-2.5-flash` **9/9** clean, `gemini-3.1-flash-lite` **9/9** clean, and **`gemma-4-31b-it` 0/3 — it echoes the prompt template (`<BUY|HOLD|SKIP>`) and leaks `<thought>` blocks on every call**. Gemma was the configured fallback for four months and could never have helped; the parse-error fallback added in June 2026 was working around exactly this. Do not put a Gemma model back on this path.

  **`gemini-3.7-flash` scored 2/6 HTTP 503 in that probe and it was misread as instability.** The AI Studio dashboard showed the model's **daily quota exhausted** — and the probing itself is what exhausted it. Two things follow. First, **this free tier reports per-model exhaustion as 503, not 429**, at least for this model: `_should_retry` only short-circuits on a `QuotaFailure` detail in the body, so an exhausted model burns all three `@_retry` attempts before the fallback engages. Second, **never diagnose a model from raw error codes without checking the dashboard** — the numbers looked like flakiness and were a spent budget.

  `gemini-3.7-flash` is the primary as of 2026-08-21 but is **unverified in that role**: it could not be tested at the time of the switch because its quota was gone. Re-run the probe after a reset (00:00 PT) before trusting it.

- **Analyst fallback provider**: When the primary analyst call fails, `analyze_ticker()` (and `analyze_etf_ticker`/`analyze_sell_ticker`) falls through the chain `primary → fallback → fallback2`. Configured via `ANALYST_FALLBACK_PROVIDER`/`_API_KEY`/`_MODEL` and `ANALYST_FALLBACK2_*` in `.env`. Both API-level failures (quota exhausted, rate limit, network) **and** parse errors (`ValueError` from `parse_claude_response`, e.g. a template-echo response) trigger the fallback; the failure only propagates once no further fallback client is configured.
- **asyncio.to_thread for all yfinance I/O**: Every yfinance call inside async functions (`fetch_fundamental_info`, `fetch_news_headlines`, `fetch_technical_data`, `partition_watchlist`, `get_top_sp500_by_fundamentals`) must be wrapped in `await asyncio.to_thread(...)` to prevent blocking the Discord gateway heartbeat. Zero bare synchronous yfinance calls on the event loop.
- **Two-gate sell signal**: `check_exit_signals` requires BOTH RSI above threshold AND MACD bearish (macd_line < signal_line). Either condition alone does not trigger a sell recommendation.
- **Analyst quota tracking is PER MODEL, because Google meters per model**: `analyst_calls` is keyed `(date, provider, model)`. Counting happens per **attempt** (`on_attempt(provider, model)`, passed from `main.py` into `_run_with_fallbacks` and invoked before each call) — failed calls burn quota just like successes. Cache hits bypass both the guard and the increment.

  Read off the AI Studio dashboard, free tier, 2026-08-21 — and these are **per model**, not per provider:

  | model | RPM | RPD |
  |---|---|---|
  | `gemini-3.1-flash-lite` | 15 | **500** |
  | every `gemini-*-flash` (2.5, 3, 3.5, 3.6, 3.7) | 5 | **20** |
  | `gemma-4-31b-it` | 30 | 14,400 — but parses 0/3, banned |

  The counter used to be keyed `(date, provider)`. Both tiers of the chain are provider `gemini`, so they **shared one budget** and `all_providers_exhausted` compared a number against itself; a fallback with 25× the primary's allowance was unreachable. Each tier now has its own limit — `ANALYST_DAILY_LIMIT` / `ANALYST_FALLBACK_DAILY_LIMIT` / `ANALYST_FALLBACK2_DAILY_LIMIT` — because one shared number has to be set to the smallest model's, throwing the rest away.

  **`gemini-3.1-flash-lite` is the primary for its quota, not its quality.** At 20 RPD a `*-flash` primary caps the entire day at ~20 analyst calls, which is what held `TOP_SP500_COUNT` at 10. 500 RPD is what makes a real universe affordable (now 50, delay 12.0s → 4.0s for 15 RPM). `gemini-3.7-flash` is the fallback: better per call, but only 20 of them a day.

  **This free tier reports per-model exhaustion as 503, not 429.** `_should_retry` only short-circuits on a `QuotaFailure` detail in the body, so an exhausted model burns all three `@_retry` attempts before the fallback engages. A 503 here is as likely to be a spent budget as a busy server — check the dashboard before concluding a model is flaky, which is a mistake already made once on this path.
- **Market-session day bucketing**: all "today" logic uses the **US market session date** (the `America/New_York` calendar date), via `market_time.market_session_date()` / `market_session_bounds_utc()`. Timestamps stay UTC in storage. `ticker_recommended_today`, `positions.entry_date`, and analyst quota tracking all use it, and each takes an optional `instant` parameter so tests can pin time without freezegun.

  **Both earlier conventions were wrong and must not be reintroduced.** Bare `date('now')` compares UTC days, which roll over mid-afternoon US time. `date(..., 'localtime')` compares the *host's* days — and this host is Asia/Taipei (UTC+8), where a US session (09:30–16:00 ET) runs 21:30–04:00 local and crosses local midnight. With `SCAN_TIMES=21:45,03:30` both scheduled scans are 09:45 ET and 15:30 ET of the **same** session but land on different local dates, so the dupe guard never matched between them and `ANALYST_DAILY_LIMIT` reset mid-session. Prefer the range predicate `created_at >= ? AND created_at < ?` over wrapping the column in `date()` — it stays index-usable.

  The scheduler itself still runs machine-local unless `SCAN_TIMEZONE` (IANA name, e.g. `America/New_York`) is set. Setting it is recommended: it would make the scan times read as market times directly. The startup banner is built by `scheduler_summary()` from the **same list `configure_scheduler` iterates**, and names the timezone. It previously printed `scan_hour`/`scan_minute` — the *fallback* fields, used only when `SCAN_TIMES` is empty — so with `SCAN_TIMES=21:45,3:30` set it announced a scan at 22:00, a time at which nothing runs.
- **Session *date* and intended *session* are two different questions** (round-5 #7). `market_session_date()` answers "which ET calendar day is this instant in" and is right for the dupe guard and the analyst quota. It is **wrong for the order ceiling**: an order entered 20:00 ET Friday has a Friday session date but Schwab queues it for **Monday's** regular session, so bucketing on it let Friday night and Monday each draw a full allowance against Monday's single real ceiling — a fail-*open* doubling caused by nothing but the clock. `intended_session_date()` answers the second question — *the first session whose close is strictly after the instant* — and `orders.intended_session_date` stores it at insert, because nothing later can recover it. `get_day_notional` equality-matches that column; do not "restore" the `submitted_at` range predicate.

  This is the one place the project takes a real exchange calendar (`exchange-calendars`, XNYS), which `market_time.py` otherwise avoids on purpose. It is load-bearing, not convenience: **Good Friday** is a market holiday but not a federal one, so a weekday rule buckets the Thursday night before it into a Friday that never trades; and the **half-day after Thanksgiving closes 13:00 ET**, so a hardcoded 16:00 calls 14:00 "still open" and files it into a session that already ended. `tests/test_market_time.py` pins both, and both were verified to kill a naive implementation before being trusted.

  The calendar is memoised but rebuilds when an instant passes its end (it only spans ~1 year ahead), because a process alive longer would otherwise raise `MinuteOutOfBounds` from inside the order path. Only the *upper* bound rebuilds — an instant before the calendar starts is bad data in a ledger created this month, and should raise.
- **ETF bypass**: ETFs are partitioned out of the stock scan by `partition_watchlist()` using `yfinance quoteType`. They run through `run_scan_etf()` which skips `passes_fundamental_filter` entirely and uses `build_etf_prompt` (no earnings/P/E context).
- **sell_blocked flag**: After a rejected sell, `sell_blocked=True` prevents re-triggering the sell signal for the same position on the same day. Auto-resets when RSI drops back below threshold.
- **One scan at a time, across BOTH scan paths — one lock, not one each**: `risk/scan_lock.py`. A symbol can appear in the stock universe *and* in the ETF universe, so two concurrent scans can reach the same ticker; `ticker_recommended_today` is a read followed by a much later write with network awaits in between, the classic check-then-act race. `idx_active_rec_per_ticker` is the durable backstop but it only turns that race into an `IntegrityError`, and an aborted scan is not a good outcome either.

  **A scan that arrives while one is running is SKIPPED, never queued.** Running it afterwards would screen a market that has already moved on, spend analyst quota a second time, and post recommendations stamped to a moment that has passed. The slash commands answer *before* `create_task`, so the operator is told immediately rather than waiting on results that will never come.

  It is **one lock per running loop** (`WeakKeyDictionary`) — the same trap and fix as `submission_gate()` and `approval_gate()`. Checking `locked()` and then acquiring is *not* a race on one loop: `Lock.acquire()` returns without suspending when the lock is free, so nothing can interleave between the two.

- **Kill switch is durable, cross-process, and fails closed**: state lives in the `kill_switch` table, not a module variable, because a `/halt` typed into the Discord process must stop a scan running in another one and must survive a restart. `is_enabled()` re-reads the DB on every call for that reason. Everything unknown — no row, no table, unreadable DB, unrecognised value — returns False. `TRADING_ENABLED` seeds only a database that has never been written, so a restart can never undo an operator's halt.
- **The submission gate is an `asyncio.Lock`, never a `threading.RLock`**: an RLock is reentrant *per thread* and all coroutines share the loop thread, so `/halt` would acquire the "same gate" as an in-flight submission and walk straight into the critical section — no exclusion at all, while looking correct. It is also **one lock per running loop** (`WeakKeyDictionary`), because a module-level `asyncio.Lock` binds to the first loop that *contends* on it and raises for every loop after; `acquire()`'s uncontended fast path hides this from tests. Both behaviours are pinned by `tests/test_kill_switch_gate.py`. Do not "simplify" either.
- **The preflight guard table is ordered, and the order is load-bearing**: `risk/preflight.py` runs 12 guards and returns the first rejection. **Guard 1 (`unauthorized`) is first** so a rejection message cannot be used as a side channel into the book — an unauthorized clicker must not be able to distinguish a halted bot from a blown ceiling. **Guard 5 (`broker_unavailable`) precedes every guard that consumes broker data**; if it ran after guard 9, exposure would already have been evaluated against an empty list and a broker outage would *open* the ceiling. `None` and `[]` are different inputs throughout (`BrokerSnapshot.readable`): None means the read failed, `[]` means it succeeded and there is nothing. Removing guard 5 was verified to leave no clean refusal at all, only a `TypeError`.

  The module is **pure** — no network, DB, clock, or Discord — so all 46 tests run with zero mocks. Two deliberate departures from spec §8 keep it that way: `trading_enabled` is passed in rather than read via `kill_switch.is_enabled()` (the *authoritative* read stays inside `submission_gate()`, where it must be; guard 2 is only the early friendly rejection), and broker reads arrive as one `BrokerSnapshot` so "the read failed" has exactly one representation. Guards 7–9 price at the **limit**, never the scan price or the raw quote, so each ceiling is checked against the most the order can cost. Guard 4 is session-aware: staleness is enforced during regular hours only, because a 30-second rule would reject every pre-open approval, which is when this system is designed to be used.
- **Broker working orders are fetched, and terminal status is an allowlist**: our ledger is not the whole truth — a buy placed by hand in the Schwab app is *working and unfilled*, so it is in no position and in no local row, and guards 9/10 would both pass while a live order for that symbol exists (round-4 finding 9). `parse_working_orders` returns `{broker_order_id, symbol, side, notional}` for everything **not** in `TERMINAL_BROKER_STATUSES` (`FILLED`/`CANCELED`/`EXPIRED`/`REJECTED`/`REPLACED`). That set is an **allowlist and must stay one** — Schwab's enum contains a literal `UNKNOWN` and can gain members without asking us, so an unrecognised status counts as *live*. Over-counting exposure rejects a legitimate trade (recoverable); under-counting opens the ceiling (not). `FILLED` is terminal because those shares are already position market value; `REPLACED` because its successor is reported separately.

  `notional` is the **unfilled remainder** at the limit — the filled part is already in market value, and reserving it twice double-charges. An unpriceable live buy (market order, or a zero/absent limit) **raises** rather than being skipped: skipping is how a live order comes to reserve nothing. `broker_order_id` is stringified so guard 9's merge actually matches ledger rows.

  `collect_broker_snapshot()` composes both reads and fails **closed**: each failure becomes `None`, never `[]`, and it never raises — the guards, not an exception, must adjudicate a broker outage.
- **Order submission is never retried; its outcome is classified**: `_dispatch` submits **exactly once** and must never carry `@_retry`. A timeout *after* Schwab accepts is an UNKNOWN outcome, not a failure, and the Schwab order API has **no idempotency key** — so a retry is a second chance to buy the same stock, and the duplicate is a real position nobody approved. `classify_submission` splits the outcomes: 2xx+`Location` → `submitted`; 2xx without one → `submit_unknown` (accepted but unidentifiable); 4xx **other than 408/429** → `submit_failed`; 408/429/5xx/timeout/anything unrecognised → `submit_unknown`. **Only `submit_failed` releases capital** (`SubmissionOutcome.reserves_capital`) — an order we cannot account for may still fill, and releasing its reservation lets the next approval spend the same dollars twice. The unrecognised-error default is `submit_unknown` on purpose.
- **The buy approval reserves before it submits, in one `BEGIN IMMEDIATE` transaction**: the sequence is `check_authorization` (pure, *before* `defer()`, so an unauthorized click gets a private reply and costs no broker calls) → defer → **approval gate** → quote + broker snapshot → `BEGIN IMMEDIATE` { read `get_day_notional` + blocking orders → `evaluate_trade` → `claim_recommendation_tx` → **INSERT the order row** } → submit once → `classify_submission`. **The reservation IS the order row** — there is no separate reservation table. `BEGIN IMMEDIATE` takes the write lock *before* the reads, so two processes serialise instead of both reading the same stale daily total (round-4 finding 8); the `asyncio.Lock` alone is process-local and cannot stop a cross-ticker breach during an overlapping restart. Any rejection returns before the INSERT and the transaction rolls back, so a refusal leaves no reservation behind.

  `approval_gate()` is **one lock per running loop** (`WeakKeyDictionary`), the same trap and the same fix as `submission_gate()` — it is *wider*, spanning the whole read→evaluate→claim→submit rather than just the final read through dispatch.

  **Only `submit_failed` reopens the recommendation.** An ambiguous outcome leaves it `approved` on purpose: reopening invites a second human approval and a second real order for something that may already exist. Guard 11 blocks new buys of that ticker meanwhile. `TradingHalted` is classified `submit_failed` because we *know* nothing was dispatched — the one failure where releasing the reservation is correct.

  **The ledger owns ceilings, not positions.** `create_trade`/`upsert_position` still run on a `submitted` outcome: the sell pass, `/positions`, `/stats` and the dupe check all read the positions table, and RISK-05 (recorded on acknowledgement, not fill) is still handled by `run_reconciliation` reporting the drift.

  **The sell side runs the same machinery with a shorter guard list** (1, 2, 3, 4, 5, 11, 12). The buy-only ceilings do not apply — selling reduces exposure, and refusing a sell because the portfolio is large would hold a position through exactly the decline the signal fired on. A sell row reserves no buy capital (`remaining_buy_reservation` returns 0 for sells); it exists for the audit trail and so guard 11 can refuse to compound an unresolved sell. **Guard 12 revalidates the quantity against the broker**, because the view captured `self.shares` when the embed was posted and the position can shrink before anyone clicks. **One quote prices both the guard and the order** — the old path let `place_marketable_sell_order` (since deleted) fetch its own second quote, so the checked price and the sent price could diverge. An ambiguous sell leaves the **position open**: we do not know it sold, and closing it would hide a holding the account may still have.

  **`guard_snapshot()` is the book the guards see**: the broker in live, the SIMULATED positions table in dry run. A broker-sourced guard 12 would refuse every simulated sell (the broker holds nothing in a dry run) and guard 10 would never see a simulated holding — the guards would be evaluating a book unrelated to the one the simulation keeps.

  **Dry run runs the full guard table**, including the kill switch — the old path wrapped that check in `if not dry_run`, so a halted bot still "approved" simulated buys. It writes no order row (a simulated order must not hold real capital against the ceiling) but does record the trade and position, as before.
- **Kill switch is checked in two places on purpose**: `_call_place_order` (the sink — the single choke point every dispatch passes through, so a caller that forgets the gate still fails closed; the `place_*` wrappers that used to be its only callers were deleted as orphans, and `test_kill_switch_wiring.py::test_the_sink_is_the_only_way_into_the_broker` now pins the invariant structurally so a NEW ungated dispatch fails a test) and the approval path (inside the gate, spanning the final read through dispatch). `TradingHalted` is re-raised rather than rewrapped, because a refusal is not a broker failure and "verify in Schwab" would send an operator hunting an order that was never sent.
- **Sells are marketable limits priced through the bid; buys stay passive**: sells price `bid * (1 - APPROVAL_SLIPPAGE_BUFFER_PCT/100)` via `marketable_sell_limit`, rounded **down** to the tick (lower = more marketable for a sell), as a **DAY** order. Buys use `buy_limit_price` — `ask * (1 + buffer)` rounded **up** — as **GTC**. The asymmetry is deliberate: a missed buy costs an opportunity, a missed sell holds the position through the decline the signal fired on. **This becomes wrong if the sell trigger stops being a momentum exit.**

  Both prices are computed **inside the approval path**, from the quote the guards evaluated. `place_marketable_sell_order` has been **deleted**: it fetched a *second* quote of its own, so the price the guards checked and the price sent to the broker could differ. Leaving a second, unguarded way to sell in the module was an invitation to call it.
- **Quote parsing has no defaults, and there is no market-order fallback**: every field in `parse_quote` is mandatory and every failure raises, because `.get("bidPrice", 0)` on an error body prices a sell at give-it-away — the same shape that made `get_positions` read a 401 as "the account holds nothing". Staleness is enforced separately (`QUOTE_MAX_AGE_S`) since a stale quote looks usable; `age_seconds` clamps at zero so clock skew cannot fake freshness. No usable quote means **no sell** — the recommendation re-opens for a human.
- **`/resolve` reports; only a human resolves.** A `submit_unknown` row may or may not exist at the broker, and guard 11 blocks every new order for its ticker until it is settled — so before this existed, one ambiguous submission blocked a symbol **forever, silently**. The candidate search (`parse_candidate_orders` → `find_recent_orders` → `report_unknown_submissions`) never transitions an order, **not even on a single exact match**: matching fields establish an order's *shape*, not its *provenance*, and Schwab exposes no client-supplied correlation id, so two identical buys may both be ours. Only `resolve_order_manually` moves a row, and it demands an actor and an evidence string.

  **`parse_candidate_orders` inverts `parse_working_orders` on two axes, deliberately.** It **includes** `TERMINAL_BROKER_STATUSES` — a `FILLED` candidate is the most dangerous one, meaning a real position the ledger never recorded — and it **does not raise** on an unpriceable candidate, carrying `limit_price=None` so `record_candidate_observation` prices it at our own `reference_price`. Reusing the working-order parser here would hide the worst case and abort whole reports over one market order.

  A failed broker read is **reported, never fatal, and never resolves anything**: `[]` from an outage would look like "no candidate exists", which is exactly what makes `confirmed_absent` — the resolution that *releases capital* — look justified. `alert_stuck_orders` re-alerts on **every** scan past `STUCK_APPROVAL_ALERT_H`, because an alert nobody repeats is a block nobody sees. Gated on `OPS_USER_IDS`, like `/halt`.
- **Ops alerts are a durable outbox**: `send_ops_alert` persists before delivering, so a Discord outage leaves a retryable row rather than a log line; `drain_ops_alerts` retries oldest-first at each scan start and stops at the first still-failing alert (an outage is not a per-alert condition). Neither send nor drain raises — both run inside scans that must not be aborted by their own reporting.
- **The broker's verdict retires a recommendation, and the index cannot ship before it does**: `sweep_terminal_recommendations()` runs at the **start of every scan** (before the universe is built, so a freed ticker is eligible in *that* scan). For each order in `OPEN_ORDER_STATUSES` with a broker id it calls `fetch_order` — the **full payload**, never a status string, because a string cannot carry `replacingOrderCollection` or `filledQuantity` — maps it through `map_broker_status`, and calls `complete_recommendation` when the verdict is terminal. Every failure is **per order and non-fatal**: a sweep that aborts on the first outage leaves every later ticker blocked for reasons unrelated to those tickers, and an outage must never read as "the order is gone".

  **`idx_active_rec_per_ticker` covers `approved`, not just `pending`.** v1 covered `pending` only, so protection lapsed the instant the claim flipped the row — exactly the window between claim and submission. Covering `approved` requires the release valve above; **v2 shipped the index while naming a `completed` transition owned by a poller that was never built**, under which the first buy of any ticker would block that ticker forever. Build the valve first. Never the reverse.

  The migration **logs and continues** if the database already violates the index, rather than refusing to start: the guards, the ledger and the kill switch all work without it, while an operator who cannot start the bot cannot `/halt` it either. `has_active_recommendation()` is the polite check in front of it, called by both scan loops — `ticker_recommended_today` does **not** answer this question, being session-scoped, so an `approved` row left by an ambiguous submission days ago is invisible to it.

- **There are TWO broker terminal-status sets, and REPLACED separates them.** `TERMINAL_BROKER_STATUSES` (working-order parser) asks *"is this still consuming exposure?"* — `REPLACED` is **in** it, because the successor is reported separately and counting both double-charges. `SWEEP_TERMINAL_BROKER_STATUSES` asks *"may I free the recommendation?"* — `REPLACED` is **out**, because the order is alive under an id we do not hold. Both are allowlists. Do not merge them.

  `REPLACED` is resolved by **following the pointer**, never by searching: the successor carries a different price and falls outside any window anchored on the original submission, so a matcher finds nothing and "nothing found" would mark the submission failed while the replacement is live. No successor in the payload ⇒ `submit_unknown` for a human, never terminal.

- **A terminal status is not permission to book a zero fill.** `fills_observed` gates the release of capital, so the sweep flips it only for fills it can trust: a payload with no `filledQuantity` at all, or a quantity with no execution prices to value it at, is written through `mark_order_terminal_unobserved` and keeps its **full** commitment at the limit. `extract_fills` reports `(n, 0.0)` for the unpriceable case deliberately and leaves the decision to the caller; booking that $0 releases the whole reservation for an order that actually filled — round-4 finding 6, in a new place. The one trustworthy zero is a definitively refused order.

- **One screen price, for every candidate that reached `.info`**: `reference_price` is taken from `screen_price(info)` at ALL five post-`.info` exits — `rejected_fundamental`, `skipped_quota_exhausted`, `rejected_signal`, `rejected_technical`, `recommended` — and never from the technical stage. The two are NOT interchangeable: `closes.iloc[-1]` comes from a different endpoint, is fetched minutes later, and yfinance history is **auto-adjusted** while `.info` is raw. Mixing them in one column makes a cohort comparison measure provenance as much as outcome, which is the one thing this subsystem exists to avoid. The technical price still lives in `technicals_json`.

  Pricing only the *rejected* side is not a smaller version of this fix, it is a broken one: `skipped_quota_exhausted` depends on scan order, day and provider budget, so leaving it unpriced makes **markability correlate with how a candidate exited** — a selection effect in the denominator. Before this, only the 4 post-technical sites recorded a price and **78% of the funnel could never be marked** (measured: 39 of 50).

  `screen_price` is **total by contract** — it is passed as an ARGUMENT to `_record_shadow`, so it is evaluated *before* that wrapper's `try`, and a raise would turn a genuine rejection into an `error` observation and could fire an ops alert. It refuses bools (`isinstance(True, int)` is True), NaN and infinity (both floats; NaN compares False to everything), strings, and non-positive values. **`previousClose` is deliberately not a fallback** — a different session silently moves the holding-period start. `reference_price_source` records provenance so the fallback is never silent and backfilled rows stay distinguishable.

- **Marking is bounded, because it runs in front of a market-timed scan**: `mark_due_outcomes` is serial with a 10s yfinance timeout per fetch and sits before the universe is built, so `MAX_MARKS_PER_RUN` / `MARKING_TIME_BUDGET_S` plus `ORDER BY`/`LIMIT` cap the delay. Skipped rows stay due, so a bound costs a delay, never a mark. `pending_shadow_marks` requires `reference_price > 0`, not `IS NOT NULL`: zero makes a row eligible while guaranteeing `compute_return` returns None — a fetch per horizon and four permanently unusable marks. That predicate IS the eligibility invariant, so it belongs in the query rather than in whatever wrote the price.

- **Position reconciliation (report-only)**: `run_reconciliation()` in `main.py` compares DB open positions against the Schwab account (RISK-05: positions are recorded on order acknowledgement, not fill). Runs before each scan's sell pass and via `/reconcile`. It NEVER mutates positions — discrepancies (phantom / untracked / mismatched) are posted as ops alerts for human correction. Skipped entirely in dry run (simulated positions have no broker counterpart). Tests that call `run_scan` must set `config.dry_run = True` (or patch `main.get_positions`) so the suite never touches the live Schwab API.
- **Config reads env at construction, not import**: `Config` fields use `field(default_factory=lambda: os.getenv(...))` (via the `_env_str/_int/_float/_bool` helpers), so `Config()` reflects the environment at call time. The old `= os.getenv(...)` defaults froze at import, forcing `importlib.reload(config)` in env-dependent tests — don't reintroduce that pattern. `test_config.py`'s reload-based USE_LIMIT_BUY tests still pass (now via construction-time reads).
- **Analyst enrichment built only on cache miss**: in `run_scan`, the `fundamental_trend` block (`fetch_eps_data` → `quarterly_income_stmt`, a slow network call) is computed inside the cache-miss branch, after `get_cached_analysis`. A cache hit skips it. The earnings block stays *before* the cache check because `earnings_date_embed` is shown on the recommendation embed even on a hit.
- **S&P 500 ranking — two-tier 24h cache**: `get_top_sp500_by_fundamentals` (~500 yfinance `.info` calls, 10-20 min) caches in-memory, then on disk at `sp500_top_cache.json` (gitignored) so a restart doesn't repay the cost. Both tiers store the **full** ranking and slice per `top_sp500_count` on read, so changing that count never invalidates the cache.
- **Channel object memoized**: `TradingBot._resolve_channel()` fetches the configured channel via `fetch_channel` once and caches it on the instance; the `send_*` methods reuse it instead of an API round-trip per post.

### Configuration

All thresholds and credentials are set via `.env` (see `.env.example`). The `Config` dataclass in `config.py` maps every variable with typed defaults. Safety-critical flags:

```
EXECUTION_MODE=dry_run   # dry_run | live | simulated. Anything but `live` sends nothing.
                         # Replaces DRY_RUN + PAPER_TRADING; leaving either in .env fails startup.
MAX_POSITION_SIZE_USD=500
```

### Database Schema

**`recommendations`**: ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth, status (`pending`/`approved`/`rejected`/`expired`/`completed`), discord_message_id, created_at, expires_at. `idx_active_rec_per_ticker` is a **partial unique index** over `ticker WHERE status IN ('pending','approved')` — at most one live recommendation per symbol

**`trades`**: recommendation_id (FK), ticker, shares, price, order_id, executed_at, side (`buy`/`sell`)

**`positions`**: ticker, shares, avg_price, created_at, updated_at, sell_blocked (bool)

**`analyst_cache`**: ticker, headline_hash (SHA-256 of headlines), signal, reasoning, confidence, created_at; `UNIQUE(ticker, headline_hash)`. It records **neither provider nor model**, so a cache hit cannot be attributed to the model that produced it — worth knowing now that quota is metered per model, and the reason research data must capture both rather than inherit this gap

**`analyst_calls`**: PRIMARY KEY (date, provider, **model**), count — daily quota per model, matching how Google meters. Legacy rows migrated to `model=''` rather than dropped (those calls were really made) or attributed to a current model (they never consumed its budget)

**`shadow_observations`**: every candidate a scan saw, including rejects — `reference_price` is the screen price from `.info` (see above) and `reference_price_source` its provenance; **`shadow_outcomes`**: forward marks per `(observation_id, horizon)`

**`orders`**: the durable order ledger — status, broker_order_id, filled_shares/notional, `fills_observed`, `predecessor_order_id`, `reserved_notional_override`, `intended_session_date` (the session the broker will actually run it in — what the daily ceiling buckets on; backfilled for pre-existing rows, since a NULL would be invisible to the ceiling and fail open)

**`ops_alerts`**: durable outbox — message, delivered_at (NULL = pending), attempts, last_error

**`kill_switch`**: single row (CHECK id=1) holding `UNINITIALIZED`/`ENABLED`/`HALTED`; **`kill_switch_events`**: append-only transition audit

### Technical Indicator Notes

`screener/technicals.py` calculates RSI using Wilder's smoothing (not simple EWM) and requires a minimum of 51 price data points (50-day MA + 1). Tests in `test_screener_technicals.py` use synthetic price series to validate RSI math directly.

### Discord Slash Commands

- `/scan` — manually trigger stock scan (same as scheduled daily run). Replies "a scan is already running" instead of starting a second one
- `/scan_etf` — manually trigger ETF-only scan. Shares `/scan`'s lock, so it refuses while either scan is running
- `/positions` — display open positions with live P&L embed
- `/stats` — win rate and P&L stats for closed trades
- `/history` — last 20 closed trades
- `/reconcile` — compare DB open positions against the Schwab account (report-only; skipped unless `EXECUTION_MODE=live`)
- `/halt` — stop all new order submissions (durable, cross-process; allowlisted via `OPS_USER_IDS`)
- `/resume` — re-enable submissions after a halt (same allowlist — both directions are guarded)
- `/resolve` — the only operator exit from an ambiguous submission (`OPS_USER_IDS`). With no arguments it **reports**: searches the broker for orders that might be ours and shows how each differs from what we submitted. With `order_id` + `resolution` (`adopt`/`confirmed_absent`/`keep_blocked`) + `evidence` it **writes**, through the audited `resolve_order_manually`. An `order_id` without a resolution reports rather than defaulting — half a write is not a write.

Analyst-model helper: `.venv/Scripts/python.exe scripts/probe_analyst_models.py` measures parse reliability against the real API, through the app's own client and parser. Run it before changing `ANALYST_MODEL`/`ANALYST_FALLBACK_MODEL` — both models on this path have been chosen on assumption and been wrong. Each model has its own quota pool, so probing one does not spend another's.

Screen-price backfill: `.venv/Scripts/python.exe scripts/backfill_screen_price.py` recovers `reference_price` for shadow rows recorded before the screen-price policy, from the `.info` dict each row already stores. Preview by default, `--apply` writes, idempotent. A script rather than an `initialize_db` migration on purpose — parsing research JSON per row is exactly the work that fails on malformed data, and an operator who cannot start the bot cannot `/halt` it either.

Funnel report: `.venv/Scripts/python.exe scripts/shadow_report.py` — read-only, every statement a SELECT, safe against the live database while the bot runs.

Pre-flight helper: `.venv/Scripts/python.exe scripts/check_ops_ids.py` reports the operator allowlist and the current kill-switch state, exiting non-zero if nobody is authorized. An empty or all-malformed `OPS_USER_IDS` locks you out of `/halt` **quietly**, which is why it is worth checking before startup.

### Test Suite

1220 tests as of 2026-08-22. Run with `.venv/Scripts/python.exe -m pytest -q` (~25s). Key test files:
- `test_order_status_sweep.py` / `test_order_status_mapping.py` / `test_active_rec_index.py` — step 11: chain-following, the sweep, the trustworthy-fill rule, and the index that cannot ship before its release valve (48 tests)
- `test_scan_lock.py` — one scan at a time: the shared lock, the per-loop binding trap, and the two slash commands (10 tests)
- `test_per_model_quota.py` — quota metered per model, and the `analyst_calls` rebuild against a PRE-EXISTING table (8 tests)
- `test_execution_mode.py` — `EXECUTION_MODE`: the derived `dry_run`, the loud migration, and the sink's both-signals-must-agree guard (21 tests)
- `test_resolve_reporting.py` / `test_resolve_command.py` — `/resolve`: the candidate search never mutates order status, terminal candidates are *included* (the inverse of `parse_working_orders`), worst-case reservation, the ops allowlist, and the repeating stuck-order alert (59 tests)
- `test_kill_switch.py` / `test_kill_switch_gate.py` / `test_kill_switch_wiring.py` / `test_halt_commands.py` — the kill switch (61 tests)
- `test_quotes.py` / `test_marketable_sells.py` — validated quotes + sell pricing (36 tests). `test_marketable_sells.py` is now spec construction only; a comment block at its foot names where each retired `place_marketable_sell_order` assertion moved to
- `test_ops_alert_outbox.py` — durable ops-alert outbox (22 tests)
- `test_preflight.py` — the 12-guard approval table, including the two ordering rules (46 tests, zero mocks)
- `test_approval_ledger.py` / `test_sell_approval_ledger.py` — the approval paths on the guards + ledger: reservation, ceiling, outcome classification, guard-12 requantification (37 tests)
- `test_submission_outcomes.py` — submission is never retried, and its outcome is classified (23 tests)
- `test_working_orders.py` — broker working orders: the terminal-status allowlist, unpriceable orders, and the fail-closed snapshot (42 tests)
- `test_intended_session_attribution.py` / `test_market_time.py` — session attribution: the ceiling buckets on the session an order *executes* in, not the one it was entered in (round-5 #7)
- `test_db_migrations.py` — schema upgrades against a PRE-EXISTING database
- `test_screener_technicals.py` — RSI math with synthetic price series
- `test_exit_signals.py` — RSI + MACD gate (16 tests, 2×2 matrix)
- `test_sell_scan.py` — run_scan sell pass integration (9 tests)
- `test_sell_buttons.py` — SellApproveRejectView async handlers (9 tests)
- `test_positions.py` — positions CRUD including weighted-avg price (11 tests)
- `test_discord_buttons.py` — ApproveRejectView handlers (10 tests)
