# Phase 1: Refactoring & Code Quality — COMPLETE

**Status:** ✅ Complete (2026-03-31)
**Requirements:** REF-01 to REF-10
**Tests:** All green after completion

## What was done

| Req | Change | File(s) |
|-----|--------|---------|
| REF-01 | Config() singleton removed; main() owns instance | `config.py`, `main.py` |
| REF-02 | Single `anthropic.Anthropic` client per scan (not per ticker) | `main.py` |
| REF-03 | Shared `yf.Ticker` object per ticker across fundamentals + technicals | `main.py` |
| REF-04 | `MIN_VOLUME_RATIO` promoted to `Config` field | `config.py`, `screener/technicals.py` |
| REF-05 | `MA_WINDOW=50`, `MIN_HISTORY_BARS=51` as module constants | `screener/technicals.py` |
| REF-06 | All deferred imports moved to module top level | all modules |
| REF-07 | `bot.dispatch("manual_scan")` replaced with `asyncio.create_task` via stored callback | `main.py`, `discord_bot/bot.py` |
| REF-08 | `earnings_growth` column added to recommendations table + migration | `database/models.py` |
| REF-09 | `parse_positions` made safe with `.get()` fallbacks | `schwab_client/orders.py` |
| REF-10 | SQLite WAL mode enabled in `get_connection` | `database/models.py` |
