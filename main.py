from __future__ import annotations
import asyncio
import hashlib
import logging
import sqlite3
from datetime import date
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import yfinance as yf

from config import Config
from database.models import initialize_db
from database import queries
from screener.universe import get_watchlist, get_top_sp500_by_fundamentals, get_universe, partition_watchlist
from screener.fundamentals import passes_fundamental_filter, fetch_fundamental_info
from screener.technicals import passes_technical_filter, fetch_technical_data
from analyst.news import fetch_news_headlines
from analyst.claude_analyst import analyze_ticker, create_analyst_client, create_fallback_client, analyze_sell_ticker, analyze_etf_ticker
from screener.macro import fetch_macro_context
from screener.exit_signals import check_exit_signals
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


def configure_scheduler(
    scheduler: BackgroundScheduler,
    config: Config,
    job_fn,
    times: list[str] | None = None,
    job_id_prefix: str = "scan",
) -> None:
    """Register one scan job per time.

    Defaults to stock scan (config.scan_times, prefix 'scan'); pass times +
    job_id_prefix for ETF scheduling (per Phase 12 D-03).
    """
    scan_times = times if times is not None else config.scan_times
    for i, time_str in enumerate(scan_times):
        hour, minute = map(int, time_str.split(":"))
        scheduler.add_job(
            job_fn,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=f"{job_id_prefix}_{i}",
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
        sp500 = await asyncio.to_thread(get_top_sp500_by_fundamentals, config)  # P8-audit: already wrapped (hotfix ae66e64)
    except Exception as exc:
        logger.warning("Could not fetch top S&P 500: %s — using watchlist only", exc)
        sp500 = []

    universe = get_universe(watchlist_path, extra_tickers=sp500)
    # Filter ETFs out of stock scan universe
    try:
        stocks_only, _etfs = await asyncio.to_thread(partition_watchlist, universe)  # P8-audit: already wrapped (Phase 7)
        universe = stocks_only
    except Exception as exc:
        logger.warning("partition_watchlist failed: %s — using full universe", exc)
    logger.info("Universe: %d tickers", len(universe))

    # Fetch macro context once for all tickers (D-02)
    try:
        macro_context = await asyncio.to_thread(fetch_macro_context)
    except Exception as exc:
        logger.warning("Macro context fetch failed: %s — continuing without macro", exc)
        macro_context = {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}

    client = create_analyst_client(config)
    fallback_client = create_fallback_client(config)
    recommendations_posted = 0
    error_count = 0
    errors_posted = 0

    for ticker in universe:
        if queries.ticker_recommended_today(config.db_path, ticker):
            continue
        if queries.has_open_position(config.db_path, ticker):
            logger.debug("Skipping %s: open position exists", ticker)
            continue

        try:
            yf_ticker = yf.Ticker(ticker)
            info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
            if not passes_fundamental_filter(info, config):
                continue

            headlines = await asyncio.to_thread(
                fetch_news_headlines, ticker, alpha_vantage_api_key=config.alpha_vantage_api_key
            )
            headline_hash = hashlib.sha256(
                "\n".join(sorted(headlines)).encode()
            ).hexdigest()
            cached = queries.get_cached_analysis(config.db_path, ticker, headline_hash)
            if cached:
                logger.debug("Cache hit for %s (hash %s...)", ticker, headline_hash[:8])
                analysis = cached
            else:
                # D-11: quota guard — skip if both providers exhausted
                primary_count = queries.get_analyst_call_count_today(
                    config.db_path, config.analyst_provider
                )
                fallback_count = (
                    queries.get_analyst_call_count_today(
                        config.db_path, config.analyst_fallback_provider
                    )
                    if config.analyst_fallback_provider
                    else config.analyst_daily_limit
                )
                if primary_count >= config.analyst_daily_limit and fallback_count >= config.analyst_daily_limit:
                    logger.warning(
                        "Daily analyst quota reached for all providers, skipping analysis for %s",
                        ticker,
                    )
                    continue
                analysis = await asyncio.to_thread(
                    analyze_ticker, ticker, info, headlines, config,
                    client, fallback_client, macro_context=macro_context
                )
                queries.increment_analyst_call_count(
                    config.db_path, analysis["provider_used"]
                )
                try:
                    queries.set_cached_analysis(
                        config.db_path, ticker, headline_hash,
                        analysis["signal"], analysis["reasoning"],
                        confidence=analysis.get("confidence"),
                    )
                except Exception as cache_exc:
                    logger.warning("Failed to write analyst cache for %s: %s", ticker, cache_exc)

            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
            if not should_recommend(analysis["signal"], tech_data, config):
                continue

            raw_yield = info.get("dividendYield")
            div_yield = raw_yield / 100 if raw_yield is not None and raw_yield > 1 else raw_yield

            rec_id = queries.create_recommendation(
                db_path=config.db_path,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data["price"],
                dividend_yield=div_yield,
                pe_ratio=info.get("trailingPE"),
                earnings_growth=info.get("earningsGrowth"),
                confidence=analysis.get("confidence"),
            )

            message_id = await bot.send_recommendation(
                rec_id=rec_id,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data["price"],
                dividend_yield=div_yield,
                pe_ratio=info.get("trailingPE"),
                confidence=analysis.get("confidence"),
            )
            queries.set_discord_message_id(config.db_path, rec_id, message_id)
            logger.info("Recommended %s", ticker)
            recommendations_posted += 1

        except Exception as exc:
            logger.error("Error processing %s: %s", ticker, exc)
            error_count += 1
            if errors_posted < 3:
                await bot.send_ops_alert(f"[ERROR] {ticker}: {type(exc).__name__}")
                errors_posted += 1
            continue

    if error_count > 3:
        overflow = error_count - 3
        await bot.send_ops_alert(f"[{overflow} more errors not shown \u2014 check logs]")

    if recommendations_posted == 0:
        logger.warning("Scan complete: 0 recommendations posted.")
        await bot.send_ops_alert("Scan complete: 0 recommendations posted.")
    else:
        logger.info("Scan complete. %d recommendation(s) posted.", recommendations_posted)

    # --- Sell pass: evaluate open positions for exit signals ---
    open_positions = queries.get_open_positions(config.db_path)
    logger.info("Sell pass: evaluating %d open position(s)", len(open_positions))

    for pos in open_positions:
        ticker = pos["ticker"]

        # D-06: skip sell-blocked positions entirely
        if pos["sell_blocked"]:
            logger.debug("Skipping %s: sell_blocked", ticker)
            # But still check if RSI dropped — reset sell_blocked if so
            try:
                yf_ticker = yf.Ticker(ticker)
                tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
                if tech_data.get("rsi") is not None and tech_data["rsi"] <= config.sell_rsi_threshold:
                    queries.reset_sell_blocked(config.db_path, ticker)
                    logger.info("Reset sell_blocked for %s (RSI %.1f <= %.1f)", ticker, tech_data["rsi"], config.sell_rsi_threshold)
            except Exception as exc:
                logger.warning("Could not check RSI for sell_blocked reset on %s: %s", ticker, exc)
            continue

        try:
            yf_ticker = yf.Ticker(ticker)
            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
            sell_info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)

            # D-01 stage 1: RSI exit signal check
            if not check_exit_signals(tech_data, config):
                continue

            # D-01 stage 2: analyst sell analysis
            entry_price = pos["avg_cost_usd"]
            current_price = tech_data["price"]
            pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0.0

            try:
                entry_date = date.fromisoformat(pos["entry_date"])
                hold_days = (date.today() - entry_date).days
            except (ValueError, TypeError):
                hold_days = 0

            headlines = await asyncio.to_thread(
                fetch_news_headlines, ticker, alpha_vantage_api_key=config.alpha_vantage_api_key
            )

            # D-11: quota guard for sell analyst call
            primary_count = queries.get_analyst_call_count_today(
                config.db_path, config.analyst_provider
            )
            fallback_count = (
                queries.get_analyst_call_count_today(
                    config.db_path, config.analyst_fallback_provider
                )
                if config.analyst_fallback_provider
                else config.analyst_daily_limit
            )
            if primary_count >= config.analyst_daily_limit and fallback_count >= config.analyst_daily_limit:
                logger.warning(
                    "Daily analyst quota reached for all providers, skipping sell analysis for %s",
                    ticker,
                )
                continue

            analysis = await asyncio.to_thread(
                analyze_sell_ticker,
                ticker, entry_price, current_price, pnl_pct, hold_days,
                tech_data["rsi"], headlines, config, client, fallback_client,
                macd_line=tech_data.get("macd_line"),
                signal_line=tech_data.get("signal_line"),
                macro_context=macro_context,
                info=sell_info,
            )
            queries.increment_analyst_call_count(
                config.db_path, analysis["provider_used"]
            )

            if analysis["signal"] != "SELL":
                logger.info("Analyst says HOLD for %s", ticker)
                continue

            # Create sell recommendation
            rec_id = queries.create_recommendation(
                db_path=config.db_path,
                ticker=ticker,
                signal="SELL",
                reasoning=analysis["reasoning"],
                price=current_price,
                dividend_yield=None,
                pe_ratio=None,
                confidence=analysis.get("confidence"),
            )

            message_id = await bot.send_sell_recommendation(
                rec_id=rec_id,
                ticker=ticker,
                reasoning=analysis["reasoning"],
                entry_price=entry_price,
                current_price=current_price,
                pnl_pct=pnl_pct,
                shares=pos["shares"],
                rsi=tech_data["rsi"],
                confidence=analysis.get("confidence"),
            )
            queries.set_discord_message_id(config.db_path, rec_id, message_id)
            logger.info("Sell recommendation posted for %s", ticker)

        except Exception as exc:
            logger.error("Error in sell evaluation for %s: %s", ticker, exc)
            continue


