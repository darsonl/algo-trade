# Stack Research — v1.2

**Project:** Algo Trade Bot — v1.2 Signal Quality & Portfolio Analytics
**Researched:** 2026-04-12
**Scope:** Additions needed for new v1.2 features only. Existing validated stack (Python, discord.py,
yfinance 0.2.51, anthropic/openai, schwab-py, APScheduler 3.10.4, SQLite) is not re-litigated.

---

## Summary

All nine v1.2 features are achievable with the existing stack. No new pip packages are required.
The work divides into four categories: yfinance info-dict field reads (already fetched, just
unused), a new standalone yfinance fetch for SPY/VIX macro context, APScheduler second-job
registration, and analyst pipeline + DB schema extensions for confidence scoring. The highest
implementation risk is the confidence scoring change — it touches four layers (prompt, parser,
DB schema, embeds) and interacts with the analyst cache.

---

## Feature Stack Analysis

### SIG-01 — Sector name in Claude prompts

- **yfinance fields / API**: `info.get("sector")` — present in the `yf_ticker.info` dict that
  `fetch_fundamental_info` already returns (it returns the full `.info` dict as-is). For ETFs,
  `sector` is typically absent; graceful fallback to `"N/A"` is correct. Confidence: MEDIUM —
  yfinance is an unofficial scraper; `sector` is a standard field but Yahoo Finance can return
  `None` for some tickers without warning.
- **New package needed**: No.
- **Integration notes**: No additional network call. `fetch_fundamental_info` already fetches and
  returns the full `info` dict. `build_prompt` and `build_sell_prompt` each receive the `info`
  dict as a parameter — add `info.get("sector", "N/A")` interpolation into the prompt string.
  `build_etf_prompt` currently takes explicit keyword args; add `sector: str | None = None` as
  a new kwarg. All three prompt builders are pure functions — the change is string interpolation
  only. No DB change required; sector is prompt context, not stored.

### SIG-02 — SPY 5-day trend + VIX level in Claude prompts

- **yfinance fields / API**:
  - SPY 5-day close trend: `yf.Ticker("SPY").history(period="5d")["Close"]` — returns a
    pd.Series of closing prices. Net 5-day move: `closes.iloc[-1] - closes.iloc[0]`. Compute
    as a percentage: `(closes.iloc[-1] / closes.iloc[0] - 1) * 100`. The `history` method is
    already used in `fetch_technical_data` (period="3mo"); period="5d" is supported.
  - VIX level: `yf.Ticker("^VIX").fast_info.last_price` — single float. The `fast_info`
    accessor is already used in `screener/positions.py` for live price lookups. `^VIX` is the
    Yahoo Finance ticker for the CBOE Volatility Index. Returns the last available price
    (previous close when market is closed). Confidence: MEDIUM — `fast_info.last_price` is a
    relatively recent yfinance API surface; it works in 0.2.x but is less tested than
    `.history()`. Fallback: catch any exception and set `vix_level = None`.
- **New package needed**: No.
- **Integration notes**: Macro data is scan-level context — fetch once per `run_scan` /
  `run_scan_etf` call, before the ticker loop. Both fetches are blocking yfinance calls and must
  be wrapped in `await asyncio.to_thread(...)` per the project's mandatory pattern. Store as two
  local variables (`spy_trend_pct: float | None`, `vix_level: float | None`) and pass them into
  each `build_prompt` / `build_sell_prompt` / `build_etf_prompt` call as optional keyword args.
  Both fetches need `try/except` guards — missing macro context shows "N/A" in the prompt, not
  a crash. A helper function `fetch_macro_context() -> tuple[float | None, float | None]` in a
  new or existing screener module keeps `run_scan` clean.

### SIG-03 — 52-week range in Claude BUY/SELL prompts

- **yfinance fields / API**: `info.get("fiftyTwoWeekHigh")` and `info.get("fiftyTwoWeekLow")` —
  both present in the `yf_ticker.info` dict already fetched by `fetch_fundamental_info`. These
  are standard yfinance fields populated for equities. ETFs also have them. Confidence: HIGH —
  these are among the most stable yfinance info fields.
  Derived value: `week52_position_pct = (current_price - week52_low) / (week52_high - week52_low)`
  where `week52_high != week52_low`. Guards needed: both fields can be `None` for newly-listed
  tickers; compute only when both are present and non-equal.
