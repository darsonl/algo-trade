# Phase 8: Asyncio Event Loop Fix - Research

**Researched:** 2026-04-08
**Domain:** asyncio.to_thread, discord.py event loop, yfinance blocking I/O
**Confidence:** HIGH

## Summary

Phase 8 is a targeted mechanical refactor: wrap every remaining synchronous yfinance call in `run_scan` and `run_scan_etf` with `asyncio.to_thread`. No new libraries, no architectural changes. The pattern is already established in the codebase — `analyze_ticker`, `analyze_sell_ticker`, `analyze_etf_ticker`, `get_top_sp500_by_fundamentals`, and `partition_watchlist` (in `run_scan_etf`) are all correctly wrapped. The remaining unwrapped calls are `fetch_fundamental_info`, `fetch_news_headlines`, and `fetch_technical_data` — each called 2-3 times across the buy, sell, and ETF scan loops.

The test harness is already shaped to support this change without test rewrites. Existing tests in `test_run_scan.py` and `test_sell_scan.py` patch functions at the `main.*` call site (e.g. `patch("main.fetch_fundamental_info", ...)`). Because `asyncio.to_thread` calls the function object at runtime, patching the name in `main`'s namespace intercepts the call whether or not `to_thread` is in the call path. This was verified experimentally: patched functions return mock values correctly when called through `asyncio.to_thread`.

The primary risk is not the wrapping itself — it's auditing call sites exhaustively. The sell pass in `run_scan` contains a `sell_blocked` RSI-reset branch (lines 183-192) that is a separate, independent call to `fetch_technical_data` outside the main sell loop, and can be overlooked in a sweep.

**Primary recommendation:** Wrap each unwrapped call with `await asyncio.to_thread(fn, *args)` at its call site in `main.py`. No changes to the wrapped functions themselves. No test rewrites needed.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ASYNC-01 | All blocking yfinance calls in `run_scan` buy pass (`fetch_fundamental_info`, `fetch_news_headlines`, `fetch_technical_data`) wrapped in `asyncio.to_thread` | Pattern already established; call sites at main.py lines 89, 93, 134 |
| ASYNC-02 | All blocking yfinance calls in `run_scan` sell pass (`fetch_news_headlines`, `fetch_technical_data`) wrapped in `asyncio.to_thread` | Two separate call locations: main sell loop (lines 195, 212) and sell_blocked reset branch (line 185) |
| ASYNC-03 | `partition_watchlist` wrapped in `asyncio.to_thread` at its Phase 7 call site — not deferred to Phase 8 | Already wrapped at both call sites (main.py lines 70 and 289); ASYNC-03 is pre-satisfied |
| ASYNC-04 | `get_top_sp500_by_fundamentals` hotfix formalized with `# already wrapped` comments; Phase 8 audit documents all already-wrapped sites before adding new wraps | Already wrapped at main.py line 62; needs comment and audit documentation |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Language:** Python (pip, pytest)
- **Test runner:** `pytest` / `pytest -v` / `pytest tests/test_file.py::test_name -v`
- **Async test framework:** `pytest-asyncio` (strict mode) — all async tests need `@pytest.mark.asyncio`
- **Code conventions:** snake_case, dataclasses, pure functions, type hints
- **No parallel scanning:** `asyncio.gather` for concurrent ticker scanning is explicitly out of scope per `Out of Scope` section in REQUIREMENTS.md
- **Safety-critical default:** `DRY_RUN=true`, `PAPER_TRADING=true` — no orders in tests

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `asyncio.to_thread` | stdlib (Python 3.9+) | Offload blocking sync calls to thread pool | Built-in, no dependency; designed exactly for this use case |
| `discord.py` | 2.4.0 [VERIFIED: project] | Async Discord client — event loop owner | Bot's event loop must stay unblocked for gateway heartbeats |
| `yfinance` | 1.2.0 [VERIFIED: project] | Market data — all calls are blocking HTTP | No async API; `to_thread` is the only correct wrapper |
| `pytest-asyncio` | 1.3.0 [VERIFIED: project] | Run `async def` tests | Strict mode confirmed (`asyncio_mode=Mode.STRICT`) |

### No New Dependencies
This phase requires no new packages. All libraries are already installed. [VERIFIED: codebase grep]

**Installation:** None required.

## Architecture Patterns

### Recommended Project Structure
No structural changes. All edits are in `main.py` only — wrapping call sites, not changing modules.

