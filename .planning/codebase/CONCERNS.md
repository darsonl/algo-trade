# Codebase Concerns

**Analysis Date:** 2026-03-30

---

## Security Concerns

**OAuth token stored as plain JSON file on disk:**
- Issue: `schwab_token.json` lives at the project root as a plain, unencrypted JSON file. Anyone with filesystem access gets full brokerage API credentials. It is in `.gitignore` but remains a risk on shared or compromised machines.
- Files: `schwab_client/auth.py` line 7
- Current mitigation: `.gitignore` excludes it from version control
- Recommendations: Store the token in the OS keychain (`keyring` library) or an encrypted secrets store. At minimum, restrict file permissions (chmod 600) programmatically after writing.

**No `.env.example` validation of dangerous defaults:**
- Issue: `DRY_RUN` and `PAPER_TRADING` default to `true` in code, but nothing prevents a user from unknowingly deploying with both set to `false` after copy-pasting an `.env.example`. `config.validate()` does not check these flags.
- Files: `config.py` lines 15–16, `config.py` lines 35–48
- Recommendations: Add a startup warning log (or even a prompt) when `DRY_RUN=false` combined with `PAPER_TRADING=false`, since that combination places real orders with real money.

**Anthropic API key stored in module-level singleton at import time:**
- Issue: `config = Config()` is instantiated at module import (`config.py` line 51), which means `ANTHROPIC_API_KEY` is read and held in memory for the entire process lifetime as a plain string attribute.
- Files: `config.py` line 51
- Impact: Memory dump or debug introspection exposes the key.
- Recommendations: Acceptable for most threat models but worth documenting.

---

## Reliability Risks

**No retry logic anywhere in the pipeline:**
- Issue: All network calls — yfinance, Wikipedia, Claude API, Schwab API, Discord — are single-shot with no retry on transient failure. A single timeout silently drops a ticker from the scan.
- Files: `screener/fundamentals.py` line 31, `screener/technicals.py` line 65, `analyst/claude_analyst.py` line 81, `analyst/news.py` line 16, `schwab_client/orders.py` line 46
- Impact: Rate limits, momentary connectivity issues, or API blips silently cause missed recommendations.
- Fix approach: Wrap external calls with `tenacity` (exponential backoff + jitter). At minimum, add per-call timeout arguments to yfinance and the Anthropic client.

**yfinance reliability is fragile by design:**
- Issue: `yfinance` is an unofficial scraper, not an official API. Structural changes to Yahoo Finance HTML can break `fetch_fundamental_info` or `fetch_news_headlines` silently — returning empty dicts or empty lists — causing tickers to be silently skipped.
- Files: `screener/fundamentals.py` line 31, `analyst/news.py` line 16, `screener/technicals.py` line 65
- Impact: Entire universe scan could produce zero recommendations without any error surfacing to operators.
- Fix approach: Log a warning when `yf.Ticker(ticker).info` returns fewer than expected keys (e.g., missing `trailingPE`, `dividendYield`, and `earningsGrowth` simultaneously). Consider an official data source (Polygon.io, Tiingo) for production.

**S&P 500 fetch from Wikipedia has no fallback or cache:**
- Issue: `get_sp500_tickers()` scrapes a Wikipedia HTML table via `pd.read_html`. The page structure, URL, or table index can change. If the fetch fails, `run_scan` falls back to the watchlist only (caught exception in `main.py` lines 54–57), but there is no cache of the last known good S&P list.
- Files: `screener/universe.py` lines 26–32, `main.py` lines 54–57
- Fix approach: Cache the result to a local file (e.g., `sp500_cache.json`) with a TTL. Use the cached version on network failure.

**Scheduler uses `asyncio.run_coroutine_threadsafe(...).result()` blocking call:**
- Issue: `main.py` line 126–128 blocks the APScheduler thread with `.result()` for the entire duration of `run_scan`. A full S&P 500 + watchlist scan (500+ tickers, each making multiple network calls) could hold this thread for 30–60+ minutes, blocking any subsequent scheduled fires or graceful shutdown.
- Files: `main.py` lines 126–128
- Impact: If a scan overruns its schedule interval (e.g., a daily scan takes longer than 24h), APScheduler may queue jobs or skip them depending on its misfire policy. The default APScheduler misfire grace time is only 1 second.
- Fix approach: Move `run_scan` to a dedicated executor or use `asyncio.ensure_future` / non-blocking dispatch. Set an explicit `misfire_grace_time` on the scheduled job.