- **New package needed**: No.
- **Integration notes**: Fields are read from the already-fetched `info` dict — zero extra network
  calls. Pass `week52_high`, `week52_low` (and optionally the derived `week52_position_pct`) into
  `build_prompt` and `build_sell_prompt`. ETF prompt can also receive them via `build_etf_prompt`
  kwargs. No DB change required.

### SIG-04 — Confidence scoring (high/medium/low badge in Discord embed)

- **yfinance fields / API**: None — analyst output and display change only.
- **New package needed**: No.
- **Integration notes**: This is the most cross-cutting v1.2 change. It touches five layers:

  **1. Prompt builders** — All three prompt functions (`build_prompt`, `build_sell_prompt`,
  `build_etf_prompt`) must request a third output line. The current two-line format becomes:
  ```
  SIGNAL: <BUY|HOLD|SKIP>
  REASONING: <2-3 sentences>
  CONFIDENCE: <HIGH|MEDIUM|LOW>
  ```

  **2. Parser** — `parse_claude_response` in `claude_analyst.py` currently extracts `signal`
  and `reasoning`. Extend to also extract `CONFIDENCE:` line. Add
  `_VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}`. Missing CONFIDENCE line must default to
  `confidence = None` (not raise ValueError) — LLMs occasionally omit optional lines, and
  breaking the entire parse for a missing badge is the wrong tradeoff. Invalid confidence
  value (not in the valid set) should also degrade to `None`, not raise.

  **3. DB schema** — Add `confidence TEXT` column to `recommendations` via additive migration
  in `initialize_db`. Pattern is established: `ALTER TABLE recommendations ADD COLUMN confidence TEXT`
  wrapped in `try/except sqlite3.OperationalError`. Update `create_recommendation` in
  `queries.py` to accept `confidence: str | None = None` and include it in the INSERT.
  Also add `confidence TEXT` column to `analyst_cache` with the same migration pattern, and
  update `get_cached_analysis` / `set_cached_analysis` to carry `confidence` through the cache.
  Old cache rows will have `confidence = NULL` — all callers must handle `None` confidence.

  **4. Embeds** — `build_recommendation_embed`, `build_sell_embed`,
  `build_etf_recommendation_embed` in `embeds.py` accept a new `confidence: str | None`
  parameter and display it as a Discord embed field. Suggested display: prefix with a colored
  indicator character (HIGH = green circle, MEDIUM = yellow circle, LOW = red circle). Discord
  does not support inline color; the character approach is the standard community pattern.

  **5. Bot send methods** — `send_recommendation`, `send_sell_recommendation`,
  `send_etf_recommendation` in `bot.py` need `confidence` threaded through to the embed builders.
  `run_scan` / `run_scan_etf` in `main.py` pass `confidence` from `analysis["confidence"]`
  (where `analysis` is the dict returned by `analyze_ticker` / `analyze_etf_ticker`).

### ETF-07 — Scheduled ETF scan at time offset

- **yfinance fields / API**: None — scheduling change only.
- **New package needed**: No — APScheduler 3.10.4 already in stack.
- **APScheduler job config**: The existing `configure_scheduler` registers stock scan jobs with
  IDs `scan_0`, `scan_1`, etc. using `CronTrigger(hour=h, minute=m)`. A parallel
  `configure_etf_scheduler` function follows the same pattern:
  ```python
  scheduler.add_job(
      etf_job_fn,
      trigger=CronTrigger(hour=config.etf_scan_hour, minute=config.etf_scan_minute),
      id="etf_scan_0",
      replace_existing=True,
  )
  ```
  ETF job IDs must use a distinct prefix (`etf_scan_0`) to avoid colliding with stock job IDs
  (`scan_0`). APScheduler raises if two jobs share an ID and `replace_existing=False`.
- **Config additions needed**: Two new `Config` fields:
  - `etf_scan_hour: int = int(os.getenv("ETF_SCAN_HOUR", "9"))`
  - `etf_scan_minute: int = int(os.getenv("ETF_SCAN_MINUTE", "30"))`
  Default of 30-minute offset from the stock scan (9:00 → 9:30) avoids rate-limit contention.
- **Integration notes**: The `on_ready` coroutine in `main.py` calls `configure_scheduler` then
  `scheduler.start()`. Add a `configure_etf_scheduler` call between those two lines, before
  `scheduler.start()`. The `bot._scan_etf_callback` lambda is already wired in `main.py` —
  the scheduler job wraps it the same way the stock job wraps `run_scan`.

### ETF-08 — `[ETF]` ops alert prefix