### Pattern 1: asyncio.to_thread at the Call Site (Established Pattern)
**What:** Wrap a synchronous function call with `await asyncio.to_thread(fn, arg1, arg2)`. The function itself is unchanged; only the call site becomes async.
**When to use:** Any synchronous function that does blocking I/O (network, disk) called from within an `async def` function that runs on the event loop.
**Example:**
```python
# Source: main.py (existing — analyze_ticker pattern)
# BEFORE (blocks event loop):
result = fetch_fundamental_info(yf_ticker)

# AFTER (offloaded to thread pool):
result = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
```

The function signature is unchanged. The caller receives the return value as before. Exception propagation is identical — exceptions raised inside the thread are re-raised in the awaiting coroutine.

### Pattern 2: Wrapping Functions with Extra Keyword Arguments
**What:** `asyncio.to_thread` passes `*args` and `**kwargs` directly to the function.
**When to use:** Functions like `fetch_news_headlines` that take keyword arguments.
**Example:**
```python
# Source: main.py (existing — fetch_news_headlines call)
# BEFORE:
headlines = fetch_news_headlines(ticker, alpha_vantage_api_key=config.alpha_vantage_api_key)

# AFTER:
headlines = await asyncio.to_thread(
    fetch_news_headlines, ticker, alpha_vantage_api_key=config.alpha_vantage_api_key
)
```

### Pattern 3: Audit Comment for Already-Wrapped Sites (ASYNC-04)
**What:** Add an inline comment to pre-existing `asyncio.to_thread` wraps to document that the site was audited in Phase 8.
**When to use:** `get_top_sp500_by_fundamentals` at main.py line 62, `partition_watchlist` at main.py lines 70 and 289.
**Example:**
```python
# Source: main.py (hotfix ae66e64 — formalized in Phase 8)
sp500 = await asyncio.to_thread(get_top_sp500_by_fundamentals, config)  # P8: already wrapped
```

### Complete Call Site Inventory

All blocking yfinance calls in `main.py` with their current wrap status:

| Location | Function | Currently Wrapped | Action Required |
|----------|----------|-------------------|-----------------|
| `run_scan` buy pass, line 89 | `fetch_fundamental_info(yf_ticker)` | No | Wrap |
| `run_scan` buy pass, line 93 | `fetch_news_headlines(ticker, ...)` | No | Wrap |
| `run_scan` buy pass, line 134 | `fetch_technical_data(yf_ticker)` | No | Wrap |
| `run_scan` sell pass sell_blocked branch, line 185 | `fetch_technical_data(yf_ticker)` | No | Wrap |
| `run_scan` sell pass main loop, line 195 | `fetch_technical_data(yf_ticker)` | No | Wrap |
| `run_scan` sell pass main loop, line 212 | `fetch_news_headlines(ticker, ...)` | No | Wrap |
| `run_scan_etf` ETF pass, line 307 | `fetch_technical_data(yf_ticker)` | No | Wrap |
| `run_scan_etf` ETF pass, line 310 | `fetch_fundamental_info(yf_ticker)` | No | Wrap |
| `run_scan_etf` ETF pass, line 316 | `fetch_news_headlines(ticker, ...)` | No | Wrap |
| `run_scan` line 62 | `get_top_sp500_by_fundamentals(config)` | YES (hotfix ae66e64) | Add audit comment |
| `run_scan` line 70 | `partition_watchlist(universe)` | YES (Phase 7) | Add audit comment |
| `run_scan_etf` line 289 | `partition_watchlist(etf_tickers)` | YES (Phase 7) | Add audit comment |

**Total unwrapped calls requiring wrapping: 9** [VERIFIED: direct code inspection]

