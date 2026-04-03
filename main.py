from __future__ import annotations
import asyncio
import hashlib
import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import yfinance as yf

from config import Config
from database.models import initialize_db
from database import queries
from screener.universe import get_watchlist, get_top_sp500_by_fundamentals, get_universe
from screener.fundamentals import passes_fundamental_filter, fetch_fundamental_info
from screener.technicals import passes_technical_filter, fetch_technical_data
from analyst.news import fetch_news_headlines
from analyst.claude_analyst import analyze_ticker, create_analyst_client, create_fallback_client
from discord_bot.bot import TradingBot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure orchestration helpers (tested in test_main.py)
# ---------------------------------------------------------------------------

def should_recommend(signal: str, tech_data: dict, config: Config) -> bool:
    """Return True only if signal is BUY and all technical filters pass."""
    if signal != "BUY":
        return False
    return passes_technical_filter(tech_data, config)


def configure_scheduler(scheduler: BackgroundScheduler, config: Config, job_fn) -> None:
    """Register one scan job per time in config.scan_times."""
    for i, time_str in enumerate(config.scan_times):
        hour, minute = map(int, time_str.split(":"))
        scheduler.add_job(
            job_fn,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=f"scan_{i}",
            replace_existing=True,
        )


# ---------------------------------------------------------------------------
# Scan pipeline
# ---------------------------------------------------------------------------

async def run_scan(bot: TradingBot, config: Config) -> None:
    """Run the full screening pipeline and post qualifying tickers to Discord."""
    logger.info("Starting scan...")
    queries.expire_stale_recommendations(config.db_path)

    watchlist_path = str(Path(__file__).parent / "watchlist.txt")
    try:
        sp500 = get_top_sp500_by_fundamentals(config)
    except Exception as exc:
        logger.warning("Could not fetch top S&P 500: %s — using watchlist only", exc)
        sp500 = []

    universe = get_universe(watchlist_path, extra_tickers=sp500)
    logger.info("Universe: %d tickers", len(universe))

    client = create_analyst_client(config)
    fallback_client = create_fallback_client(config)
    recommendations_posted = 0

    for ticker in universe:
        if queries.ticker_recommended_today(config.db_path, ticker):
            continue
        if queries.has_open_position(config.db_path, ticker):
            logger.debug("Skipping %s: open position exists", ticker)
            continue

        try:
            yf_ticker = yf.Ticker(ticker)
            info = fetch_fundamental_info(yf_ticker)
            if not passes_fundamental_filter(info, config):
                continue

            headlines = fetch_news_headlines(ticker)
            headline_hash = hashlib.sha256(
                "\n".join(sorted(headlines)).encode()
            ).hexdigest()
            cached = queries.get_cached_analysis(config.db_path, ticker, headline_hash)
            if cached:
                logger.debug("Cache hit for %s (hash %s...)", ticker, headline_hash[:8])
                analysis = cached
            else:
                analysis = await asyncio.to_thread(
                    analyze_ticker, ticker, info, headlines, config,
                    client, fallback_client
                )
                try:
                    queries.set_cached_analysis(
                        config.db_path, ticker, headline_hash,
                        analysis["signal"], analysis["reasoning"]
                    )
                except Exception as cache_exc:
                    logger.warning("Failed to write analyst cache for %s: %s", ticker, cache_exc)

            tech_data = fetch_technical_data(yf_ticker)
            if not should_recommend(analysis["signal"], tech_data, config):
                continue

            rec_id = queries.create_recommendation(
                db_path=config.db_path,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data["price"],
                dividend_yield=info.get("dividendYield"),
                pe_ratio=info.get("trailingPE"),
                earnings_growth=info.get("earningsGrowth"),
            )

            message_id = await bot.send_recommendation(
                rec_id=rec_id,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data["price"],
                dividend_yield=info.get("dividendYield"),
                pe_ratio=info.get("trailingPE"),
            )
            queries.set_discord_message_id(config.db_path, rec_id, message_id)
            logger.info("Recommended %s", ticker)
            recommendations_posted += 1

        except Exception as exc:
            logger.error("Error processing %s: %s", ticker, exc)
            continue

    if recommendations_posted == 0:
        logger.warning("Scan complete: 0 recommendations posted.")
        await bot.send_ops_alert("Scan complete: 0 recommendations posted.")
    else:
        logger.info("Scan complete. %d recommendation(s) posted.", recommendations_posted)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Configure logging and the DB, construct the Discord bot and scheduler, then block until the bot exits."""
    config = Config()
    config.validate()

    import logging.handlers
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    _log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    _file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "algo_trade.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setFormatter(_fmt)

    _stream_handler = logging.StreamHandler()
    _stream_handler.setFormatter(_fmt)

    logging.root.setLevel(_log_level)
    logging.root.addHandler(_file_handler)
    logging.root.addHandler(_stream_handler)

    initialize_db(config.db_path)

    bot = TradingBot(config)
    bot._scan_callback = lambda: run_scan(bot, config)
    scheduler = BackgroundScheduler()

    @bot.event
    async def on_ready():
        """Validate the Discord channel, warn if live trading is active, then start the APScheduler scan jobs."""
        logger.info("Discord bot ready as %s", bot.user)
        try:
            await bot.fetch_channel(config.discord_channel_id)
            logger.info("Discord channel %s verified.", config.discord_channel_id)
        except Exception as exc:
            logger.error(
                "Cannot access Discord channel %s: %s — aborting startup.",
                config.discord_channel_id,
                exc,
            )
            raise RuntimeError(
                f"Discord channel {config.discord_channel_id} not accessible: {exc}"
            ) from exc

        if not config.dry_run and not config.paper_trading:
            logger.warning(
                "LIVE TRADING ACTIVE: DRY_RUN=false and PAPER_TRADING=false. "
                "Real orders will be placed on Schwab."
            )
            await bot.send_ops_alert(
                "WARNING: Bot started in LIVE TRADING mode. "
                "DRY_RUN=false AND PAPER_TRADING=false — real orders will be placed."
            )

        configure_scheduler(
            scheduler,
            config,
            lambda: asyncio.run_coroutine_threadsafe(
                run_scan(bot, config), bot.loop
            ).result(),
        )
        scheduler.start()
        logger.info(
            "Scheduler started — daily scan at %02d:%02d",
            config.scan_hour, config.scan_minute,
        )

    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