- **yfinance fields / API**: None.
- **New package needed**: No.
- **Integration notes**: `run_scan_etf` already calls `bot.send_ops_alert(...)` for zero
  recommendations. Change the message string to `"[ETF] Scan complete: 0 recommendations posted."`.
  One-line change in `main.py`.

### ETF-09 — Expense ratio threshold filter

- **yfinance fields / API**: `info.get("netExpenseRatio")` — already fetched in `run_scan_etf`
  at `main.py` line 318. No new fetch needed. Note the codebase uses `netExpenseRatio` as the
  primary key and falls back gracefully when it is `None`. The v1.1 STACK.md references
  `annualReportExpenseRatio` as an alternative key seen in tests — both may be present; `netExpenseRatio`
  is used in the live code path and should remain the primary key.
- **New package needed**: No.
- **Integration notes**: Add one `Config` field: `max_etf_expense_ratio: float = float(os.getenv("MAX_ETF_EXPENSE_RATIO", "0.005"))`. In `run_scan_etf`, after fetching `expense_ratio`, add a filter gate:
  ```python
  if expense_ratio is not None and expense_ratio > config.max_etf_expense_ratio:
      logger.debug("Skipping %s: expense ratio %.4f exceeds %.4f threshold", ...)
      continue
  ```
  When `expense_ratio is None`, skip the filter (same missing-data policy used throughout). The
  embed already displays the expense ratio field — no embed change needed.

### PORT-01 — Total unrealized P&L aggregate in `/positions`

- **yfinance fields / API**: `yf.Ticker(ticker).fast_info.last_price` — already used in
  `screener/positions.py` via `get_position_summary`. No new fields.
- **New package needed**: No.
- **Integration notes**: `get_position_summary` already returns per-position dicts with
  `current_price`, `shares`, and `avg_cost_usd`. Total unrealized P&L:
  ```python
  total_pnl_usd = sum(
      (s["current_price"] - s["avg_cost_usd"]) * s["shares"]
      for s in summaries
      if s["current_price"] is not None
  )
  ```
  This summation belongs either in `_positions_command` in `bot.py` or passed as a parameter
  to `build_positions_embed`. The embed should show the aggregate as a footer or summary field.
  No DB change, no new fetch.

### PORT-02 — `/stats` slash command (win rate, avg gain, avg loss)

- **yfinance fields / API**: None — DB query against existing `trades` table only.
- **New package needed**: No — standard `sqlite3` module already used throughout `queries.py`.
- **SQLite query shape for closed trade stats**: The `trades` table has `recommendation_id`,
  `ticker`, `price`, `side` (`buy`/`sell`), and `executed_at`. Pairing buy and sell by
  `recommendation_id`:
  ```sql
  SELECT
      t.ticker,
      t.price       AS exit_price,
      b.price       AS entry_price,
      t.executed_at AS exit_at
  FROM trades t
  JOIN trades b
    ON b.recommendation_id = t.recommendation_id
   AND b.side = 'buy'
  WHERE t.side = 'sell'
  ORDER BY t.executed_at DESC
  ```
  This returns one row per closed trade. Compute stats in Python (not SQL) for testability:
  ```python
  gains = [(row["exit_price"] - row["entry_price"]) / row["entry_price"] for row in rows]
  wins   = [g for g in gains if g > 0]
  losses = [g for g in gains if g <= 0]
  win_rate  = len(wins) / len(gains) if gains else None
  avg_gain  = sum(wins) / len(wins) if wins else None
  avg_loss  = sum(losses) / len(losses) if losses else None
  ```
  Add `get_trade_stats(db_path: str) -> dict` to `database/queries.py` returning
  `{"total_trades": int, "win_rate": float|None, "avg_gain": float|None, "avg_loss": float|None}`.
  Register the `/stats` slash command in `setup_hook` in `bot.py` alongside `/scan`, `/scan_etf`,
  `/positions`. The handler calls `get_trade_stats` via `asyncio.to_thread` and formats a Discord
  embed. When no closed trades exist, respond with a plain message ("No closed trades yet.") rather
  than computing stats on an empty list.

### OPS-01 — Scan exceptions posted to Discord ops channel