### Anti-Patterns to Avoid
- **Wrapping the function definition** (adding `async def` to `fetch_technical_data` etc.): Wrong approach. The functions are correctly synchronous. Only the call site changes.
- **Using `loop.run_in_executor` directly**: `asyncio.to_thread` is the modern (Python 3.9+) replacement. [VERIFIED: Python 3.14.3 installed, `to_thread` confirmed present]
- **Using `asyncio.gather` to parallelize ticker scanning**: Explicitly out of scope per REQUIREMENTS.md.
- **Creating a new ThreadPoolExecutor**: `asyncio.to_thread` uses the default thread pool (`ThreadPoolExecutor` with `min(32, os.cpu_count() + 4)` workers). No manual executor management needed.
- **Wrapping `passes_fundamental_filter` or `passes_technical_filter`**: These are pure in-memory computations — no I/O, no blocking. They must NOT be wrapped.
- **Wrapping `queries.*` functions**: SQLite with WAL mode is fast (microseconds); these are not the source of heartbeat delays. Do not wrap.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Thread pool management | Custom `ThreadPoolExecutor` context manager | `asyncio.to_thread` (uses default pool) | Default pool is correctly sized; manual executor adds lifecycle complexity |
| Async yfinance wrapper | Custom async class wrapping yf.Ticker | `asyncio.to_thread` at call site | yfinance is not thread-safe per ticker instance — see Pitfall 2 |
| Retry logic in async wrapper | Async retry decorator around `to_thread` | Existing `@_retry` decorators on the wrapped functions | Retry logic already exists inside `fetch_fundamental_info`, `fetch_technical_data`, `_fetch_from_alpha_vantage` via tenacity |

**Key insight:** `asyncio.to_thread` is a one-liner at each call site. The functions are already correct; only the invocation style changes.

## Common Pitfalls

### Pitfall 1: Missing the sell_blocked Branch
**What goes wrong:** The `sell_blocked` RSI-reset branch (lines 183-192 in `run_scan`) calls `fetch_technical_data` independently before the main sell evaluation loop. A sweep that only looks at the primary sell evaluation block misses this call.
**Why it happens:** The branch is inside an `if pos["sell_blocked"]: ... continue` guard, making it visually separate from the main sell loop. It's easily overlooked as a "skip path" that has no side effects worth caring about — but it still calls `fetch_technical_data` synchronously.
**How to avoid:** Search for every occurrence of each function name in `main.py`, not just the "happy path" loops. Use `grep -n "fetch_technical_data\|fetch_fundamental_info\|fetch_news_headlines" main.py`.
**Warning signs:** RSI-reset logic working but heartbeat still blocked during sell_blocked positions.

### Pitfall 2: yfinance Thread Safety — yf.Ticker Instance Sharing
**What goes wrong:** `yf.Ticker` objects cache internal state. If the same instance were shared across concurrent calls (e.g. via `asyncio.gather`), concurrent `.history()` or `.info` calls could produce corrupt or incomplete data.
**Why it happens:** yfinance is not documented as thread-safe for concurrent access to the same `Ticker` instance. [ASSUMED — yfinance has no official thread-safety statement]
**How to avoid:** In the current sequential scan design (one ticker at a time), this is not an issue. Each ticker creates its own `yf.Ticker(ticker)` object (lines 88, 184, 194 in `main.py`). This is already correct. Do NOT introduce `asyncio.gather` over ticker loops.
**Warning signs:** Only becomes relevant if ticker parallelism is added in a future phase.

### Pitfall 3: Test Mocks Still Work — No Rewrites Needed
**What goes wrong:** Developer assumes wrapping in `asyncio.to_thread` will break existing `patch("main.fetch_fundamental_info", ...)` mocks and rewrites tests unnecessarily.
**Why it happens:** Incorrect mental model of how `to_thread` interacts with `unittest.mock.patch`.
**How to avoid:** `patch("main.fetch_fundamental_info", return_value=...)` replaces the name `fetch_fundamental_info` in `main`'s namespace. When `asyncio.to_thread(fetch_fundamental_info, yf_ticker)` runs, it calls the patched object — the mock. This was verified experimentally in this session: mock returns correct value through `to_thread` call. Existing test assertions remain valid.
**Warning signs:** None — tests will continue to pass without modification.

### Pitfall 4: asyncio.to_thread Requires a Running Event Loop
**What goes wrong:** Calling `await asyncio.to_thread(...)` outside an async context raises `RuntimeError: no running event loop`.
**Why it happens:** `to_thread` is a coroutine; it must be awaited from within an `async def` function that is executing on an event loop.
**How to avoid:** All call sites in `run_scan` and `run_scan_etf` are already inside `async def` functions running on discord.py's event loop. This is a non-issue for the current phase. Only relevant if someone attempts to call these from a sync context (scheduler thread). The scheduler already handles this correctly via `asyncio.run_coroutine_threadsafe` in `on_ready`.
**Warning signs:** Errors during APScheduler-triggered scans (the scheduler runs in a background thread, but correctly submits to the event loop).

