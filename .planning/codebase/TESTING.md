# Testing Patterns

**Analysis Date:** 2026-03-30

## Test Framework

**Runner:**
- pytest 8.3.4
- Config: no `pytest.ini` or `pyproject.toml` detected; pytest discovers tests via default convention
- Async support: `pytest-asyncio==0.24.0` installed (no async tests currently written)

**Assertion Library:**
- pytest's built-in `assert` statements throughout; no `unittest.TestCase`

**Run Commands:**
```bash
pytest                                                        # Run all tests
pytest -v                                                     # Verbose output
pytest tests/test_screener_technicals.py                      # Single file
pytest tests/test_analyst_claude.py::test_parse_buy_signal -v # Single test by name
```

## Test File Organization

**Location:**
- All tests live in `tests/` directory at project root (not co-located with source)
- One test file per source module

**Naming:**
- `tests/test_<package>_<module>.py` for submodule tests (e.g., `test_screener_technicals.py`)
- `tests/test_<module>.py` for top-level module tests (e.g., `test_main.py`, `test_database.py`)

**Structure:**
```
tests/
├── __init__.py
├── test_analyst_claude.py        # build_prompt, parse_claude_response
├── test_analyst_news.py          # extract_headlines
├── test_database.py              # initialize_db, CRUD queries
├── test_discord_bot.py           # compute_share_quantity (pure helper only)
├── test_discord_embeds.py        # build_recommendation_embed
├── test_main.py                  # should_recommend, configure_scheduler
├── test_schwab_auth.py           # get_token_path
├── test_schwab_orders.py         # build_market_buy, parse_positions
├── test_screener_fundamentals.py # passes_fundamental_filter
├── test_screener_technicals.py   # compute_rsi, passes_technical_filter
└── test_screener_universe.py     # get_watchlist, get_universe
```

## Test Structure

**Suite organization:**
```python
# Section headers group related tests with comment banners
# --- RSI unit tests ---
def test_rsi_overbought_on_rising_prices():
    prices = _make_rising_prices()
    rsi = compute_rsi(prices)
    assert rsi > 70

# --- passes_technical_filter unit tests ---
def test_passes_when_all_technical_criteria_met(cfg):
    assert passes_technical_filter(make_ticker_data(), cfg) is True
```

**Fixture pattern (pytest fixtures):**
```python
@pytest.fixture
def cfg():
    c = Config()
    c.max_rsi = 70.0
    return c
```
- Fixtures create a `Config()` instance and set thresholds explicitly
- Used in: `test_screener_technicals.py`, `test_screener_fundamentals.py`
- `autouse=True` fixture used in `test_database.py` to create a fresh DB and clean up after every test

**Test data factory pattern:**
```python
def make_ticker_data(rsi=55.0, price=110.0, ma50=100.0, volume=1_200_000, avg_volume=1_000_000):
    return {"rsi": rsi, "price": price, "ma50": ma50, "volume": volume, "avg_volume": avg_volume}

def make_info(pe=15.0, div_yield=0.03, earnings_growth=0.10):
    return {"trailingPE": pe, "dividendYield": div_yield, "earningsGrowth": earnings_growth}
```
- Factory functions with named keyword arguments and sensible passing defaults
- Tests override only the specific field being tested (e.g., `make_ticker_data(rsi=75.0)`)
- Used across: `test_screener_technicals.py`, `test_screener_fundamentals.py`, `test_main.py`, `test_schwab_orders.py`, `test_discord_embeds.py`, `test_analyst_news.py`

**Boundary value testing:**
- Tests explicitly check boundary conditions (e.g., `test_passes_at_exact_boundary`, `test_passes_at_rsi_boundary`)
- Pattern: assert that a value exactly at the threshold limit returns `True` (inclusive boundary)

## Mocking

**No mocking library in use.** No `unittest.mock`, `pytest-mock`, or `MagicMock` imports detected anywhere in the test suite.

**Strategy instead of mocking:**
- External dependencies (yfinance, Anthropic API, Schwab API, Discord client) are **never called in tests**
- Pure function design means testable logic is separated from I/O at the module level
- Network-touching functions (`fetch_*` functions) have no tests; only their pure counterparts are tested

**What is tested directly (no mock needed):**
- `passes_fundamental_filter` — accepts a plain dict
- `passes_technical_filter` — accepts a plain dict
- `compute_rsi` — accepts a `pd.Series`
- `build_prompt` — accepts strings and dicts
- `parse_claude_response` — accepts a string
- `extract_headlines` — accepts a list of dicts
- `should_recommend` — accepts string + dict + Config
- `configure_scheduler` — operates on a real `BackgroundScheduler` instance (no network)
- `build_recommendation_embed` — operates on discord.py in-memory objects (no network)
- `compute_share_quantity` — pure math function
- `build_market_buy` — returns a plain dict (no API call)
- `parse_positions` — accepts a plain dict
- `get_token_path` — returns a file path string

## Fixtures and Factories

