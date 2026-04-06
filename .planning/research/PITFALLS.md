# Domain Pitfalls

**Domain:** Adding ETF scan separation + asyncio.to_thread hardening to a working Python Discord trading bot
**Researched:** 2026-04-06
**Confidence:** HIGH — all pitfalls derived from direct codebase inspection, not speculation

---

## Critical Pitfalls

Mistakes that cause silent failures, broken tests, gateway disconnections, or require rewrites.

---

### Pitfall 1: Double-wrapping get_top_sp500_by_fundamentals in asyncio.to_thread

**What goes wrong:** `main.py` line 61 already wraps `get_top_sp500_by_fundamentals` in `asyncio.to_thread`. Phase 8 will audit all blocking yfinance calls and wrap them. If the Phase 8 implementer wraps this function a second time — either by wrapping the call site again, or by making the function itself call `asyncio.to_thread` internally — the result is calling `asyncio.to_thread` from inside a thread pool thread. This raises `RuntimeError: no running event loop` because `asyncio.to_thread` requires a running event loop in the calling thread, and the thread pool thread has none.

**Why it happens:** Phase 7 adds `partition_watchlist` which also calls yfinance per ticker. The Phase 8 implementer sees "wrap all yfinance calls" and wraps the partition call too. Meanwhile `get_top_sp500_by_fundamentals` is already wrapped, making it a target for double-wrapping if the audit is not cross-phase aware.

**Consequences:** `run_scan` crashes at startup with an opaque RuntimeError, not a yfinance error. The hotfix from the backlog (noted in PROJECT.md backlog 999.1) disappears into Phase 8 re-implementation if it is not explicitly marked as already done.

**Prevention:**
- Add a comment directly on the `asyncio.to_thread(get_top_sp500_by_fundamentals, config)` call in `main.py`: `# already wrapped — do not wrap again in Phase 8`
- Phase 8 must begin by listing all call sites already wrapped before adding new wrapping
- Functions that must run on a thread pool worker must never themselves call `asyncio.to_thread`; they must stay pure sync

**Detection:** `RuntimeError: no running event loop` in logs at scan startup; or a pytest `asyncio.run()` call inside a test that is already inside `pytest.mark.asyncio`

---

### Pitfall 2: partition_watchlist triggering N blocking yfinance calls on the Discord event loop

**What goes wrong:** `partition_watchlist(tickers)` will call `yf.Ticker(t).info` (or equivalent) for every ticker in the watchlist at scan startup to retrieve `quoteType`. If this function is called directly from `run_scan` — an async function running on the Discord event loop — without wrapping in `asyncio.to_thread`, every one of those N HTTP calls blocks the event loop. For a watchlist of 20 tickers, that is 20 synchronous HTTP calls blocking Discord's heartbeat.

**Why it happens:** The existing pattern in `run_scan` calls sync functions like `fetch_fundamental_info` and `get_universe` directly (they are not blocking in a meaningful way because `get_universe` is pure logic). The new `partition_watchlist` looks similar in call signature but hides N network calls inside it, making it easy to treat it the same way.

**Consequences:** Discord gateway sends heartbeat ACK timeout after ~41.25 seconds of event loop blocking. The bot disconnects and reconnects, dropping any pending interactions. The APScheduler thread's `.result()` call (main.py line 336–338) blocks for the entire duration. For a 30-ticker watchlist with 0.15s sleep per ticker, that is at least 4.5 seconds of pure blocking before the scan loop even starts.

**Prevention:**
- `partition_watchlist` must be called inside `asyncio.to_thread` in `run_scan`, same as `get_top_sp500_by_fundamentals`
- The function signature must remain pure sync (no async), so it can be thread-safe and testable without an event loop
- Add the `asyncio.to_thread` wrapping in Phase 7 when `partition_watchlist` is introduced, not deferred to Phase 8

**Detection:** Discord logs `Heartbeat blocked for more than X seconds`; bot shows as offline during scan; `asyncio.get_event_loop()` debug mode raises `BlockingIOError`

---

### Pitfall 3: yfinance quoteType field is absent or None for tickers that appear in the watchlist