# ---------------------------------------------------------------------------
# ETF scan pipeline
# ---------------------------------------------------------------------------

async def run_scan_etf(bot: TradingBot, config: Config) -> None:
    """Run the ETF screening pipeline and post qualifying tickers to Discord (per ETF-02)."""
    logger.info("Starting ETF scan...")
    queries.expire_stale_recommendations(config.db_path)

    etf_watchlist_path = str(Path(__file__).parent / "etf_watchlist.txt")
    etf_tickers = get_watchlist(etf_watchlist_path)

    # D-08 / ASYNC-03: wrap partition_watchlist in asyncio.to_thread
    _stocks, etfs = await asyncio.to_thread(partition_watchlist, etf_tickers)  # P8-audit: already wrapped (Phase 7)
    logger.info("ETF universe: %d tickers", len(etfs))

    # Fetch macro context once for all ETFs (D-02)
    try:
        macro_context = await asyncio.to_thread(fetch_macro_context)
    except Exception as exc:
        logger.warning("Macro context fetch failed: %s — continuing without macro", exc)
        macro_context = {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}

    client = create_analyst_client(config)
    fallback_client = create_fallback_client(config)
    recommendations_posted = 0
    error_count = 0
    errors_posted = 0

    for ticker in etfs:
        if queries.ticker_recommended_today(config.db_path, ticker):
            continue
        if queries.has_open_position(config.db_path, ticker):
            logger.debug("Skipping %s: open position exists", ticker)
            continue

        try:
            yf_ticker = yf.Ticker(ticker)

            # Fetch technical data (no fundamental filter for ETFs)
            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)

            # Fetch expense ratio from yfinance info
            info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
            expense_ratio = info.get("netExpenseRatio")
            if expense_ratio is None:
                logger.debug("Expense ratio unavailable for %s", ticker)

            # Fetch news headlines (per D-01)
            headlines = await asyncio.to_thread(
                fetch_news_headlines, ticker, alpha_vantage_api_key=config.alpha_vantage_api_key
            )

            # Analyst cache check (same pattern as run_scan)
            headline_hash = hashlib.sha256(
                "\n".join(sorted(headlines)).encode()
            ).hexdigest()
            cached = queries.get_cached_analysis(config.db_path, ticker, headline_hash)
            if cached:
                logger.debug("Cache hit for %s (hash %s...)", ticker, headline_hash[:8])
                analysis = cached
            else:
                # Quota guard (same pattern as run_scan buy pass)
                primary_count = queries.get_analyst_call_count_today(
                    config.db_path, config.analyst_provider
                )
                fallback_count = (
                    queries.get_analyst_call_count_today(
                        config.db_path, config.analyst_fallback_provider
                    )
                    if config.analyst_fallback_provider
                    else config.analyst_daily_limit
                )
                if primary_count >= config.analyst_daily_limit and fallback_count >= config.analyst_daily_limit:
                    logger.warning(
                        "Daily analyst quota reached for all providers, skipping analysis for %s",
                        ticker,
                    )
                    continue

                analysis = await asyncio.to_thread(
                    analyze_etf_ticker, ticker, headlines, tech_data,
                    expense_ratio, config, client, fallback_client,
                    macro_context=macro_context
                )
                queries.increment_analyst_call_count(
                    config.db_path, analysis["provider_used"]
                )
                try:
                    queries.set_cached_analysis(
                        config.db_path, ticker, headline_hash,
                        analysis["signal"], analysis["reasoning"],
                        confidence=analysis.get("confidence"),
                    )
                except Exception as cache_exc:
                    logger.warning("Failed to write analyst cache for %s: %s", ticker, cache_exc)

            # ETF uses BUY signal check but no technical filter (no fundamental filter per ETF-02)
            if analysis["signal"] != "BUY":
                continue

            rec_id = queries.create_recommendation(
                db_path=config.db_path,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data["price"] or 0.0,
                dividend_yield=None,
                pe_ratio=None,
                asset_type="etf",
                confidence=analysis.get("confidence"),
            )

            message_id = await bot.send_etf_recommendation(
                rec_id=rec_id,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data.get("price"),
                rsi=tech_data.get("rsi"),
                ma50=tech_data.get("ma50"),
                expense_ratio=expense_ratio,
                etf_max_expense_ratio=config.etf_max_expense_ratio,
                confidence=analysis.get("confidence"),
            )
            queries.set_discord_message_id(config.db_path, rec_id, message_id)
            logger.info("ETF recommended %s", ticker)
            recommendations_posted += 1

        except sqlite3.OperationalError as exc:
            logger.error("ETF scan aborted — DB schema error: %s", exc)
            await bot.send_ops_alert(f"ETF scan aborted — DB schema error: {exc}")
            return
        except Exception as exc:
            logger.error("Error processing ETF %s: %s", ticker, exc)
            error_count += 1
            if errors_posted < 3:
                await bot.send_ops_alert(f"[ERROR] {ticker}: {type(exc).__name__}")
                errors_posted += 1
            continue

    if error_count > 3:
        overflow = error_count - 3
        await bot.send_ops_alert(f"[{overflow} more errors not shown \u2014 check logs]")

    if recommendations_posted == 0:
        logger.warning("ETF scan complete: 0 recommendations posted.")
        await bot.send_ops_alert("[ETF] ETF scan complete: 0 recommendations posted.")
    else:
        logger.info("ETF scan complete. %d recommendation(s) posted.", recommendations_posted)


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
    bot._scan_etf_callback = lambda: run_scan_etf(bot, config)
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
        configure_scheduler(
            scheduler,
            config,
            lambda: asyncio.run_coroutine_threadsafe(
                run_scan_etf(bot, config), bot.loop
            ).result(),
            times=config.etf_scan_times,
            job_id_prefix="etf_scan",
        )
        scheduler.start()
        logger.info(
            "Scheduler started — daily scan at %02d:%02d",
            config.scan_hour, config.scan_minute,
        )
        logger.info(
            "ETF scheduler started — daily ETF scan at %02d:%02d",
            config.etf_scan_hour, config.etf_scan_minute,
        )

    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
