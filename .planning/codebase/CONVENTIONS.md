# Coding Conventions

**Analysis Date:** 2026-03-30

## Naming Patterns

**Files:**
- All source files: `snake_case.py` (e.g., `claude_analyst.py`, `discord_bot/embeds.py`)
- All packages: `snake_case/` with `__init__.py` (e.g., `screener/`, `discord_bot/`, `schwab_client/`)
- Test files: `test_<module_name>.py` mirroring the source path (e.g., `tests/test_screener_technicals.py`)

**Functions:**
- All functions: `snake_case` (e.g., `passes_fundamental_filter`, `build_prompt`, `fetch_technical_data`)
- Predicate functions (returning bool) prefixed with a verb: `passes_*`, `should_*` (e.g., `passes_technical_filter`, `should_recommend`)
- Fetch functions prefixed with `fetch_`: `fetch_fundamental_info`, `fetch_news_headlines`, `fetch_technical_data`
- Builder functions prefixed with `build_`: `build_prompt`, `build_recommendation_embed`, `build_market_buy`
- Private helpers in test files: prefixed with `_` (e.g., `_make_rising_prices`, `_make_account_response`)

**Variables:**
- All variables: `snake_case`
- Config thresholds named to match their env var counterpart in lowercase: `max_rsi`, `min_dividend_yield`
- Ticker strings: uppercase throughout the pipeline (enforced in `screener/universe.py`)

**Classes:**
- `PascalCase` (e.g., `Config`, `TradingBot`, `ApproveRejectView`)
- Discord UI classes suffixed with `View`: `ApproveRejectView`

**Constants / module-level:**
- Private module constants: `_UPPER_SNAKE` with leading underscore (e.g., `_VALID_SIGNALS` in `analyst/claude_analyst.py`)

## Code Style

**Formatting:**
- No formatter config file detected (no `.prettierrc`, `pyproject.toml` with black config, or `ruff.toml`)
- Consistent 4-space indentation throughout
- Lines generally kept short; multi-line expressions aligned naturally

**Linting:**
- No `.flake8`, `.pylintrc`, or `ruff.toml` detected; no enforced linter configuration

**Type Hints:**
- Used consistently on all function signatures throughout the codebase
- Return types always annotated (e.g., `-> bool`, `-> dict`, `-> int`, `-> None`)
- Parameter types annotated (e.g., `ticker: str`, `prices: pd.Series`, `config: Config`)
- Union types using `|` syntax (Python 3.10+ style): `float | None`, `sqlite3.Row | None`
- `list[str]` and `dict` used as generic annotations (lowercase, PEP 585 style)
- `from __future__ import annotations` used in `main.py` and `analyst/claude_analyst.py` for deferred evaluation

**Dataclass Pattern:**
- `config.py` uses `@dataclass` for typed, documented configuration with defaults
- All fields typed; boolean flags use inline `.lower() == "true"` coercion from env strings
- A `validate()` method on the dataclass performs startup fast-fail checks; all checks raise `ValueError` with descriptive messages

**Pure Functions:**
- Filter functions (`passes_fundamental_filter`, `passes_technical_filter`) are stateless — they take data and config, return bool
- Prompt builders (`build_prompt`) take plain data, return a string, have no side effects
- Parser functions (`parse_claude_response`) take a string, return a dict, raise `ValueError` on bad input
- Orchestration helpers (`should_recommend`, `configure_scheduler`) are pure/stateless for testability
- `yfinance` imports deferred inside network-touching functions (`fetch_technical_data`, `fetch_fundamental_info`, `fetch_news_headlines`) to keep pure functions importable without side effects

## Error Handling

**Validation errors (ValueError):**
- Raised by pure parsing/validation functions when input is structurally wrong
- `parse_claude_response` raises `ValueError` with descriptive messages: `"Response missing SIGNAL: line"`, `"Invalid signal '...'"`
- `compute_rsi` raises `ValueError` with a clear message when too few data points are given
- `build_recommendation_embed` raises `ValueError` on invalid signal values
- `Config.validate()` raises `ValueError` for each missing credential

**Runtime errors (RuntimeError):**
- Used for unexpected runtime state, e.g., Discord channel not found: `raise RuntimeError(f"Discord channel {id} not found")`

**Graceful degradation in async scan loop:**
- `run_scan()` in `main.py` wraps each ticker iteration in `try/except Exception` with `logger.error(...)` and `continue`
- S&P 500 fetch failure is caught and logged as a warning; execution continues with watchlist only

**None-safety pattern:**
- Filter functions consistently use `info.get("key")` and check for `None` before comparison
- `any(v is None for v in (...))` used to short-circuit when any field is missing (e.g., `passes_technical_filter`)

## Docstrings and Comments

**Docstring style:**
- Single-line or short multi-line docstrings on all public functions
- Format: imperative first sentence (e.g., `"Compute RSI using Wilder's smoothing method."`, `"Return True only if all technical criteria are met."`)
- No Sphinx/Google/NumPy docstring format — plain prose only
- Key parameter expectations documented inline when non-obvious: `"Expects keys: 'rsi', 'price', 'ma50', 'volume', 'avg_volume'"`

**Inline comments:**
- Used to explain non-obvious algorithm steps (e.g., `# Seed with simple average for first period`, `# Wilder's smoothing for remaining values`)
- Section separators use `# ---` style dashed lines with a label (e.g., `# --- Pure orchestration helpers (tested in test_main.py) ---`)
- Test helper functions have docstrings explaining their purpose (e.g., `"Generates a steadily rising price series — RSI will be near 100."`)

## Import Organization

**Order (observed across modules):**
1. `from __future__ import annotations` (when present, always first)
2. Standard library imports (`os`, `math`, `logging`, `sqlite3`, `asyncio`, `pathlib`)
3. Third-party imports (`pandas`, `discord`, `apscheduler`, `anthropic`)
4. Local application imports (`from config import Config`, `from database import queries`, etc.)

**Deferred imports:**
- Network/heavyweight libraries imported inside functions to avoid import-time side effects: `import yfinance as yf` inside `fetch_*` functions, `import anthropic` inside `analyze_ticker`

**No wildcard imports (`from x import *`) detected anywhere.**

**Path aliases:**
- None; imports use relative package paths from project root (e.g., `from screener.fundamentals import passes_fundamental_filter`)

## Configuration Patterns

**Single source of truth:**
- All configuration lives in `config.py` as a `Config` dataclass instance `config = Config()`
- Every threshold, credential, and feature flag is a typed field with a safe default
- Defaults are safe: `DRY_RUN=true`, `PAPER_TRADING=true`, `MAX_POSITION_SIZE_USD=500`

**Environment variable loading:**
- `python-dotenv` loads `.env` anchored to the project root via `Path(__file__).parent / ".env"`
- All `os.getenv()` calls include a default value — no bare `os.getenv("KEY")` without fallback

**Config passed explicitly:**
- `Config` instance is passed as a parameter to all functions that need it; no global config access inside modules
- Exception: `config.py` creates the module-level `config` singleton that `main.py` imports directly

**Feature flags:**
- `dry_run: bool` — when `True`, Discord Approve button logs instead of placing orders
- `paper_trading: bool` — when `True`, Schwab uses the paper trading endpoint

---

*Convention analysis: 2026-03-30*