**What goes wrong:** `partition_watchlist` will read `info.get("quoteType")` to classify tickers as ETF vs stock. The `quoteType` field is not present in all yfinance responses. It is absent when: (a) yfinance returns a minimal response due to rate limiting, (b) the ticker is delisted or unrecognized, (c) the Yahoo Finance scraper returns a partial HTML page. When `quoteType` is `None` or missing, a naive `if info["quoteType"] == "ETF"` raises `KeyError`; a `.get()` fallback silently routes the ticker to the wrong scan path.

**Why it happens:** yfinance is an unofficial scraper (confirmed in PROJECT.md context note). The `.info` dict can return with 2–3 keys or 50+ keys depending on whether Yahoo Finance returned a full response. There is no schema contract. The existing `fetch_fundamental_info` already handles this by using `.get()` with `None` checks, but `partition_watchlist` is new code that must deliberately match this defensive pattern.

**Consequences:**
- ETFs classified as stocks pass into the stock fundamental filter, which returns `False` for them (no earnings growth) and silently skips them — but this was the behavior before Phase 7 and is not a regression
- Stocks classified as ETFs bypass the fundamental filter and get sent to Claude for a ticker where the fundamental screen would have rejected them, wasting analyst quota
- If fallback is to route unknown tickers to the stock path, this is the safer default

**Prevention:**
- Use `info.get("quoteType", "").upper() == "ETF"` — never index directly
- Log a `DEBUG` message when `quoteType` is absent: `"quoteType missing for {ticker}, defaulting to stock path"`
- Add a static allowlist fallback: if `ticker in KNOWN_ETF_SYMBOLS`, classify as ETF regardless of `quoteType` result (the `_ETF_ALLOWLIST` already exists in `screener/fundamentals.py` — reuse it)
- Test the partition function with a mock returning `{}`, `{"quoteType": None}`, `{"quoteType": "ETF"}`, `{"quoteType": "EQUITY"}`

**Detection:** SPY/QQQ appear in Claude analyst logs during an ETF scan (analyst should never be called for ETFs in the buy pass); or stock like AAPL missing from scan results because `quoteType` returned "MUTUALFUND" transiently

---

### Pitfall 4: ETF recommendations break create_recommendation because it requires earnings_growth

**What goes wrong:** `queries.create_recommendation` is called with `earnings_growth=info.get("earningsGrowth")`. For ETFs, `earningsGrowth` is always `None` in yfinance (ETFs do not report earnings). The `recommendations` table schema accepts `NULL` for `earnings_growth`, so the DB write itself succeeds. The problem is downstream: the Discord embed (`build_recommendation_embed`) and any query that reads `earnings_growth` for display or filtering will receive `None`. If any code path does `earnings_growth * 100` or `f"{earnings_growth:.1%}"` without a None guard, it will raise `TypeError` at the point the ETF rec is posted.

**Why it happens:** The existing embed and recommendation flow was designed for stocks. `earnings_growth` being `None` for a stock means the fundamental filter rejected it earlier, so no stock with `earnings_growth=None` ever reaches `create_recommendation`. ETFs skip the fundamental filter entirely, so they will be the first tickers to reach `create_recommendation` with `earnings_growth=None` in normal operation.

**Consequences:** `TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'` raised inside `send_recommendation`, causing the Discord post to fail silently (caught by `except Exception` in `run_scan`). ETF recommendation is written to DB but no Discord embed is posted. The 222-test suite passes because no existing test sends an ETF recommendation end-to-end.

**Prevention:**
- Audit `build_recommendation_embed` and any embed field that formats `earnings_growth`; add `if earnings_growth is not None` guards before formatting
- Add an integration-style test in `test_discord_embeds.py`: `build_recommendation_embed("SPY", "BUY", "reason", 450.0, None, None)` — this should not raise

**Detection:** `TypeError` in bot logs on first `/scan_etf` execution; `None` appearing as literal text "None%" in the Discord embed

---

### Pitfall 5: asyncio.to_thread test mocks break when the wrapped function is a sync function under patch