**pytest tmp_path fixture:**
```python
@pytest.fixture
def tmp_watchlist(tmp_path):
    p = tmp_path / "watchlist.txt"
    p.write_text("# comment\nAAPL\n\nJNJ\n VYM \n")
    return str(p)
```
- Used in `test_screener_universe.py` to test file I/O without touching real filesystem

**Real SQLite DB fixture:**
```python
DB_PATH = "test_algo_trade.db"

@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)
```
- Creates a real SQLite file before each test, removes it after
- This is the only integration-style test in the suite (actual file I/O and SQL)
- Location: `tests/test_database.py`

**Synthetic price series:**
```python
def _make_rising_prices(n=30, start=100.0, step=1.0):
    """Generates a steadily rising price series — RSI will be near 100."""
    return pd.Series([start + i * step for i in range(n)])

def _make_falling_prices(n=30, start=130.0, step=1.0):
    return pd.Series([start - i * step for i in range(n)])
```
- Used in `test_screener_technicals.py` to validate RSI math without live market data
- Series designed to produce predictable RSI values (near 100 for rising, near 0 for falling, near 50 for alternating)

## Coverage

**Requirements:** None enforced (no coverage threshold config)

**View Coverage:**
```bash
pytest --cov=. --cov-report=term-missing
```
(requires `pytest-cov`, which is not in `requirements.txt` — must be installed separately)

## Test Types

**Unit Tests (all current tests):**
- Test individual pure functions in isolation
- No network calls, no process spawning, no external services
- Fast: all tests run without I/O except `test_database.py` and `test_screener_universe.py` (local filesystem only)

**Integration Tests:**
- `test_database.py` is a lightweight integration test: creates a real SQLite DB, runs actual SQL queries, then removes the file
- No HTTP integration tests exist

**E2E Tests:**
- Not used. `test_discord_live.py` exists at project root (outside `tests/`) but is not part of the pytest suite

## Coverage Gaps

**Untested: `TradingBot` class and `ApproveRejectView` (Discord interaction handlers)**
- File: `discord_bot/bot.py`
- `TradingBot.send_recommendation`, `ApproveRejectView.approve`, `ApproveRejectView.reject` require a live Discord connection
- The Approve button's order-placement branch (`if not self.config.dry_run`) has no test coverage
- Risk: Button logic that writes trades and calls Schwab could silently break
- Only `compute_share_quantity` (a pure helper extracted from the class) is tested

**Untested: `fetch_*` network functions**
- `fetch_fundamental_info` in `screener/fundamentals.py` — wraps `yf.Ticker(ticker).info`
- `fetch_technical_data` in `screener/technicals.py` — wraps `yf.Ticker(ticker).history(...)`
- `fetch_news_headlines` in `analyst/news.py` — wraps `yf.Ticker(ticker).news`
- Risk: yfinance API contract changes or data shape changes will cause silent failures at runtime
- Priority: Medium (yfinance is relatively stable, and pure logic is well tested)

**Untested: `analyze_ticker` (Anthropic API call)**
- File: `analyst/claude_analyst.py`
- `analyze_ticker` function instantiates `anthropic.Anthropic` and calls `client.messages.create`
- The injectable `client` parameter exists (`def analyze_ticker(..., client=None)`) but is unused in tests
- Risk: API contract changes or error responses (rate limit, network error) are not exercised
- Priority: Medium — a mock client test would be straightforward given the injectable parameter

**Untested: `run_scan` async pipeline**
- File: `main.py`
- The full async scan pipeline is not tested end-to-end
- `pytest-asyncio` is installed but no async tests are written
- Risk: Integration of components (DB write → Discord send → message ID storage) could break silently
- Priority: Medium

**Untested: Schwab OAuth2 flow**
- Files: `schwab_client/auth.py`, `schwab_client/orders.py` (the `place_order` function)
- `test_schwab_auth.py` only tests `get_token_path` (a path helper); the OAuth flow itself has no test
- `place_order` (which calls the Schwab API) is not tested
- Risk: Order placement errors are only caught at runtime in production
- Priority: Low (requires Schwab sandbox credentials; dry-run default mitigates production risk)

**Untested: `ticker_recommended_today` and `set_discord_message_id` queries**
- File: `database/queries.py`
- `ticker_recommended_today` is the duplicate-prevention function — not tested in `test_database.py`
- `set_discord_message_id` is not tested
- Risk: A bug in `ticker_recommended_today` could allow duplicate recommendations to be posted
- Priority: High — this is the deduplication guard; a simple DB test would suffice

**Untested: `screener/universe.py` — `get_sp500_tickers`**
- File: `screener/universe.py`
- `get_sp500_tickers` fetches S&P 500 from Wikipedia via `pandas.read_html`; not tested
- Only `get_watchlist` and `get_universe` are tested
- Risk: Wikipedia table format changes would break silently
- Priority: Low (failure is caught and logged gracefully in `run_scan`)

---

*Testing analysis: 2026-03-30*