### Pitfall 5: fetch_technical_data Called Twice Per Ticker in Sell Pass
**What goes wrong:** In the sell pass, `fetch_technical_data` is called once for the sell_blocked RSI check (line 185) and again for the main sell evaluation (line 195) for non-blocked positions. These are separate `yf.Ticker` instances. Wrapping only one is insufficient.
**Why it happens:** Copy-paste pattern — the sell_blocked branch and the main sell loop look structurally similar but are guarded by the `continue` statement, meaning a ticker hits at most one branch.
**How to avoid:** Wrap both independently. They are in different branches so there is no duplication at runtime for any given ticker.

## Code Examples

Verified patterns from official sources and codebase:

### Complete Buy Pass Wrapping Pattern
```python
# Source: main.py — buy pass loop (current state + Phase 8 changes)
yf_ticker = yf.Ticker(ticker)
info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)     # ASYNC-01
if not passes_fundamental_filter(info, config):
    continue

headlines = await asyncio.to_thread(                                   # ASYNC-01
    fetch_news_headlines, ticker,
    alpha_vantage_api_key=config.alpha_vantage_api_key
)
# ... cache check, analyst call (already wrapped) ...

tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)  # ASYNC-01
```

### Sell Pass sell_blocked Branch Wrapping
```python
# Source: main.py — sell_blocked RSI reset branch (ASYNC-02)
yf_ticker = yf.Ticker(ticker)
tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)  # ASYNC-02
if tech_data.get("rsi") is not None and tech_data["rsi"] <= config.sell_rsi_threshold:
    queries.reset_sell_blocked(config.db_path, ticker)
```

### Sell Pass Main Loop Wrapping
```python
# Source: main.py — main sell evaluation (ASYNC-02)
yf_ticker = yf.Ticker(ticker)
tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)  # ASYNC-02
if not check_exit_signals(tech_data, config):
    continue
# ...
headlines = await asyncio.to_thread(                                   # ASYNC-02
    fetch_news_headlines, ticker,
    alpha_vantage_api_key=config.alpha_vantage_api_key
)
```

### ETF Pass Wrapping
```python
# Source: main.py — ETF scan loop
yf_ticker = yf.Ticker(ticker)
tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
headlines = await asyncio.to_thread(
    fetch_news_headlines, ticker,
    alpha_vantage_api_key=config.alpha_vantage_api_key
)
```

### Audit Comment Pattern (ASYNC-04)
```python
# Source: main.py line 62 — hotfix ae66e64, formalized Phase 8
sp500 = await asyncio.to_thread(get_top_sp500_by_fundamentals, config)  # P8-audit: already wrapped (hotfix ae66e64)
stocks_only, _etfs = await asyncio.to_thread(partition_watchlist, universe)  # P8-audit: already wrapped (Phase 7)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `loop.run_in_executor(None, fn, *args)` | `asyncio.to_thread(fn, *args)` | Python 3.9 | Cleaner API; `to_thread` propagates context vars automatically |
| Manual thread management for blocking calls | `asyncio.to_thread` from stdlib | Python 3.9 | No third-party dependency needed |

**Deprecated/outdated:**
- `loop.run_in_executor(None, fn)`: Still works in Python 3.14, but `asyncio.to_thread` is the idiomatic replacement. The project already uses `to_thread` exclusively — no `run_in_executor` calls exist. [VERIFIED: codebase grep]

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | yfinance `yf.Ticker` instances are not safe for concurrent access from multiple threads | Pitfall 2 | Low — concurrent access is not part of this phase; sequential design is unchanged |
| A2 | `run_scan_etf` blocking calls (lines 307, 310, 316) are the Phase 7 additions not yet wrapped | Call Site Inventory | Low — verified by direct code inspection of main.py |

## Open Questions

1. **ETF pass `fetch_fundamental_info` call (line 310)**
   - What we know: called to get `netExpenseRatio` for ETFs; currently unwrapped
   - What's unclear: Phase 7 plans may have intentionally deferred this to Phase 8 (consistent with ASYNC-01/02 scope)
   - Recommendation: Wrap it. The ETF pass runs on the same event loop and the same heartbeat risk applies.

2. **`partition_watchlist` in `run_scan` buy pass (line 70)**
   - What we know: ASYNC-03 in REQUIREMENTS.md says this should be wrapped at its Phase 7 call site, not deferred to Phase 8. It IS already wrapped (verified: main.py line 70).
   - What's unclear: Whether ASYNC-03 is fully satisfied by Phase 7 and needs no action in Phase 8 beyond an audit comment.
   - Recommendation: Mark as pre-satisfied in Phase 8 audit; add audit comment per ASYNC-04 pattern.

## Environment Availability

Step 2.6: SKIPPED — this phase is purely call-site edits in `main.py` (adding `await asyncio.to_thread(...)` wrappers). No new external dependencies, tools, or services required. Python 3.14.3 with asyncio.to_thread already available [VERIFIED: runtime check].

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | none — asyncio mode detected via plugin default |
| Asyncio mode | STRICT (requires explicit `@pytest.mark.asyncio` on all async tests) |
| Quick run command | `pytest tests/test_run_scan.py tests/test_sell_scan.py -q` |
| Full suite command | `pytest -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ASYNC-01 | `fetch_fundamental_info`, `fetch_news_headlines`, `fetch_technical_data` called via `to_thread` in buy pass | integration | `pytest tests/test_run_scan.py -q` | Yes — existing tests already cover behavior; new tests needed for call-site verification |
| ASYNC-02 | Same functions called via `to_thread` in sell pass (both branches) | integration | `pytest tests/test_sell_scan.py -q` | Yes — existing tests cover behavior |
| ASYNC-03 | `partition_watchlist` wrapped at Phase 7 call site | integration | `pytest tests/test_run_scan.py::test_run_scan_excludes_etfs_from_stock_universe -q` | Yes |
| ASYNC-04 | Audit comments present on pre-wrapped call sites | code review | manual | N/A |

