# Technology Stack

**Analysis Date:** 2026-03-30

## Languages

**Primary:**
- Python 3.14 - All application code, scripts, and tests

**Secondary:**
- SQL (SQLite dialect) - Database schema and queries via `database/models.py` and `database/queries.py`

## Runtime

**Environment:**
- Python 3.14 (local machine — no containerization detected)

**Package Manager:**
- pip
- Lockfile: `requirements.txt` (pinned versions, no `poetry.lock` or `Pipfile.lock`)

## Frameworks

**Discord Bot:**
- `discord.py` 2.4.0 - Async Discord client with slash commands, UI views (buttons), and embed messages
  - Used in `discord_bot/bot.py`, `discord_bot/embeds.py`
  - Uses `discord.Client`, `discord.ui.View`, `discord.ui.Button`, `app_commands.CommandTree`

**Scheduling:**
- `APScheduler` 3.10.4 - Background cron scheduler for the daily scan job
  - Used in `main.py` via `BackgroundScheduler` + `CronTrigger`

**Testing:**
- `pytest` 8.3.4 - Test runner
- `pytest-asyncio` 0.24.0 - Async test support for Discord coroutine testing
  - Config: no `pytest.ini` or `pyproject.toml` detected; tests live in `tests/`

**Build/Dev:**
- `python-dotenv` 1.0.1 - Loads `.env` into `os.getenv` at import time in `config.py`

## Key Dependencies

**Critical:**
- `anthropic` 0.40.0 - Anthropic Python SDK; calls `claude-opus-4-6` model via `client.messages.create()`
  - Used in `analyst/claude_analyst.py`
- `schwab-py` 1.4.0 - Official Schwab brokerage API client; handles OAuth2 flow and order placement
  - Used in `schwab_client/auth.py`, `schwab_client/orders.py`
- `discord.py` 2.4.0 - Bot framework; drives all user interaction and approval workflow
  - Used in `discord_bot/bot.py`
- `yfinance` 0.2.51 - Yahoo Finance data fetching for prices, fundamentals, technicals, and news
  - Used in `screener/fundamentals.py`, `screener/technicals.py`, `analyst/news.py`

**Infrastructure:**
- `pandas` 2.2.3 - DataFrame operations on yfinance price history for RSI and MA50 calculations
  - Used in `screener/technicals.py`
- `sqlite3` (stdlib) - Embedded database, no additional ORM; raw SQL via `database/queries.py`

## Configuration

**Environment:**
- All credentials and thresholds loaded from `.env` via `python-dotenv`
- `.env` file loaded at module import time in `config.py` (line 6: `load_dotenv(Path(__file__).parent / ".env")`)
- Config is a `@dataclass` at `config.py` with typed defaults; `Config.validate()` raises `ValueError` on missing credentials at startup
- `.env.example` present as reference template

**Key env vars required at runtime:**
- `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `SCHWAB_CALLBACK_URL`, `SCHWAB_ACCOUNT_HASH`
- `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`
- `ANTHROPIC_API_KEY`
- `DRY_RUN` (default: `"true"`), `PAPER_TRADING` (default: `"true"`)
- `MAX_POSITION_SIZE_USD` (default: `500.0`)
- `SCAN_HOUR` (default: `9`), `SCAN_MINUTE` (default: `0`)
- `DB_PATH` (default: `algo_trade.db` adjacent to `config.py`)

**Build:**
- No build step — pure Python, run directly with `python main.py`

## Platform Requirements

**Development:**
- Python 3.14+
- Internet access (Schwab OAuth2, Anthropic API, Discord Gateway, Yahoo Finance)
- A browser available on first run (Schwab OAuth2 opens browser via `schwab.auth.easy_client`)
- SQLite (stdlib — no server required)

**Production:**
- Same as development; designed for a persistent local process (not serverless/containerized)
- Schwab OAuth token cached to `schwab_token.json` at project root; must be writable

---

*Stack analysis: 2026-03-30*