**What goes wrong:** Existing tests in `test_run_scan.py` patch `main.analyze_ticker` directly because `analyze_ticker` is a sync function and `asyncio.to_thread` is transparent to `patch` — the patch replaces the function reference that `asyncio.to_thread` receives, and the mock is called synchronously in the test's event loop thread. This works today. When Phase 7 adds `asyncio.to_thread(partition_watchlist, ...)`, the same pattern applies and will work. However, if a test patches `screener.universe.partition_watchlist` instead of `main.partition_watchlist`, the patch misses the reference that `asyncio.to_thread` holds, because `main.py` imported the function at module load time. The mock is never called, the real function runs, and it makes actual yfinance network calls during the test.

**Why it happens:** The codebase uses `from screener.universe import get_top_sp500_by_fundamentals` at the top of `main.py` (line 17). When `asyncio.to_thread(get_top_sp500_by_fundamentals, config)` is called, it has a direct reference to the function object, not a module attribute lookup. Patching `screener.universe.get_top_sp500_by_fundamentals` after import does not intercept the already-bound reference. The existing tests in `test_run_scan.py` correctly patch `main.get_top_sp500_by_fundamentals`, which works because `main` still holds the name. All new `to_thread` wrapping of imported functions must be patched at `main.<function_name>`, not at the source module.

**Consequences:** Tests that patch the wrong target run the real yfinance function, make network calls in CI, fail intermittently with connection errors, and take 30+ seconds per test run.

**Prevention:**
- Establish a rule: patch the module where the `asyncio.to_thread` call lives, not the module where the function is defined. For all calls in `main.py`, patch `main.<name>`.
- Add this as a comment in `test_run_scan.py`'s `_full_patch` context manager where the pattern is established: `# Always patch at main.<name>, not at the source module`
- When Phase 7 adds `partition_watchlist` to `run_scan`, add its mock to `_full_patch` immediately, not in a follow-up test

**Detection:** Test passes locally (yfinance cached), fails in CI (no cache, real network call times out); `assert mock_fn.called` fails even though the function appears to be patched

---

### Pitfall 6: Separate /scan_etf command shares the same scan_callback slot, breaking /scan

**What goes wrong:** `TradingBot` has a single `_scan_callback` attribute set in `main.py`: `bot._scan_callback = lambda: run_scan(bot, config)`. The `_scan_command` handler reads this slot. If `/scan_etf` is implemented by adding a second callback attribute (`_scan_etf_callback`) on the bot, this works. But if the implementer instead reassigns `_scan_callback` to the ETF scan function during setup (thinking it should be the default), the `/scan` command now runs an ETF scan instead of a stock scan. Because both scans post to the same Discord channel, this will not be immediately obvious — the recommendations will look normal but ETF-only.

**Why it happens:** The `_scan_callback` pattern is a simple slot with no type guard. It is set once in `main.py` and referenced by `_scan_command`. The pattern is not documented as "one slot, one function." New implementers may assume it is the general "run a scan" hook.

**Consequences:** The `/scan` command silently runs ETF scan logic only. Stock scan never fires manually. The scheduled cron job (which calls `run_scan` directly, not via `_scan_callback`) still runs correctly, masking the bug during normal operation. The bug only appears during manual `/scan` invocations.

**Prevention:**
- In `setup_hook`, register `/scan_etf` as a separate `app_commands.Command` with its own inline callback
- Do not reuse `_scan_callback` for ETF scan; ETF scan should be invoked directly from its own command handler via `asyncio.create_task(run_etf_scan(bot, config))`
- The test for `/scan_etf` must assert that `run_scan` (stock version) is NOT called when `/scan_etf` is invoked

**Detection:** After a `/scan` command, Discord channel shows only ETF tickers (SPY, QQQ) but no stock tickers — subtle because ETF scans are infrequent

---

## Moderate Pitfalls

### Pitfall 1: ETF scan writes to recommendations table without an asset_type column, making historical queries ambiguous

