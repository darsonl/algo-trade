# Codebase Structure

**Analysis Date:** 2026-03-30

## Directory Layout

```
algo-trade/                     # Project root
├── main.py                     # Orchestration entry point
├── config.py                   # Config dataclass + module-level singleton
├── requirements.txt            # Python dependencies
├── watchlist.txt               # User-managed ticker list (one per line)
├── algo_trade.db               # SQLite database (runtime artifact, not committed)
├── schwab_token.json           # OAuth2 token (runtime artifact, not committed)
├── .env                        # Credentials and overrides (not committed)
├── .env.example                # Template for .env
│
├── screener/                   # Market data fetch + pre-analyst filters
│   ├── __init__.py
│   ├── universe.py             # Watchlist + S&P 500 ticker universe
│   ├── fundamentals.py         # P/E, dividend yield, earnings growth filter
│   └── technicals.py          # RSI, MA50, volume filter + compute_rsi()
│
├── analyst/                    # Claude AI analysis
│   ├── __init__.py
│   ├── news.py                 # yfinance headline fetch
│   └── claude_analyst.py      # Prompt builder, API call, response parser
│
├── discord_bot/                # Discord client and UI
│   ├── __init__.py
│   ├── bot.py                  # TradingBot (discord.Client), ApproveRejectView, /scan command
│   └── embeds.py              # build_recommendation_embed() (pure, no discord state)
│
├── database/                   # SQLite persistence
│   ├── __init__.py
│   ├── models.py               # Schema DDL, get_connection(), initialize_db()
│   └── queries.py             # All CRUD: create/get/update recommendations, trades, expiry
│
├── schwab_client/              # Schwab brokerage integration
│   ├── __init__.py
│   ├── auth.py                 # OAuth2 client construction, token path
│   └── orders.py              # build_market_buy(), place_order(), get_positions(), parse_positions()
│
├── tests/                      # Pytest test suite
│   ├── __init__.py
│   ├── test_main.py
│   ├── test_screener_universe.py
│   ├── test_screener_fundamentals.py
│   ├── test_screener_technicals.py
│   ├── test_analyst_claude.py
│   ├── test_analyst_news.py
│   ├── test_database.py
│   ├── test_discord_bot.py
│   ├── test_discord_embeds.py
│   ├── test_schwab_auth.py
│   └── test_schwab_orders.py
│
└── docs/
    └── superpowers/specs/      # Design documents
        └── 2026-03-28-schwab-algo-trade-design.md
```

## Directory Purposes

**`screener/`:**
- Purpose: Fetch market data from yfinance and apply cheap threshold filters before paying for Claude API calls
- Contains: Universe assembly, fundamental filter, technical indicator computation and filter
- Key files: `screener/technicals.py` (RSI implementation), `screener/universe.py` (Wikipedia S&P 500 scrape)

**`analyst/`:**
- Purpose: All Claude AI interaction — news aggregation and signal generation
- Contains: Headline extraction from yfinance, structured prompt construction, Claude API call, response parsing
- Key files: `analyst/claude_analyst.py` (contains `build_prompt`, `parse_claude_response`, `analyze_ticker`)

**`discord_bot/`:**
- Purpose: Human-in-the-loop interface — post recommendations, collect approvals
- Contains: Bot client subclass, interactive button view, embed formatter
- Key files: `discord_bot/bot.py` (main Discord logic), `discord_bot/embeds.py` (pure formatter)

**`database/`:**
- Purpose: Durable state for recommendations and executed trades
- Contains: Schema definition, connection factory, all SQL queries
- Key files: `database/queries.py` (all query logic), `database/models.py` (DDL)

**`schwab_client/`:**
- Purpose: Isolated Schwab brokerage API interaction
- Contains: OAuth2 token management, order construction and placement, position parsing
- Key files: `schwab_client/orders.py` (order placement + position fetch), `schwab_client/auth.py` (token lifecycle)

**`tests/`:**
- Purpose: Pytest unit tests; one file per source module
- Contains: Tests for every module; pure functions tested directly, I/O functions tested with mocks
- Key files: `tests/test_screener_technicals.py` (RSI math validation with synthetic series)

## Key File Locations

**Entry Points:**
- `main.py`: Process entry point; `main()` initialises everything and calls `bot.run()`
- `main.py:run_scan()`: Full screening pipeline; called by scheduler and `/scan` command

**Configuration:**
- `config.py`: `Config` dataclass and module-level `config = Config()` singleton
- `.env.example`: Canonical list of all supported environment variables

**Core Logic:**
- `main.py:should_recommend()`: Decision function combining Claude signal + technical filter
- `screener/technicals.py:compute_rsi()`: RSI with Wilder's smoothing
- `analyst/claude_analyst.py:build_prompt()`: Prompt template
- `analyst/claude_analyst.py:parse_claude_response()`: Strict response parser