**Discord channel fetch can silently return None:**
- Issue: `bot.send_recommendation()` calls `self.get_channel(...)` which returns `None` if the channel is not cached (bot hasn't seen it yet, or no message history). This raises `RuntimeError` which is caught by the broad `except Exception` in `run_scan`, silently aborting the recommendation post.
- Files: `discord_bot/bot.py` line 99, `main.py` line 100
- Fix approach: Use `await bot.fetch_channel(...)` (guaranteed network fetch) instead of `get_channel` (cache only). Add a startup channel validation in `on_ready`.

**`place_order` returns `None` silently on failure, and the trade record is still written:**
- Issue: If `place_order` throws and returns `None`, `create_trade` is still called with `order_id=None`. The DB record shows an "approved" trade with a null `order_id`, making it indistinguishable from a successfully placed order to any future query.
- Files: `discord_bot/bot.py` lines 40–53, `schwab_client/orders.py` lines 50–52
- Fix approach: Distinguish "dry run with no order" (intentional `None`) from "order failed" (exceptional `None`). Return a sentinel or raise on failure.

---

## Technical Debt

**New `anthropic.Anthropic` client created per ticker per scan:**
- Issue: `analyze_ticker` instantiates a new `anthropic.Anthropic(api_key=...)` on every call when `client=None` (the default in `run_scan`). For a 500-ticker universe, this is 500 object instantiations.
- Files: `analyst/claude_analyst.py` lines 77–78, `main.py` line 72
- Fix approach: Instantiate the client once in `run_scan` and pass it as the `client` argument.

**New `yf.Ticker` object created for every call, no caching:**
- Issue: `fetch_fundamental_info` and `fetch_technical_data` each construct a new `yf.Ticker(ticker)` object and make separate HTTP calls for the same ticker within the same scan iteration. Fundamental info and technical data could overlap in what they fetch.
- Files: `screener/fundamentals.py` line 31, `screener/technicals.py` lines 64–65
- Fix approach: Merge the two fetch functions or pass the `yf.Ticker` object as an argument to avoid double-construction.

**Hardcoded volume filter threshold (0.5x avg_volume):**
- Issue: The volume filter uses `volume < avg_volume * 0.5` with the multiplier baked directly into source code rather than driven by config.
- Files: `screener/technicals.py` line 54
- Fix approach: Add `min_volume_ratio: float = 0.5` to `Config` and reference it from the filter.

**Hardcoded minimum data point count (51) duplicated between logic and docs:**
- Issue: The value `51` is used in `fetch_technical_data` (line 67) to guard against insufficient history, but `compute_rsi` (line 10) only checks `period + 1 = 15`. The `51` is explained only in CLAUDE.md. If the MA window changes, this silent constant must be hunted down.
- Files: `screener/technicals.py` lines 67, 76–78
- Fix approach: Derive the minimum from the actual constants (`MA_WINDOW + 1 = 51`) or expose as a named constant at module level.

**`config.py` instantiates a global `config` object at module import time:**
- Issue: `config = Config()` at module level means any test that imports from `config` will trigger `os.getenv` reads and `load_dotenv`. This forces tests to either have a `.env` file present or carefully manage environment variables.
- Files: `config.py` line 51
- Impact: Tests that mutate `Config()` fields (e.g., `test_main.py` lines 9–13) work around this by instantiating fresh `Config()` objects, but any test that imports `from config import config` directly gets the global singleton.
- Fix approach: Remove the global singleton from `config.py`; let `main.py` own the single instance.

**`earnings_growth` field name mismatch risk:**
- Issue: `passes_fundamental_filter` reads `info.get("earningsGrowth")` from yfinance. However, yfinance field names change across versions (they shifted from snake_case to camelCase in 0.2.x). If the key is absent, the filter silently rejects all tickers (returns `False`).
- Files: `screener/fundamentals.py` lines 13, 15

---

## Missing Production Features

**No alerting on scan failure:**
- Issue: If `run_scan` catches an exception for every ticker and logs errors, the scan completes "successfully" with zero recommendations and no Discord notification to operators.
- Files: `main.py` lines 100–102
- Fix approach: Post a Discord message (to the same or a dedicated ops channel) if zero recommendations were produced or if the error rate exceeded a threshold.

**No position sizing against existing holdings:**
- Issue: `compute_share_quantity` uses only `MAX_POSITION_SIZE_USD` and current price. It does not check if a position already exists or what the current portfolio concentration is. A ticker can be approved multiple times across different scans if rejection/expiry logic does not prevent it cleanly.
- Files: `discord_bot/bot.py` lines 15–19, `schwab_client/orders.py` lines 55–66
- Fix approach: Call `get_positions` before approving to check existing exposure.

**No sell / exit logic:**
- Issue: The bot only places market buys. There is no stop-loss, take-profit, or scheduled exit logic. Positions accumulate indefinitely.
- Files: `schwab_client/orders.py` — no sell order builder exists
- Fix approach: Add `build_market_sell` and a separate exit strategy module.

**No structured logging or log shipping:**
- Issue: `logging.basicConfig` writes to stdout only. There is no file handler, log rotation, or external sink (e.g., Datadog, CloudWatch, Papertrail). Logs are lost on process restart.
- Files: `main.py` lines 112–115
- Fix approach: Add a `RotatingFileHandler` and consider structured JSON logging for parsing.

**No health check endpoint:**
- Issue: There is no way to externally verify the bot is alive between scans (e.g., a `/health` HTTP endpoint or a heartbeat message). Silent crashes go undetected until the next scheduled scan does not fire.
- Fix approach: Add a heartbeat ping to Discord or a simple HTTP server (e.g., `aiohttp`) on a configurable port.

**No rate limit handling for Claude API:**
- Issue: Scanning all 500+ S&P 500 tickers will produce one Claude API call per ticker that passes the fundamental filter. With no rate-limiting or concurrency controls, this can exhaust Anthropic API rate limits rapidly and trigger 429 errors that are silently swallowed by the `except Exception` in `run_scan`.
- Files: `main.py` line 72, `analyst/claude_analyst.py` line 81
- Fix approach: Add inter-call sleep/throttle or batch processing with token budget tracking.

**No support for multiple Discord channels or multi-user approval:**
- Issue: All recommendations go to a single `DISCORD_CHANNEL_ID`. Any user can approve or reject any recommendation. There is no permission check on who clicked Approve.
- Files: `discord_bot/bot.py` lines 31–65
- Fix approach: Add an allowed-approver role check in the `approve` button handler.

---

## Test Coverage Gaps

**`run_scan` has no integration or unit test:**
- Issue: The core orchestration function `run_scan` in `main.py` is completely untested. No test exercises the full pipeline (fundamentals → Claude → technicals → DB write → Discord post), even with mocks.
- Files: `main.py` lines 47–104, `tests/test_main.py` (only tests `should_recommend` and `configure_scheduler`)
- Risk: Regressions in ordering, error handling, or the interaction between pipeline stages go undetected.
- Priority: High

**`ApproveRejectView` button handlers have no tests:**
- Issue: `tests/test_discord_bot.py` only tests `compute_share_quantity`. The `approve` and `reject` button callbacks — which write to DB, call the Schwab API, and send Discord messages — have zero test coverage.
- Files: `discord_bot/bot.py` lines 30–65
- Risk: DB status update, dry-run branching, and order placement logic changes go undetected.
- Priority: High

**`analyze_ticker` (the live Claude API call path) has no mock test:**
- Issue: `tests/test_analyst_claude.py` tests `build_prompt` and `parse_claude_response` but does not test `analyze_ticker` end-to-end with a mocked `anthropic.Anthropic` client. The `ValueError` path from `parse_claude_response` is not tested as it propagates through `analyze_ticker`.
- Files: `analyst/claude_analyst.py` lines 63–86
- Priority: Medium

**`fetch_technical_data` and `fetch_fundamental_info` network paths not tested:**
- Issue: No test mocks `yf.Ticker` to exercise the actual fetch functions. Only the pure filter functions (`passes_technical_filter`, `passes_fundamental_filter`) are tested with synthetic data. Network failures, empty responses, and malformed yfinance output in the fetchers are untested.
- Files: `screener/technicals.py` lines 60–88, `screener/fundamentals.py` lines 28–31
- Priority: Medium

**`get_positions` (Schwab account fetch) is untested:**
- Issue: `tests/test_schwab_orders.py` tests `build_market_buy` and `parse_positions` but does not test `get_positions` (which calls `get_client` and `client.get_account`). The `get_client` path itself has only path-shape tests.
- Files: `schwab_client/orders.py` lines 55–66, `tests/test_schwab_auth.py`
- Priority: Low

**`ticker_recommended_today` timezone behavior not tested:**
- Issue: `ticker_recommended_today` uses `date('now')` which is UTC in SQLite. If the host system and the user's intended trading timezone differ, the duplicate-check window may be off by hours. No test exercises this boundary.
- Files: `database/queries.py` lines 83–92
- Priority: Medium

**No test for `expire_stale_recommendations` edge cases:**
- Issue: The expiry query targets `expires_at < datetime('now')`. There is no test for recommendations expiring exactly at `now` (boundary) or for recommendations that are already `approved`/`rejected` (confirmed they are not re-expired). The existing test only covers the basic expiry path.
- Files: `database/queries.py` lines 95–103, `tests/test_database.py` lines 101–121
- Priority: Low

---

## Scalability Concerns

**Single-threaded sequential scan over 500+ tickers:**
- Issue: `run_scan` processes each ticker in a `for` loop with multiple synchronous I/O calls (yfinance × 2, Claude API, optionally Schwab). For a 500-ticker universe with even 1 second per ticker, a scan takes 8+ minutes. With network latency and retries, it can easily exceed 30 minutes.
- Files: `main.py` lines 62–102
- Fix approach: Parallelize with `asyncio.gather` or a `ThreadPoolExecutor` with a configurable concurrency limit.

**SQLite is single-writer:**
- Issue: Multiple concurrent approvals (two users clicking Approve on different recommendations simultaneously) could hit SQLite's write lock. The current `get_connection` does not enable WAL mode.
- Files: `database/models.py` lines 4–7
- Fix approach: Enable WAL mode (`conn.execute("PRAGMA journal_mode=WAL")`) in `get_connection`. For high concurrency, migrate to PostgreSQL.

**No connection pooling — new SQLite connection per query:**
- Issue: Every query function in `database/queries.py` opens and closes its own `sqlite3.Connection`. For a 500-ticker scan with 3–4 DB calls per ticker, this is 1500+ connection open/close cycles per scan.
- Files: `database/queries.py` — every function
- Fix approach: Use a context-managed connection or a lightweight pool (e.g., pass a connection as an argument for batch operations).

---

## Documentation Gaps

**No `.env.example` file present in the repository:**
- Issue: `config.py` references 14+ environment variables. New contributors have no reference file to know which variables are required vs optional, what format values should take, or what safe defaults look like.
- Fix approach: Create `.env.example` with all variable names, placeholder values, and inline comments. Verify it does not contain real secrets.

**No docstrings on `run_scan`, `main`, or `on_ready`:**
- Issue: The three most important orchestration functions in `main.py` have only minimal or no docstrings. The overall startup sequence is documented in CLAUDE.md but not in the code itself.
- Files: `main.py` lines 47, 111, 123

**`schwab_client/auth.py` does not document the OAuth first-run flow:**
- Issue: The function comment mentions "opens a browser" but does not explain what port it listens on, how long it waits, or what the `callback_url` parameter must match. This has caused confusion for first-time setup.
- Files: `schwab_client/auth.py` lines 10–24

**`passes_technical_filter` does not document the `0.5` volume multiplier:**
- Issue: The volume check `volume < avg_volume * 0.5` is not explained anywhere in the function body or docstring. The threshold origin is unclear.
- Files: `screener/technicals.py` line 54

---

## Refactoring Opportunities

**`create_recommendation` missing `earnings_growth` field:**
- Issue: The `recommendations` table schema does not store `earnings_growth`, even though it is a filter criterion passed to Claude. This makes historical audit of why a recommendation was made incomplete.
- Files: `database/models.py` lines 13–25, `database/queries.py` lines 5–23
- Fix approach: Add `earnings_growth REAL` column to schema and populate from `info.get("earningsGrowth")` in `run_scan`.

**`on_manual_scan` event is a non-standard Discord.py pattern:**
- Issue: `main.py` uses `bot.dispatch("manual_scan")` and registers an `on_manual_scan` handler, which is not a standard Discord.py lifecycle event. This custom event dispatch is undocumented and can be confusing to contributors unfamiliar with the pattern.
- Files: `main.py` lines 136–137, `discord_bot/bot.py` line 87

**Module-level `import yfinance as yf` deferred inside functions:**
- Issue: `yfinance`, `anthropic`, and `schwab` are imported inside function bodies (`fetch_fundamental_info`, `fetch_technical_data`, `analyze_ticker`, `place_order`, etc.) rather than at module top level. While this avoids import-time side effects in tests, it obscures the actual dependency graph and makes IDE analysis less useful.
- Files: `screener/fundamentals.py` line 30, `screener/technicals.py` line 62, `analyst/claude_analyst.py` line 77, `schwab_client/orders.py` lines 9, 41, 61
- Fix approach: Use top-level imports and use `unittest.mock.patch` in tests to control them.

**`parse_positions` will raise `KeyError` on malformed position data:**
- Issue: `parse_positions` accesses `pos["instrument"]["symbol"]`, `pos["longQuantity"]`, etc. with no `.get()` fallback. Any position missing these keys (e.g., options, bonds, or a schema change from Schwab) will raise `KeyError` and crash `get_positions`.
- Files: `schwab_client/orders.py` lines 24–31
- Fix approach: Use `.get()` with defaults or add a `try/except` per position with a `logger.warning`.

---

*Concerns audit: 2026-03-30*