**What goes wrong:** The `recommendations` table has no column distinguishing ETF recommendations from stock recommendations. After Phase 7, the table will contain a mix of rows for ETFs and stocks. Any query that reads historical recommendations to compute hit rate, review past signals, or check duplicate suppression will treat them uniformly. The `ticker_recommended_today` check will correctly suppress duplicate ETF recs, but there is no way to query "all ETF recs this week" without pattern-matching on ticker symbol.

**Why it happens:** The schema is designed for stocks. The PROJECT.md constraint says "schema changes must be additive." Adding an `asset_type` column satisfies this constraint. Not adding it creates silent ambiguity.

**Prevention:**
- Add `asset_type TEXT NOT NULL DEFAULT 'stock'` to the `recommendations` table via `ALTER TABLE` migration in `initialize_db`, same pattern as the existing `earnings_growth` migration
- Pass `asset_type='etf'` when calling `create_recommendation` from the ETF scan path
- Do this in Phase 7 when ETF recommendations are first written — not as a follow-up

**Detection:** Ops tries to count ETF recs in DB with `SELECT COUNT(*) FROM recommendations WHERE ticker='SPY'` — works today, but will be necessary as the table grows

---

### Pitfall 2: APScheduler run_coroutine_threadsafe + ETF scan creates two blocking .result() calls

**What goes wrong:** The APScheduler job in `on_ready` already calls `.result()` on `run_scan`, blocking the APScheduler thread for the full scan duration. If Phase 7 adds a second scheduled ETF scan job using the same pattern (`run_coroutine_threadsafe(run_etf_scan(...)).result()`), two blocking calls can overlap if both scans are scheduled at the same time or run long. APScheduler uses a thread pool; two simultaneous `.result()` calls are fine in isolation but each scan makes N yfinance calls. If both scans run at 9:00 AM, the ETF scan's startup `partition_watchlist` calls and the stock scan's `get_top_sp500_by_fundamentals` call compete for the yfinance scraper at the same time, raising the probability of 429 / rate-limit responses from Yahoo Finance.

**Prevention:**
- Schedule the ETF scan at a different time (e.g., 9:05 AM) with at least a 5-minute offset from the stock scan
- Or share one `run_scan` entry point that internally runs both passes sequentially (stock pass then ETF pass)

---

### Pitfall 3: ETF scan analyst prompt includes stock-specific fields that are None for ETFs

**What goes wrong:** The existing `build_prompt` function in `claude_analyst.py` includes `pe_ratio`, `dividend_yield`, and `earnings_growth` in the prompt text sent to Claude. For ETFs, all three are `None`. The prompt builder may render them as `"None"` literally, or the f-string may crash. Even if it renders correctly, sending Claude "P/E Ratio: None, Earnings Growth: None" produces low-quality analysis because Claude will note these are missing and hedge its recommendation.

**Prevention:**
- Build a separate `build_etf_prompt` that omits stock-specific fields and instead emphasizes momentum, volume, and trend context
- Test `build_etf_prompt` with `pytest` before wiring to `run_etf_scan`

---

### Pitfall 4: _full_patch context manager in test_run_scan.py will not cover run_etf_scan

**What goes wrong:** The `_full_patch` helper in `test_run_scan.py` is a carefully constructed context manager that patches all external dependencies for `run_scan`. A new `run_etf_scan` function will have its own set of call sites — including the new `partition_watchlist` and ETF-aware analyst path. If tests for `run_etf_scan` are written by copying `_full_patch` and modifying it, the two versions will drift. When a shared dependency (like `queries.create_recommendation`) changes signature, both `_full_patch` implementations must be updated.

**Prevention:**
- Extract shared patch parameters into a base helper and compose `_full_patch_stock` and `_full_patch_etf` from it
- Or use parametrize to run the same test against both scan functions where the behavior is expected to be identical (e.g., `recommendations_posted == 0` → ops alert)

---

## Minor Pitfalls

### Pitfall 1: KNOWN_ETF_SYMBOLS allowlist in fundamentals.py is not shared with partition_watchlist