**Database:**
- `database/models.py:initialize_db()`: Creates tables if absent; safe to call on every startup
- `database/queries.py:ticker_recommended_today()`: Dupe-prevention gate
- `database/queries.py:expire_stale_recommendations()`: Run at scan start to clean up >24h pending records

**Testing:**
- `tests/`: All test files; named `test_<module>.py` mirroring source layout

## Naming Conventions

**Files:**
- Snake case: `claude_analyst.py`, `screener_technicals.py` (test mirrors: `test_screener_technicals.py`)
- One module per file; no barrel `__init__.py` re-exports (all `__init__.py` files are empty)

**Directories:**
- Short, lowercase nouns describing domain: `screener/`, `analyst/`, `database/`, `discord_bot/`, `schwab_client/`

**Functions:**
- Pure filters: `passes_<noun>_filter()` — `passes_fundamental_filter`, `passes_technical_filter`
- Data fetchers: `fetch_<noun>()` — `fetch_fundamental_info`, `fetch_technical_data`, `fetch_news_headlines`
- DB operations: verb + noun — `create_recommendation`, `get_recommendation`, `update_recommendation_status`, `expire_stale_recommendations`

## Module Dependency Graph

```
main.py
  ├── config.py
  ├── database/models.py
  ├── database/queries.py
  ├── screener/universe.py
  ├── screener/fundamentals.py  → config.py
  ├── screener/technicals.py    → config.py, pandas
  ├── analyst/news.py
  ├── analyst/claude_analyst.py → config.py, anthropic
  └── discord_bot/bot.py
        ├── config.py
        ├── database/queries.py
        ├── discord_bot/embeds.py   → discord
        └── schwab_client/orders.py (lazy import, only when dry_run=False)
              └── schwab_client/auth.py → schwab-py
```

Notes:
- `yfinance` is imported lazily inside function bodies in `screener/fundamentals.py`, `screener/technicals.py`, and `analyst/news.py` — not at module level. This keeps import time fast and makes those functions easy to mock in tests.
- `anthropic` is imported lazily inside `analyst/claude_analyst.py:analyze_ticker()`.
- `schwab_client/orders.py` is only imported at runtime inside `ApproveRejectView.approve` when `dry_run=False`, preventing Schwab SDK import on dry-run startup.

## Config Loading Pattern

`config.py` loads `.env` at module import time via `python-dotenv`:

```python
load_dotenv(Path(__file__).parent / ".env")

@dataclass
class Config:
    schwab_app_key: str = os.getenv("SCHWAB_APP_KEY", "")
    # ... all fields with typed defaults

    def validate(self):
        # raises ValueError for any missing required credential

config = Config()   # module-level singleton
```

`main.py` imports the singleton: `from config import Config, config`

`config.validate()` is called explicitly in `main()` before any other initialisation. All modules that need config receive it as an explicit function parameter — no module accesses the `config` singleton directly except `main.py`.

## Where to Add New Code

**New screener filter:**
- Implementation: `screener/<filter_name>.py` following the `passes_<filter>_filter(data, config) -> bool` + `fetch_<filter>_data(ticker) -> dict` pattern
- Tests: `tests/test_screener_<filter_name>.py`
- Wire in: `main.py:run_scan()` between existing filter steps

**New analyst signal source (beyond Claude):**
- Implementation: `analyst/<source_name>.py`
- Tests: `tests/test_analyst_<source_name>.py`

**New Discord command:**
- Implementation: Add `app_commands.Command` in `discord_bot/bot.py:TradingBot.setup_hook()`
- No separate file needed unless the command is complex

**New database query:**
- Implementation: Add function to `database/queries.py`; add schema changes to `database/models.py:initialize_db()` as `CREATE TABLE IF NOT EXISTS` or `ALTER TABLE` guards
- Tests: `tests/test_database.py`

**New Schwab API operation:**
- Implementation: Add function to `schwab_client/orders.py`
- Tests: `tests/test_schwab_orders.py`

**New config threshold or credential:**
- Add field to `Config` dataclass in `config.py` with typed default
- If required (no safe default), add `validate()` check
- Document in `.env.example`

## Special Directories

**`.planning/codebase/`:**
- Purpose: GSD codebase analysis documents (STACK.md, ARCHITECTURE.md, etc.)
- Generated: By GSD map-codebase agent
- Committed: Yes

**`docs/superpowers/specs/`:**
- Purpose: Design specification documents
- Generated: No (human-authored)
- Committed: Yes

**`.claude/`:**
- Purpose: Claude Code GSD workflow tooling (agents, commands, hooks)
- Generated: Installed by GSD framework
- Committed: Yes

---

*Structure analysis: 2026-03-30*