### Key Testing Insight — No Mock Rewrites Needed
Existing tests in `test_run_scan.py` and `test_sell_scan.py` patch at `main.*` namespace. When the code changes from:
```python
result = fetch_fundamental_info(yf_ticker)
```
to:
```python
result = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
```
`patch("main.fetch_fundamental_info", return_value=...)` continues to intercept the call correctly because `asyncio.to_thread` receives the already-patched function object. [VERIFIED: experimental confirmation in this session]

### Wave 0 Gaps
New test file recommended to verify `to_thread` wrapping explicitly:
- [ ] `tests/test_async_wrapping.py` — verifies that unwrapped functions raise `BlockingCallDetected` or (simpler) verifies call counts through `asyncio.to_thread` spy — covers ASYNC-01, ASYNC-02

**Note on testing approach:** The simplest verification is a spy on `asyncio.to_thread` itself. However, since `asyncio.to_thread` is in stdlib and the functional behavior is unchanged (mocked functions return same values), the practical validation is:
1. Existing tests pass after the change (no regressions)
2. Manual smoke test: run live bot, observe no heartbeat timeout in logs during scan

The planner may decide Wave 0 test file is optional for this phase given the mechanical nature of the change and 100% existing test coverage of the affected functions.

### Sampling Rate
- **Per task commit:** `pytest tests/test_run_scan.py tests/test_sell_scan.py -q`
- **Per wave merge:** `pytest -q` (full suite, currently 252 tests)
- **Phase gate:** Full suite green (252+) before `/gsd-verify-work`

## Security Domain

This phase modifies only the execution threading model of existing I/O calls. No new attack surfaces, no new data flows, no authentication changes. Security domain is not applicable to this phase. [VERIFIED: scope limited to `asyncio.to_thread` wrapping of existing functions]

## Sources

### Primary (HIGH confidence)
- [VERIFIED: direct code inspection] `main.py` — all call sites inventoried line-by-line
- [VERIFIED: runtime check] Python 3.14.3, `asyncio.to_thread` present and documented
- [VERIFIED: runtime check] pytest-asyncio 1.3.0, strict mode, `@pytest.mark.asyncio` required
- [VERIFIED: experimental] `asyncio.to_thread` + `patch("main.fn")` interaction confirmed

### Secondary (MEDIUM confidence)
- Python docs: `asyncio.to_thread` added in Python 3.9, uses `contextvars.copy_context()` [CITED: Python stdlib asyncio source, `help(asyncio.to_thread)` output]

### Tertiary (LOW confidence)
- yfinance thread safety: no official documentation; A1 in Assumptions Log [ASSUMED]

## Metadata

**Confidence breakdown:**
- Call site inventory: HIGH — read main.py directly, line-by-line
- Pattern correctness: HIGH — existing wrapped calls in same codebase work correctly
- Test impact: HIGH — experimentally verified mock/to_thread interaction
- yfinance thread safety: LOW — no official docs; not relevant to this phase's sequential design

**Research date:** 2026-04-08
**Valid until:** 2026-05-08 (stable stdlib; yfinance version pinned in project)