**What goes wrong:** `screener/fundamentals.py` has `_ETF_ALLOWLIST = {"SPY", "QQQ", "VTI", ...}` for the existing ETF bypass in `passes_fundamental_filter`. `partition_watchlist` will need its own ETF detection list. If two separate lists are created, they will drift (one updated, one not). A ticker may be classified as "stock" by `partition_watchlist` but then pass the fundamental filter ETF bypass, resulting in an ETF going through the stock scan path with a "free pass" — not breaking anything, but producing misleading stock-style recommendations for ETFs.

**Prevention:** Move `_ETF_ALLOWLIST` to a shared location (e.g., `screener/constants.py`) and import it in both `fundamentals.py` and the new `universe.py` partition function.

---

### Pitfall 2: asyncio.to_thread raises TypeError if the wrapped function raises a non-Exception BaseException

**What goes wrong:** `asyncio.to_thread` propagates exceptions from the worker thread back to the awaiting coroutine. If the wrapped function raises `SystemExit` or `KeyboardInterrupt` (subclasses of `BaseException`, not `Exception`), the `except Exception` in `run_scan` does not catch it, and the bot process exits. This is unlikely in normal operation but can occur if a test incorrectly raises `SystemExit` inside a mocked sync function.

**Prevention:** This is expected behavior — `SystemExit` should propagate. But tests that use `side_effect=SystemExit` should be aware the test process may exit rather than fail gracefully.

---

### Pitfall 3: ETF scan ops alert uses same "0 recommendations" threshold as stock scan

**What goes wrong:** If the ETF universe is small (2–3 tickers: SPY, QQQ, VTI) and all have been recommended today, the ETF scan legitimately posts 0 recommendations. The existing `send_ops_alert` fires when `recommendations_posted == 0`. This will trigger a false alarm on any day after the initial ETF recommendations are posted (since `ticker_recommended_today` suppresses duplicates for 24h).

**Prevention:** Either use a separate ops alert threshold for ETF scan (e.g., only alert if the ETF universe is non-empty AND 0 recs were posted AND none were suppressed by duplicate check), or simply suppress the ETF scan ops alert entirely and rely on the stock scan alert.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|----------------|------------|
| Phase 7: partition_watchlist | N blocking yfinance calls on event loop | Wrap in `asyncio.to_thread` in `run_scan` immediately when introduced |
| Phase 7: /scan_etf command | Overwrites `_scan_callback`, breaking /scan | Add separate command handler; never reuse `_scan_callback` for ETF |
| Phase 7: ETF recommendation write | `earnings_growth=None` crashes embed formatting | Audit `build_recommendation_embed` for None guards before Phase 7 ships |
| Phase 7: ETF analyst prompt | Stock-specific None fields sent to Claude | Write `build_etf_prompt` as a distinct function from `build_prompt` |
| Phase 7: schema | ETF vs stock ambiguity in recommendations table | Add `asset_type` column migration in Phase 7, not deferred |
| Phase 8: to_thread audit | Double-wrapping `get_top_sp500_by_fundamentals` | Mark existing wrap with comment; Phase 8 audit must start from existing wrap list |
| Phase 8: test mocks | Patching source module instead of `main.<fn>` | Always patch at `main.<fn>` for all functions called via `asyncio.to_thread` in `main.py` |
| Both phases | _full_patch drift between stock and ETF test helpers | Extract shared base helper; compose per-scan patches from it |

---

## Sources

- Direct inspection of `main.py`, `discord_bot/bot.py`, `screener/universe.py`, `screener/fundamentals.py`, `screener/technicals.py`, `analyst/claude_analyst.py`, `database/models.py` — HIGH confidence
- Direct inspection of `tests/test_run_scan.py`, `tests/test_discord_buttons.py`, `tests/test_screener_fetchers.py` — HIGH confidence for mock pattern pitfalls
- `.planning/codebase/CONCERNS.md` (blocking scheduler concern, yfinance reliability) — HIGH confidence, already validated against code
- `.planning/PROJECT.md` (schema constraints, async backlog, ETF allowlist existence) — HIGH confidence
- asyncio.to_thread behavior under patch: based on Python standard library behavior (function reference binding at import time) — HIGH confidence
