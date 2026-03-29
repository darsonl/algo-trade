from __future__ import annotations
import asyncio
import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Config, config
from database.models import initialize_db
from database import queries
from screener.universe import get_watchlist, get_sp500_tickers, get_universe
from screener.fundamentals import passes_fundamental_filter, fetch_fundamental_info
from screener.technicals import passes_technical_filter, fetch_technical_data
from analyst.news import fetch_news_headlines
from analyst.claude_analyst import analyze_ticker
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
    """Register the daily scan job on an existing scheduler instance."""
    scheduler.add_job(
        job_fn,
        trigger=CronTrigger(hour=config.scan_hour, minute=config.scan_minute),
        id="daily_scan",
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
        sp500 = get_sp500_tickers()
    except Exception as exc:
        logger.warning("Could not fetch S&P 500 list: %s — using watchlist only", exc)
        sp500 = []

    universe = get_universe(watchlist_path, extra_tickers=sp500)
    logger.info("Universe: %d tickers", len(universe))

    for ticker in universe:
        if queries.ticker_recommended_today(config.db_path, ticker):
            continue

        try:
            info = fetch_fundamental_info(ticker)
            if not passes_fundamental_filter(info, config):
                continue

            headlines = fetch_news_headlines(ticker)
            analysis = analyze_ticker(ticker, info, headlines, config)

            tech_data = fetch_technical_data(ticker)
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

        except Exception as exc:
            logger.error("Error processing %s: %s", ticker, exc)
            continue

    logger.info("Scan complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config.validate()
    initialize_db(config.db_path)

    bot = TradingBot(config)
    scheduler = BackgroundScheduler()

    @bot.event
    async def on_ready():
        logger.info("Discord bot ready as %s", bot.user)
        configure_scheduler(scheduler, config, lambda: asyncio.run_coroutine_threadsafe(
            run_scan(bot, config), bot.loop
        ).result())
        scheduler.start()
        logger.info(
            "Scheduler started — daily scan at %02d:%02d",
            config.scan_hour, config.scan_minute,
        )

    @bot.event
    async def on_manual_scan():
        await run_scan(bot, config)

    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
