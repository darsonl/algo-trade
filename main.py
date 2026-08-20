from __future__ import annotations
import asyncio
import hashlib
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import yfinance as yf

from config import Config
from database.models import get_cursor, initialize_db
from risk import kill_switch
from database import queries
from screener.universe import get_watchlist, get_top_sp500_by_fundamentals, get_universe, partition_watchlist
from screener.fundamentals import passes_fundamental_filter, fetch_fundamental_info, fetch_eps_data, normalize_dividend_yield
from screener.technicals import passes_technical_filter, fetch_technical_data
from analyst.news import fetch_news_headlines
from analyst.claude_analyst import analyze_ticker, create_analyst_client, create_fallback_client, create_fallback2_client, analyze_sell_ticker, analyze_etf_ticker
from screener.macro import fetch_macro_context
from screener.exit_signals import check_exit_signals
from database.order_accounting import DEFINITIVELY_UNFILLED_STATUSES, OPEN_ORDER_STATUSES
from risk.resolution import alert_stuck_orders
from schwab_client.order_payload import extract_fills, map_broker_status
from schwab_client.orders import fetch_order, get_positions
from schwab_client.reconcile import diff_positions, format_reconciliation_report
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

    Times are interpreted in config.scan_timezone when set (e.g.
    "America/New_York" keeps the schedule market-aligned across DST);
    otherwise machine-local time, the historical behavior.
    """
    scan_times = times if times is not None else config.scan_times
    tz = config.scan_timezone or None
    for i, time_str in enumerate(scan_times):
        hour, minute = map(int, time_str.split(":"))
        scheduler.add_job(
            job_fn,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=f"{job_id_prefix}_{i}",
            replace_existing=True,
        )


def compute_headline_hash(headlines: list[str]) -> str:
    """SHA-256 cache key over sorted headlines.

    An empty headline list is salted with today's date: a broken or empty news
    feed would otherwise produce one constant hash per ticker, pinning a single
    analyst_cache entry forever. The salt bounds that staleness to one day.
    """
    if headlines:
        content = "\n".join(sorted(headlines))
    else:
        content = f"NO_HEADLINES:{date.today().isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()


def all_providers_exhausted(config: Config) -> bool:
    """Return True only when every configured analyst provider is at/over its daily quota.

    An unconfigured fallback slot counts as exhausted (treated as at-limit) so the
    result reflects only providers that could actually serve a call today (D-11).
    """
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
    fallback2_count = (
        queries.get_analyst_call_count_today(
            config.db_path, config.analyst_fallback2_provider
        )
        if config.analyst_fallback2_provider
        else config.analyst_daily_limit
    )
    return (
        primary_count >= config.analyst_daily_limit
        and fallback_count >= config.analyst_daily_limit
        and fallback2_count >= config.analyst_daily_limit
    )


# ---------------------------------------------------------------------------
# Position reconciliation (RISK-05)
# ---------------------------------------------------------------------------

def _untrustworthy_fill(status: str, payload: dict, shares: float, notional: float) -> str | None:
    """Why this payload's fills must not be booked, or None if they may be.

    Three cases, and only the third is safe to trust blindly:

    * a terminal status with no `filledQuantity` at all -- it says the order is
      done but not how much of it happened;
    * a quantity with no execution prices to value it at -- `extract_fills`
      reports (n, 0.0) here deliberately and says the caller must decide, and
      booking $0 for ten real shares releases the whole reservation;
    * a definitively refused order (rejected / submit_failed), where the broker
      turned it down outright, so no capital moved and the zero is real.
    """
    if status in DEFINITIVELY_UNFILLED_STATUSES:
        return None
    if "filledQuantity" not in payload:
        return "the payload reports no filledQuantity"
    if shares > 0 and notional <= 0:
        return f"{shares:g} shares reported with no execution prices to value them"
    return None


def _apply_broker_status(config: Config, order: dict, payload: dict) -> int | None:
    """Write one order's broker verdict to the ledger. Returns a rec_id to retire.

    Split out of the sweep so the DB work is one short-lived connection per
    order and `complete_recommendation` -- which opens its own -- is never
    called while this one holds the write lock.
    """
    order_id = order["id"]
    update = map_broker_status(payload)

    with get_cursor(config.db_path) as conn:
        if update.successor_id is not None:
            # Follow the pointer the broker gave us. adopt_replacement closes
            # the predecessor at its ACTUAL fills and inserts the successor with
            # its OWN quantity and limit, so a 5@$100 replaced by 10@$150 stops
            # reserving $500 against $1,500 of live exposure.
            queries.adopt_replacement(conn, order_id, payload)
            return None

        if update.status == "submit_unknown":
            queries.mark_order_submit_unknown(conn, order_id, update.reason or "")
            return None

        if not update.terminal:
            return None

        # `fills_observed` gates the release of capital, so the fills must be
        # recorded in the same breath as the terminal status. A terminal row
        # with an unverified zero fill releases the whole budget (finding 6) --
        # which is why the flag is only flipped for fills we can actually trust.
        filled_shares, filled_notional = extract_fills(payload)
        unusable = _untrustworthy_fill(update.status, payload, filled_shares, filled_notional)
        if unusable:
            logger.warning(
                "Sweep: order %s is %s but %s; keeping its full commitment",
                order_id, update.status, unusable,
            )
            queries.mark_order_terminal_unobserved(
                conn, order_id, update.status, f"terminal, fills unusable: {unusable}"
            )
        else:
            queries.observe_fills(
                conn, order_id, filled_shares, filled_notional, update.status
            )

    return order["recommendation_id"]


async def sweep_terminal_recommendations(config: Config) -> int:
    """Retire recommendations whose orders the broker says are done (§11).

    This is the release valve for the `approved`-covering partial unique index.
    Without it the first buy of any ticker blocks that ticker forever: the claim
    flips the row to `approved`, and nothing ever moves it off.

    Runs at the start of each scan. Every failure is per-order and non-fatal --
    a sweep that aborts on the first broker outage leaves every later ticker
    blocked for reasons that have nothing to do with those tickers, and an
    outage must never be read as "the order is gone".

    Skipped in DRY_RUN: simulated orders have no broker counterpart to ask about.
    """
    if config.dry_run:
        return 0

    with get_cursor(config.db_path) as conn:
        open_orders = queries.get_orders_by_status(conn, OPEN_ORDER_STATUSES)

    completed = 0
    for order in open_orders:
        broker_order_id = order["broker_order_id"]
        if not broker_order_id:
            # A pending_submit row has no id to ask about, and calling the
            # broker with a NULL id is a request for somebody else's order.
            continue

        try:
            payload = await asyncio.to_thread(fetch_order, config, broker_order_id)
        except Exception as exc:
            logger.warning(
                "Sweep: could not read broker order %s for %s (%s); leaving it open",
                broker_order_id, order["ticker"], exc,
            )
            continue

        try:
            rec_id = await asyncio.to_thread(_apply_broker_status, config, order, payload)
        except Exception:
            logger.exception(
                "Sweep: could not apply broker status for order %s", order["id"]
            )
            continue

        if rec_id is None:
            continue

        try:
            if await asyncio.to_thread(
                queries.complete_recommendation, config.db_path, rec_id, payload["status"]
            ):
                completed += 1
                logger.info(
                    "Sweep: recommendation %s (%s) completed — broker says %s",
                    rec_id, order["ticker"], payload["status"],
                )
        except Exception:
            logger.exception("Sweep: could not complete recommendation %s", rec_id)

    return completed


async def run_reconciliation(bot: TradingBot, config: Config, alert_on_discrepancy: bool = True) -> str:
    """Compare DB open positions against the Schwab account and report drift.

    Report-only: never mutates positions — correcting real-money state is a
    human decision, consistent with the approval flow for trades. Returns a
    human-readable summary string. When discrepancies are found, posts them as
    an ops alert unless alert_on_discrepancy=False (the /reconcile command
    passes False because it displays the returned summary itself).
    """
    if config.dry_run:
        msg = "Reconciliation skipped: DRY_RUN mode (positions are simulated, broker comparison is meaningless)."
        logger.info(msg)
        return msg

    try:
        broker_positions = await asyncio.to_thread(get_positions, config)
    except Exception as exc:
        msg = f"Reconciliation failed: could not fetch Schwab positions ({exc})."
        logger.warning(msg)
        # This is the RISK-05 safety monitor. A failure to RUN is not the same as
        # a clean result, and staying quiet about it manufactures confidence:
        # get_positions raised on every call for months and nothing surfaced it.
        # Gated on alert_on_discrepancy so /reconcile, which renders this string
        # itself, does not also post it.
        if alert_on_discrepancy:
            await bot.send_ops_alert(msg)
        return msg

    db_rows = await asyncio.to_thread(queries.get_open_positions, config.db_path)
    diff = diff_positions([dict(r) for r in db_rows], broker_positions)
    report = format_reconciliation_report(diff)

    if report is None:
        msg = f"Reconciliation clean: {len(db_rows)} open position(s) match Schwab."
        logger.info(msg)
        return msg

    logger.warning("Position reconciliation discrepancies:\n%s", report)
    if alert_on_discrepancy:
        await bot.send_ops_alert(report)
    return report


# ---------------------------------------------------------------------------
# Scan pipeline
# ---------------------------------------------------------------------------

async def analyze_with_cache(
    config: Config,
    ticker: str,
    headlines: list[str],
    analyze_fn,
) -> dict | None:
    """Shared analyst-cache + quota path for the buy and ETF scans.

    Returns the analysis dict — from analyst_cache on a hit, or by awaiting
    analyze_fn() on a miss (its result is then written to the cache). Returns
    None when every provider is at its daily quota, signalling the caller to
    skip this ticker (`if analysis is None: continue`).

    analyze_fn is an async zero-arg callable that builds the pass-specific
    prompt and runs the analyzer. It runs ONLY on a cache miss, so any expensive
    enrichment inside it (e.g. the buy pass's fetch_eps_data) is skipped on a hit.
    """
    headline_hash = compute_headline_hash(headlines)
    cached = queries.get_cached_analysis(config.db_path, ticker, headline_hash)
    if cached:
        logger.debug("Cache hit for %s (hash %s...)", ticker, headline_hash[:8])
        return cached

    # D-11: quota guard — skip if all providers exhausted
    if all_providers_exhausted(config):
        logger.warning(
            "Daily analyst quota reached for all providers, skipping analysis for %s",
            ticker,
        )
        return None

    analysis = await analyze_fn()
    # Load-bearing: None is reserved above as the "quota-exhausted, skip" signal, so a
    # real analysis must be a dict. analyze_ticker/analyze_etf_ticker always return one
    # (or raise) — this guards against a future analyzer silently reading as "skip".
    assert analysis is not None, "analyze_fn must return an analysis dict, not None"
    try:
        queries.set_cached_analysis(
            config.db_path, ticker, headline_hash,
            analysis["signal"], analysis["reasoning"],
            confidence=analysis.get("confidence"),
        )
    except Exception as cache_exc:
        logger.warning("Failed to write analyst cache for %s: %s", ticker, cache_exc)
    return analysis


async def _drain_ops_outbox(bot: TradingBot) -> None:
    """Retry ops alerts stranded by an earlier Discord outage.

    A scan is the natural retry point: it is the recurring beat of the system,
    and an alert about the previous scan is exactly what an outage would have
    eaten. Failures are contained — a broken outbox must not abort the scan it
    would be reporting on.
    """
    try:
        redelivered = await bot.drain_ops_alerts()
        if redelivered:
            logger.info("Redelivered %d backlogged ops alert(s)", redelivered)
    except Exception as exc:
        logger.error("Ops-alert outbox drain failed: %s", exc)


async def run_scan(bot: TradingBot, config: Config) -> None:
    """Run the full screening pipeline and post qualifying tickers to Discord."""
    logger.info("Starting scan...")
    await _drain_ops_outbox(bot)
    # Repeated on every scan: guard 11 blocks this ticker until a human
    # runs /resolve, and an alert nobody repeats is a block nobody sees.
    await alert_stuck_orders(bot, config)
    # Before anything is screened, not after: a ticker whose order the broker
    # has finished with should be eligible in THIS scan, not the next one.
    try:
        await sweep_terminal_recommendations(config)
    except Exception:
        # Reporting and housekeeping must never abort the scan they run inside.
        logger.exception("Terminal-order sweep failed; continuing the scan")
    queries.expire_stale_recommendations(config.db_path)

    watchlist_path = str(Path(__file__).parent / "watchlist.txt")
    try:
        sp500 = await asyncio.to_thread(get_top_sp500_by_fundamentals, config)  # P8-audit: already wrapped (hotfix ae66e64)
    except Exception as exc:
        logger.warning("Could not fetch top S&P 500: %s — using watchlist only", exc)
        sp500 = []

    universe = get_universe(watchlist_path, extra_tickers=sp500)
    # Filter ETFs out of stock scan universe. partition_watchlist already fetches each
    # ticker's .info (the heaviest yfinance call) to read quoteType; capture it in
    # info_by_ticker so the loop below can reuse it instead of fetching .info twice.
    info_by_ticker: dict = {}
    try:
        stocks_only, _etfs = await asyncio.to_thread(partition_watchlist, universe, info_by_ticker)  # P8-audit: already wrapped (Phase 7)
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
    fallback2_client = create_fallback2_client(config)

    def on_attempt(provider: str) -> None:
        # Count every provider attempt against today's quota — calls that reach
        # a provider and then fail burn quota exactly like successes.
        queries.increment_analyst_call_count(config.db_path, provider)

    recommendations_posted = 0
    error_count = 0
    errors_posted = 0
    headline_fetches = 0
    empty_headline_fetches = 0
    scan_time = datetime.now().strftime("%H:%M")

    for ticker in universe:
        if queries.ticker_recommended_today(config.db_path, ticker):
            continue
        if queries.has_open_position(config.db_path, ticker):
            logger.debug("Skipping %s: open position exists", ticker)
            continue
        if queries.has_active_recommendation(config.db_path, ticker):
            # Not the same question as ticker_recommended_today, which is
            # session-scoped: an `approved` row left by an ambiguous submission
            # days ago is invisible to that guard but blocks this insert.
            logger.debug("Skipping %s: an active recommendation already exists", ticker)
            continue

        try:
            yf_ticker = yf.Ticker(ticker)
            # Reuse the .info already fetched by partition_watchlist; fetch only on a miss
            # (e.g. the ticker hit the allowlist fallback and was never fetched).
            info = info_by_ticker.get(ticker)
            if info is None:
                info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
            if not passes_fundamental_filter(info, config):
                continue

            # Phase 16 (SIG-05, SIG-06): earnings date from info dict — zero extra HTTP call (D-09).
            _ts = info.get("earningsTimestamp")
            if _ts is None:
                earnings_date_embed = "N/A"
                earnings_date_prompt = None
            else:
                _earnings_dt = datetime.fromtimestamp(_ts, tz=timezone.utc).date()
                _today = date.today()
                if _earnings_dt < _today:
                    # Past earnings — suppress to N/A per D-02
                    earnings_date_embed = "N/A"
                    earnings_date_prompt = None
                else:
                    _days_until = (_earnings_dt - _today).days
                    _date_str = _earnings_dt.strftime("%b %d, %Y")
                    if 0 <= _days_until < 7:
                        # Within 7 days — warning prefix in embed (D-06), proximity note in prompt (D-08)
                        earnings_date_embed = f"⚠️ {_date_str}"
                        earnings_date_prompt = f"{_date_str} (in {_days_until} days — proximity risk)"
                    else:
                        earnings_date_embed = _date_str
                        earnings_date_prompt = _date_str

            headlines = await asyncio.to_thread(
                fetch_news_headlines, ticker, alpha_vantage_api_key=config.alpha_vantage_api_key
            )
            headline_fetches += 1
            if not headlines:
                empty_headline_fetches += 1

            async def _analyze_buy():
                # Runs only on a cache miss (analyze_with_cache calls this closure only
                # then): the cached path never uses fundamental_trend, and fetch_eps_data
                # (quarterly_income_stmt) is a slow network call we shouldn't pay for on a
                # hit (perf, review item 5). Per D-07/D-08.
                trailing_pe = info.get("trailingPE")
                forward_pe = info.get("forwardPE")
                if trailing_pe is None or forward_pe is None or trailing_pe <= 0:
                    pe_direction = "N/A"  # D-03: graceful N/A on missing forwardPE or zero/negative trailingPE
                elif abs(forward_pe - trailing_pe) / abs(trailing_pe) < 0.05:
                    pe_direction = "stable"  # D-01: ±5% stable band
                elif forward_pe < trailing_pe:
                    pe_direction = "expanding"   # D-02: earnings growing → multiple contracting
                else:
                    pe_direction = "contracting"  # D-02: earnings shrinking → multiple expanding

                try:
                    eps_trend = await asyncio.to_thread(fetch_eps_data, yf_ticker)
                except Exception as exc:
                    logger.warning(
                        "EPS data fetch failed for %s: %s — continuing without EPS trend",
                        ticker, exc,
                    )
                    eps_trend = None

                fundamental_trend = {
                    "pe_direction": pe_direction,
                    "eps_trend": eps_trend,
                }
                logger.debug("fundamental_trend for %s: pe_direction=%s, eps_quarters=%s",
                             ticker, pe_direction, len(eps_trend) if eps_trend else 0)

                return await asyncio.to_thread(
                    analyze_ticker, ticker, info, headlines, config,
                    client, fallback_client, macro_context=macro_context,
                    fundamental_trend=fundamental_trend,  # Phase 15 SIG-07, SIG-08
                    earnings_date=earnings_date_prompt,   # Phase 16 SIG-06
                    fallback2_client=fallback2_client,
                    on_attempt=on_attempt,
                )

            analysis = await analyze_with_cache(config, ticker, headlines, _analyze_buy)
            if analysis is None:
                continue  # all providers quota-exhausted

            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
            if not should_recommend(analysis["signal"], tech_data, config):
                continue

            div_yield = normalize_dividend_yield(info.get("dividendYield"))

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
                earnings_date=earnings_date_embed,   # NEW — Phase 16 SIG-05
                scan_time=scan_time,                 # NEW — Phase 17 RISK-04
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

    # Health check: every headline fetch coming back empty across a real scan means the
    # news pipeline is broken (e.g. a yfinance schema change), not that there is no news.
    if headline_fetches >= 3 and empty_headline_fetches == headline_fetches:
        logger.warning(
            "All %d headline fetches returned 0 headlines \u2014 news pipeline may be broken.",
            headline_fetches,
        )
        await bot.send_ops_alert(
            f"All {headline_fetches} headline fetches returned 0 headlines \u2014 "
            "news pipeline may be broken (yfinance schema change or Alpha Vantage outage)."
        )

    if recommendations_posted == 0:
        logger.warning("Scan complete: 0 recommendations posted.")
        await bot.send_ops_alert("Scan complete: 0 recommendations posted.")
    else:
        logger.info("Scan complete. %d recommendation(s) posted.", recommendations_posted)

    # --- Position reconciliation (RISK-05): surface DB/broker drift before the
    # sell pass acts on positions that may not exist at the broker ---
    try:
        await run_reconciliation(bot, config)
    except Exception as exc:
        logger.warning("Reconciliation error: %s — continuing with sell pass", exc)

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
            if all_providers_exhausted(config):
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
                fallback2_client=fallback2_client,
                on_attempt=on_attempt,
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
    await _drain_ops_outbox(bot)
    # Repeated on every scan: guard 11 blocks this ticker until a human
    # runs /resolve, and an alert nobody repeats is a block nobody sees.
    await alert_stuck_orders(bot, config)
    queries.expire_stale_recommendations(config.db_path)

    etf_watchlist_path = str(Path(__file__).parent / "etf_watchlist.txt")
    etf_tickers = get_watchlist(etf_watchlist_path)

    # D-08 / ASYNC-03: wrap partition_watchlist in asyncio.to_thread.
    # Capture .info per ticker so the loop reuses it instead of re-fetching for expense ratio.
    info_by_ticker: dict = {}
    _stocks, etfs = await asyncio.to_thread(partition_watchlist, etf_tickers, info_by_ticker)  # P8-audit: already wrapped (Phase 7)
    logger.info("ETF universe: %d tickers", len(etfs))

    # Fetch macro context once for all ETFs (D-02)
    try:
        macro_context = await asyncio.to_thread(fetch_macro_context)
    except Exception as exc:
        logger.warning("Macro context fetch failed: %s — continuing without macro", exc)
        macro_context = {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}

    client = create_analyst_client(config)
    fallback_client = create_fallback_client(config)
    fallback2_client = create_fallback2_client(config)

    def on_attempt(provider: str) -> None:
        # Count every provider attempt against today's quota (see run_scan).
        queries.increment_analyst_call_count(config.db_path, provider)

    recommendations_posted = 0
    error_count = 0
    errors_posted = 0

    for ticker in etfs:
        if queries.ticker_recommended_today(config.db_path, ticker):
            continue
        if queries.has_open_position(config.db_path, ticker):
            logger.debug("Skipping %s: open position exists", ticker)
            continue
        if queries.has_active_recommendation(config.db_path, ticker):
            # Not the same question as ticker_recommended_today, which is
            # session-scoped: an `approved` row left by an ambiguous submission
            # days ago is invisible to that guard but blocks this insert.
            logger.debug("Skipping %s: an active recommendation already exists", ticker)
            continue

        try:
            yf_ticker = yf.Ticker(ticker)

            # Fetch technical data (no fundamental filter for ETFs)
            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)

            # Fetch expense ratio from yfinance info (reuse partition_watchlist's .info; fetch on miss)
            info = info_by_ticker.get(ticker)
            if info is None:
                info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
            expense_ratio = info.get("netExpenseRatio")
            if expense_ratio is None:
                logger.debug("Expense ratio unavailable for %s", ticker)

            # Fetch news headlines (per D-01)
            headlines = await asyncio.to_thread(
                fetch_news_headlines, ticker, alpha_vantage_api_key=config.alpha_vantage_api_key
            )

            async def _analyze_etf():
                return await asyncio.to_thread(
                    analyze_etf_ticker, ticker, headlines, tech_data,
                    expense_ratio, config, client, fallback_client,
                    macro_context=macro_context,
                    fallback2_client=fallback2_client,
                    on_attempt=on_attempt,
                )

            # Shared analyst-cache + quota path (same helper as the run_scan buy pass)
            analysis = await analyze_with_cache(config, ticker, headlines, _analyze_etf)
            if analysis is None:
                continue  # all providers quota-exhausted

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

    # Seeds only a database that has never been written; a persisted halt wins
    # over TRADING_ENABLED, so a restart cannot quietly re-arm the bot.
    state = kill_switch.init(config.db_path, config.trading_enabled)
    logger.info("Kill switch state at startup: %s", state)

    bot = TradingBot(config)
    bot._scan_callback = lambda: run_scan(bot, config)
    bot._scan_etf_callback = lambda: run_scan_etf(bot, config)
    bot._reconcile_callback = lambda: run_reconciliation(bot, config, alert_on_discrepancy=False)
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