- **yfinance fields / API**: None.
- **New package needed**: No.
- **Integration notes**: `send_ops_alert` already exists on `TradingBot` and posts to
  `config.discord_channel_id`. Two distinct error surfaces:

  **Per-ticker errors** are already caught by `try/except Exception` blocks in `run_scan` and
  `run_scan_etf`. Currently they `logger.error(...)` and `continue`. Add a
  `await bot.send_ops_alert(f"[SCAN ERROR] {ticker}: {exc}")` call inside those handlers. To
  avoid Discord spam on a bad scan (e.g. all 20 tickers fail), gate the alert on a per-scan
  error count: only post to Discord after the 3rd+ error in a single scan run.

  **Scan-level exceptions** (uncaught exceptions that escape `run_scan` entirely) are currently
  swallowed by APScheduler or the `asyncio.run_coroutine_threadsafe` wrapper. The cleanest fix
  is a thin wrapper in `main.py`:
  ```python
  async def _guarded_scan(bot, config):
      try:
          await run_scan(bot, config)
      except Exception as exc:
          logger.error("Unhandled scan exception: %s", exc, exc_info=True)
          await bot.send_ops_alert(f"[SCAN FAILED] Unhandled exception: {exc}")
  ```
  Replace `run_scan(bot, config)` with `_guarded_scan(bot, config)` in the scheduler lambda and
  the `/scan` command callback. Mirror the same pattern for ETF scan.

---

## New Dependencies

| Package | Version | Purpose | Required? |
|---------|---------|---------|-----------|
| (none) | — | All v1.2 features use the existing stack | — |

**No new packages required.** All API surface needed for v1.2 is available in the currently
pinned versions. Confirmed surface used:

| Library | Version | v1.2 Surface Used |
|---------|---------|------------------|
| yfinance | 0.2.51 | `info["sector"]`, `info["fiftyTwoWeekHigh"]`, `info["fiftyTwoWeekLow"]`, `Ticker("^VIX").fast_info.last_price`, `Ticker("SPY").history(period="5d")` |
| APScheduler | 3.10.4 | Second `scheduler.add_job(...)` call with distinct `id="etf_scan_0"` |
| discord.py | 2.4.0 | New `/stats` slash command via existing `app_commands.Command` pattern |
| sqlite3 | stdlib | New `get_trade_stats` JOIN query; additive `ALTER TABLE` migrations for `confidence` column |
| anthropic / openai SDK | 0.40.0 / >=1.0.0 | No API changes — prompt string and response parser only |

---

## Risks

### yfinance field availability is not guaranteed (MEDIUM confidence)

`sector`, `fiftyTwoWeekHigh`, `fiftyTwoWeekLow` are standard `info` dict keys but yfinance is an
unofficial scraper. Yahoo Finance can change response shapes silently. Mitigation: always use
`info.get("key")` (never bracket access) and treat `None` as "N/A" in prompts. This pattern is
already established throughout the codebase. `^VIX` via `fast_info.last_price` is less exercised
than standard equity tickers — wrap in `try/except` with `vix_level = None` fallback.

### Confidence scoring touches five layers simultaneously (HIGH risk, implementation only)

Changing `parse_claude_response` to expect a CONFIDENCE line affects all three prompt paths. The
parser must remain permissive: missing CONFIDENCE line becomes `confidence = None`, not a parse
failure. This is critical because:
1. The analyst cache currently stores `{signal, reasoning}` — cache hits would return no
   confidence until cache rows are regenerated. Code must handle `None` confidence everywhere.
2. Existing tests in `test_analyst_claude.py` assert on two-line responses — they will need
   updating to either three-line responses or explicit `confidence=None` assertions.
3. The `_VALID_SIGNALS` check raises `ValueError` on bad signal values; confidence must NOT
   raise on missing/invalid values to avoid a cascade where one omitted line fails the full scan.

### APScheduler ETF job ID collision

If `configure_scheduler` and `configure_etf_scheduler` use the same job ID prefix, APScheduler
will silently overwrite jobs if `replace_existing=True`. Use `etf_scan_0` for ETF jobs and
`scan_0` for stock jobs. The existing test `test_scheduler_has_one_job_after_configure` asserts
`len(scheduler.get_jobs()) == 1` after `configure_scheduler` — adding a new `configure_etf_scheduler`
function (separate from `configure_scheduler`) keeps that test valid without modification.

### `/stats` query assumes trades table integrity

The JOIN query pairing sell trades to buy trades via `recommendation_id` assumes every sell has
exactly one corresponding buy. This holds for the current flow. Positions opened before the bot
was deployed have no `recommendation_id` — those trades are silently excluded from stats, which
is the correct behavior (stats on bot-managed trades only). No defensive query change needed.

### DB migration ordering

Adding `confidence` to `recommendations` and `analyst_cache` requires two new `ALTER TABLE`
migrations in `initialize_db`. Apply both in the same function, after existing migrations, using
the established `try/except sqlite3.OperationalError: pass` pattern.
