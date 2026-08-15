from __future__ import annotations
import asyncio
import math
import logging

import discord
from discord import app_commands
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import queries
from database.models import get_cursor
from risk import kill_switch
from risk.kill_switch import TradingHalted
from discord_bot.embeds import build_recommendation_embed, build_positions_embed, build_sell_embed, build_etf_recommendation_embed, build_stats_embed, build_history_embed
from schwab_client.orders import place_limit_order, place_order, place_sell_order

logger = logging.getLogger(__name__)

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


# The ops-alert CRUD takes a connection rather than a db_path, so that a caller
# can one day enqueue an alert inside the same transaction as the state change
# that warranted it. The bot has no such need, so these adapt it to the
# db_path-taking shape every other DB call here dispatches via to_thread.

def _enqueue_alert(db_path: str, message: str) -> int:
    with get_cursor(db_path) as conn:
        return queries.enqueue_ops_alert(conn, message)


def _mark_alert_delivered(db_path: str, alert_id: int) -> None:
    with get_cursor(db_path) as conn:
        queries.mark_ops_alert_delivered(conn, alert_id)


def _record_alert_failure(db_path: str, alert_id: int, error: str) -> None:
    with get_cursor(db_path) as conn:
        queries.record_ops_alert_failure(conn, alert_id, error)


def _pending_alerts(db_path: str, limit: int) -> list[dict]:
    with get_cursor(db_path) as conn:
        return queries.get_undelivered_ops_alerts(conn, limit=limit)


@_retry
async def _send_message(channel, embed, view):
    return await channel.send(embed=embed, view=view)


def is_authorized(config, user_id: int) -> bool:
    """Whether this Discord user may operate the kill switch.

    Empty allowlist authorizes nobody. An unparseable entry is skipped rather
    than treated as a wildcard — a typo must narrow access, never widen it.

    Both /halt AND /resume are guarded. The design's first version allowlisted
    only /halt, which protects the wrong direction: it left anyone able to
    clear a halt in the middle of an incident.
    """
    allowed = set()
    for part in str(getattr(config, "ops_user_ids", "") or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            allowed.add(int(part))
        except ValueError:
            logger.warning("Ignoring malformed OPS_USER_IDS entry %r", part)
    return user_id in allowed


def compute_share_quantity(price: float, max_position_usd: float) -> int:
    """Return how many whole shares can be bought without exceeding max_position_usd."""
    if price <= 0:
        return 0
    return math.floor(max_position_usd / price)


class ApproveRejectView(discord.ui.View):
    """Discord UI view with Approve and Reject buttons for a pending buy recommendation."""

    def __init__(self, rec_id: int, ticker: str, price: float, config: Config, scan_time: str | None = None):
        super().__init__(timeout=None)
        self.rec_id = rec_id
        self.ticker = ticker
        self.price = price
        self.config = config
        self.scan_time = scan_time
        # Deterministic custom_ids (keyed by rec_id) make these buttons survive a bot
        # restart: the ids baked into the sent message match the ids on the view
        # re-registered at startup (see build_view_for_recommendation + setup_hook).
        # Without this, decorator buttons get a fresh random id each run, so clicks on
        # a pre-restart message route nowhere ("interaction failed").
        self.approve.custom_id = f"approve:{rec_id}"
        self.reject.custom_id = f"reject:{rec_id}"

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        shares = compute_share_quantity(self.price, self.config.max_position_size_usd)
        if shares == 0:
            await interaction.response.send_message(
                f"Cannot buy {self.ticker}: price ${self.price:.2f} exceeds max position size.",
                ephemeral=True,
            )
            return

        # POS-05: exposure guard — block if total portfolio exposure would exceed limit
        new_exposure = shares * self.price
        existing_positions = await asyncio.to_thread(
            queries.get_open_positions, self.config.db_path
        )
        existing_total = sum(
            p["shares"] * (p["last_price"] if p["last_price"] is not None else p["avg_cost_usd"])
            for p in existing_positions
        )
        if existing_total + new_exposure > self.config.max_portfolio_usd:
            await interaction.response.send_message(
                f"Blocked: buying {shares} share(s) of {self.ticker} at ${self.price:.2f} "
                f"(${new_exposure:.0f}) would exceed MAX_PORTFOLIO_USD "
                f"(${self.config.max_portfolio_usd:.0f}, current exposure: ${existing_total:.0f}).",
                ephemeral=True,
            )
            return

        # Idempotency gate: exactly one click wins the pending -> approved transition.
        claimed = await asyncio.to_thread(
            queries.claim_recommendation, self.config.db_path, self.rec_id, "approved"
        )
        if not claimed:
            await interaction.response.send_message(
                f"This recommendation for {self.ticker} was already handled.",
                ephemeral=True,
            )
            return

        # Acknowledge within Discord's 3s interaction window before the Schwab call,
        # which is synchronous HTTP with retries and must run off the event loop.
        await interaction.response.defer()

        order_id = None
        limit_price_val = None      # D-06: dry-run records market defaults regardless of use_limit_buy
        order_type_val = "market"   # D-06: see above
        try:
            if not self.config.dry_run:
                # The gate spans the final kill-switch read through the broker
                # dispatch. Without it /halt could land in one of the await
                # boundaries below: the worker reads ENABLED, /halt persists
                # HALTED and replies "halted", and the worker submits anyway.
                # /halt acquires the same gate, so it returns only once nothing
                # is in flight.
                async with kill_switch.submission_gate():
                    kill_switch.require_enabled(self.config)
                    if self.config.use_limit_buy:
                        order_id = await asyncio.to_thread(
                            place_limit_order, self.ticker, shares, self.price, self.config
                        )
                        limit_price_val = self.price
                        order_type_val = "limit"
                    else:
                        order_id = await asyncio.to_thread(
                            place_order, self.ticker, shares, self.config
                        )
        except TradingHalted as exc:
            # Nothing was dispatched, so re-open the recommendation and say so
            # plainly. The generic handler's "verify in Schwab" would send the
            # operator looking for an order that was never sent.
            await asyncio.to_thread(
                queries.update_recommendation_status, self.config.db_path, self.rec_id, "pending"
            )
            logger.warning("Buy blocked for %s: %s", self.ticker, exc)
            await interaction.followup.send(
                f"Buy for {self.ticker} blocked: trading is halted. No order was "
                f"sent. ({exc}) The recommendation stays open — approve again "
                "after /resume."
            )
            return
        except Exception as exc:
            # Release the claim so the button can be retried after the failure.
            await asyncio.to_thread(
                queries.update_recommendation_status, self.config.db_path, self.rec_id, "pending"
            )
            logger.error("Buy order failed for %s: %s", self.ticker, exc)
            await interaction.followup.send(
                f"Order placement failed for {self.ticker}: {exc} — recommendation "
                "re-opened. Verify in Schwab before retrying."
            )
            return

        # WARNING (RISK-05 / Phase 17): GTC limit orders are recorded as positions immediately
        # on broker acknowledgement, not on fill. If the limit does not fill, has_open_position()
        # will block re-buys and the sell pass may attempt to sell non-existent shares.
        # Fill reconciliation is deferred to a future phase.
        await asyncio.to_thread(
            queries.create_trade,
            db_path=self.config.db_path,
            recommendation_id=self.rec_id,
            ticker=self.ticker,
            shares=shares,
            price=self.price,
            order_id=order_id,
            limit_price=limit_price_val,
            order_type=order_type_val,
        )
        # POS-02: track position
        await asyncio.to_thread(
            queries.upsert_position, self.config.db_path, self.ticker, shares, self.price
        )

        elapsed = f" (scan at {self.scan_time})" if self.scan_time else ""
        if self.config.dry_run:
            msg = f"[DRY RUN] Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}{elapsed}."
        elif self.config.use_limit_buy:
            msg = f"Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f} (limit, GTC{elapsed})."
        else:
            msg = f"Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}{elapsed}."
        await interaction.followup.send(msg)
        self.stop()

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        claimed = await asyncio.to_thread(
            queries.claim_recommendation, self.config.db_path, self.rec_id, "rejected"
        )
        if not claimed:
            await interaction.response.send_message(
                f"This recommendation for {self.ticker} was already handled.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(f"Rejected {self.ticker}.")
        self.stop()


class SellApproveRejectView(discord.ui.View):
    """Discord UI view with Approve and Reject buttons for a pending sell recommendation."""

    def __init__(self, rec_id: int, ticker: str, shares: float, current_price: float, config: Config):
        super().__init__(timeout=None)
        self.rec_id = rec_id
        self.ticker = ticker
        self.shares = int(shares)  # Schwab expects int
        self.current_price = current_price
        self.config = config
        # Deterministic custom_ids (see ApproveRejectView) so a sell view re-registered
        # after restart matches the buttons on the original message.
        self.approve.custom_id = f"sell_approve:{rec_id}"
        self.reject.custom_id = f"sell_reject:{rec_id}"

    @discord.ui.button(label="Approve Sell", style=discord.ButtonStyle.danger, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Idempotency guard: reject duplicate/concurrent presses if position already closed (WR-01)
        has_position = await asyncio.to_thread(
            queries.has_open_position, self.config.db_path, self.ticker
        )
        if not has_position:
            await interaction.response.send_message(
                f"Position for {self.ticker} is already closed.", ephemeral=True
            )
            return

        # Idempotency gate: exactly one click wins the pending -> approved transition.
        claimed = await asyncio.to_thread(
            queries.claim_recommendation, self.config.db_path, self.rec_id, "approved"
        )
        if not claimed:
            await interaction.response.send_message(
                f"This sell recommendation for {self.ticker} was already handled.",
                ephemeral=True,
            )
            return

        # Acknowledge within Discord's 3s interaction window before the Schwab call,
        # which is synchronous HTTP with retries and must run off the event loop.
        await interaction.response.defer()

        order_id = None
        try:
            if not self.config.dry_run:
                # Same gate as the buy path — one switch halts both directions.
                async with kill_switch.submission_gate():
                    kill_switch.require_enabled(self.config)
                    order_id = await asyncio.to_thread(
                        place_sell_order, self.ticker, self.shares, self.config
                    )
        except TradingHalted as exc:
            await asyncio.to_thread(
                queries.update_recommendation_status, self.config.db_path, self.rec_id, "pending"
            )
            logger.warning("Sell blocked for %s: %s", self.ticker, exc)
            await interaction.followup.send(
                f"Sell for {self.ticker} blocked: trading is halted. No order was "
                f"sent. ({exc}) The recommendation stays open — approve again "
                "after /resume."
            )
            return
        except Exception as exc:
            # Release the claim so the button can be retried after the failure.
            await asyncio.to_thread(
                queries.update_recommendation_status, self.config.db_path, self.rec_id, "pending"
            )
            logger.error("Sell order failed for %s: %s", self.ticker, exc)
            await interaction.followup.send(
                f"Sell order placement failed for {self.ticker}: {exc} — recommendation "
                "re-opened. Verify in Schwab before retrying."
            )
            return

        # Fetch cost_basis from open position before recording trade (PORT-02 / T-13-02)
        cost_basis = None
        open_positions = await asyncio.to_thread(
            queries.get_open_positions, self.config.db_path
        )
        for pos in open_positions:
            if pos["ticker"] == self.ticker:
                cost_basis = pos["avg_cost_usd"]
                break

        await asyncio.to_thread(
            queries.create_trade,
            db_path=self.config.db_path,
            recommendation_id=self.rec_id,
            ticker=self.ticker,
            shares=self.shares,
            price=self.current_price,
            order_id=order_id,
            side="sell",
            cost_basis=cost_basis,
        )
        await asyncio.to_thread(queries.close_position, self.config.db_path, self.ticker)

        label = "[DRY RUN] " if self.config.dry_run else ""
        await interaction.followup.send(
            f"{label}Approved: selling {self.shares} share(s) of {self.ticker} at ${self.current_price:.2f}.",
        )
        self.stop()

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.secondary, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        claimed = await asyncio.to_thread(
            queries.claim_recommendation, self.config.db_path, self.rec_id, "rejected"
        )
        if not claimed:
            await interaction.response.send_message(
                f"This sell recommendation for {self.ticker} was already handled.",
                ephemeral=True,
            )
            return
        await asyncio.to_thread(queries.set_sell_blocked, self.config.db_path, self.ticker)
        await interaction.response.send_message(
            f"Rejected sell for {self.ticker}. Position sell-blocked until RSI drops below threshold.",
        )
        self.stop()


def build_view_for_recommendation(rec, config: Config) -> discord.ui.View:
    """Reconstruct the persistent view that matches a stored recommendation row.

    SELL rows → SellApproveRejectView (shares read from the open position, or 0.0 if
    none — the approve handler's has_open_position guard makes that safe). BUY/ETF rows
    → ApproveRejectView. Used at startup to re-register views so Approve/Reject buttons
    posted before a restart keep working.
    """
    if rec["signal"] == "SELL":
        shares = 0.0
        for pos in queries.get_open_positions(config.db_path):
            if pos["ticker"] == rec["ticker"]:
                shares = pos["shares"]
                break
        return SellApproveRejectView(rec["id"], rec["ticker"], shares, rec["price"], config)
    return ApproveRejectView(rec["id"], rec["ticker"], rec["price"], config)


class TradingBot(discord.Client):
    """Discord client that posts stock recommendations and handles Approve/Reject button interactions."""

    def __init__(self, config: Config):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self._scan_callback = None  # Set by main.py after construction
        self._scan_etf_callback = None  # Set by main.py after construction
        self._reconcile_callback = None  # Set by main.py after construction
        self._cached_channel = None  # Memoized by _resolve_channel on first send

    async def setup_hook(self):
        """Register and sync the /scan and /positions slash commands on bot startup."""
        self.tree.add_command(
            app_commands.Command(
                name="scan",
                description="Trigger an immediate stock scan",
                callback=self._scan_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="positions",
                description="Show current open positions and estimated P&L",
                callback=self._positions_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="scan_etf",
                description="Trigger an immediate ETF scan",
                callback=self._scan_etf_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="stats",
                description="Show win rate and P&L stats for closed trades",
                callback=self._stats_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="history",
                description="Show the last 20 closed trades",
                callback=self._history_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="reconcile",
                description="Compare bot positions against the Schwab account",
                callback=self._reconcile_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="halt",
                description="Stop all new order submissions (durable, all processes)",
                callback=self._halt_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="resume",
                description="Re-enable order submissions after a halt",
                callback=self._resume_command,
            )
        )
        await self.tree.sync()
        self._register_persistent_views()

    def _register_persistent_views(self) -> None:
        """Re-register persistent Approve/Reject views for still-pending recommendations.

        Called on startup so buttons posted before a restart keep routing to working
        handlers. discord.py matches incoming clicks to these views by the deterministic
        custom_ids set in each view's __init__.
        """
        try:
            pending = queries.get_pending_recommendations(self.config.db_path)
        except Exception as exc:
            logger.warning("Could not load pending recommendations for view restore: %s", exc)
            return
        restored = 0
        for rec in pending:
            message_id = rec["discord_message_id"]
            if not message_id:
                continue
            try:
                view = build_view_for_recommendation(rec, self.config)
                self.add_view(view, message_id=int(message_id))
                restored += 1
            except Exception as exc:
                logger.warning("Failed to restore view for recommendation %s: %s", rec["id"], exc)
        if restored:
            logger.info("Restored %d persistent recommendation view(s) after startup", restored)

    async def _scan_command(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message("Scan triggered — results incoming...")
        except Exception:
            pass  # Interaction may have expired; still run the scan
        if self._scan_callback is not None:
            asyncio.create_task(self._scan_callback())

    async def _scan_etf_command(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message("ETF scan triggered — results incoming...")
        except Exception:
            pass
        if self._scan_etf_callback is not None:
            asyncio.create_task(self._scan_etf_callback())

    async def _reconcile_command(self, interaction: discord.Interaction):
        """Handle /reconcile: compare DB positions against Schwab and report the result."""
        if self._reconcile_callback is None:
            await interaction.response.send_message("Reconciliation is not configured.")
            return
        # Defer: the Schwab account fetch can exceed Discord's 3s interaction window.
        await interaction.response.defer()
        result = await self._reconcile_callback()
        await interaction.followup.send(result)

    async def _positions_command(self, interaction: discord.Interaction):
        """Handle /positions slash command: show open holdings with P&L."""
        from screener.positions import get_position_summary
        summaries = await asyncio.to_thread(get_position_summary, self.config.db_path)
        if not summaries:
            await interaction.response.send_message("No open positions.")
            return
        embed = build_positions_embed(summaries)
        await interaction.response.send_message(embed=embed)

    async def _stats_command(self, interaction: discord.Interaction):
        """Handle /stats slash command: show win rate and P&L stats for closed trades."""
        from database.queries import get_trade_stats
        stats = await asyncio.to_thread(get_trade_stats, self.config.db_path)
        if stats is None:
            await interaction.response.send_message(
                "No closed trades yet — nothing to analyze."
            )
            return
        embed = build_stats_embed(stats)
        await interaction.response.send_message(embed=embed)

    async def _history_command(self, interaction: discord.Interaction):
        """Handle /history slash command: show last 20 closed trades as a code-block table."""
        from database.queries import get_closed_trades
        trades = await asyncio.to_thread(get_closed_trades, self.config.db_path)
        if not trades:
            await interaction.response.send_message("No closed trades yet.")
            return
        embed = build_history_embed(trades)
        await interaction.response.send_message(embed=embed)

    async def _resolve_channel(self):
        """Return the configured channel, fetching it from the API once and caching it.

        The send_* methods post many messages per scan and each previously called
        fetch_channel (an API round-trip). The channel object is stable for the
        bot's lifetime, so memoize the first fetch and reuse it (review item 5).
        getattr-with-default tolerates instances built via __new__ in tests.
        """
        if getattr(self, "_cached_channel", None) is None:
            self._cached_channel = await self.fetch_channel(self.config.discord_channel_id)
        return self._cached_channel

    async def send_recommendation(
        self,
        rec_id: int,
        ticker: str,
        signal: str,
        reasoning: str,
        price: float,
        dividend_yield: float | None,
        pe_ratio: float | None,
        confidence: str | None = None,
        earnings_date: str | None = None,   # NEW — Phase 16 SIG-05
        scan_time: str | None = None,       # NEW — Phase 17 RISK-04
    ) -> str:
        """Fetch the configured channel, post a recommendation embed with Approve/Reject buttons, and return the message id as a string."""
        channel = await self._resolve_channel()
        embed = build_recommendation_embed(ticker, signal, reasoning, price, dividend_yield, pe_ratio, confidence=confidence, earnings_date=earnings_date, scan_time=scan_time)
        view = ApproveRejectView(rec_id, ticker, price, self.config, scan_time=scan_time)
        msg = await _send_message(channel, embed, view)
        return str(msg.id)

    async def send_sell_recommendation(
        self,
        rec_id: int,
        ticker: str,
        reasoning: str,
        entry_price: float,
        current_price: float,
        pnl_pct: float,
        shares: float,
        rsi: float,
        confidence: str | None = None,
    ) -> str:
        """Post a sell recommendation embed with Approve/Reject buttons and return the message id."""
        channel = await self._resolve_channel()
        embed = build_sell_embed(ticker, reasoning, entry_price, current_price, pnl_pct, shares, rsi, confidence=confidence)
        view = SellApproveRejectView(rec_id, ticker, shares, current_price, self.config)
        msg = await _send_message(channel, embed, view)
        return str(msg.id)

    async def send_etf_recommendation(
        self,
        rec_id: int,
        ticker: str,
        signal: str,
        reasoning: str,
        price: float | None,
        rsi: float | None,
        ma50: float | None,
        expense_ratio: float | None,
        etf_max_expense_ratio: float | None = None,
        confidence: str | None = None,
    ) -> str:
        """Post an ETF recommendation embed with Approve/Reject buttons and return the message id."""
        channel = await self._resolve_channel()
        embed = build_etf_recommendation_embed(
            ticker,
            signal,
            reasoning,
            price,
            rsi,
            ma50,
            expense_ratio,
            etf_max_expense_ratio=etf_max_expense_ratio,
            confidence=confidence,
        )
        view = ApproveRejectView(rec_id, ticker, price or 0.0, self.config)
        msg = await _send_message(channel, embed, view)
        return str(msg.id)

    async def _halt_command(self, interaction, reason: str = "no reason given") -> None:
        """/halt — stop new order submissions, durably and across processes.

        Order of operations matters. The halt is PERSISTED FIRST, then the gate
        is awaited. Doing it the other way round would leave trading enabled
        for as long as an in-flight broker call takes, which is exactly the
        window an operator is trying to close. Persisting first means even a
        /halt still queued behind a slow submission has already stopped the
        next one.

        Awaiting the gate afterwards is what lets the reply be truthful: it
        returns only once no submission is mid-flight.
        """
        if not is_authorized(self.config, interaction.user.id):
            logger.warning("Unauthorized /halt from user %s", interaction.user.id)
            await interaction.response.send_message(
                "You are not authorized to halt trading.", ephemeral=True
            )
            return

        actor = f"discord:{interaction.user.id}"
        await asyncio.to_thread(
            kill_switch.halt, self.config.db_path, actor, reason
        )
        await interaction.response.send_message(
            f"Trading HALTED by <@{interaction.user.id}> ({reason}). "
            "Waiting for any in-flight submission to finish..."
        )

        async with kill_switch.submission_gate():
            pass  # returns once nothing is mid-flight

        await interaction.followup.send(
            "Halt complete: no submission is in flight. Note this cannot recall "
            "orders the broker has **already** accepted — check open orders in "
            "Schwab for anything outstanding. /resume re-enables trading."
        )

    async def _resume_command(self, interaction, reason: str = "no reason given") -> None:
        """/resume — re-enable submissions.

        Guarded by the same allowlist as /halt. A switch anyone can clear
        protects nothing.
        """
        if not is_authorized(self.config, interaction.user.id):
            logger.warning("Unauthorized /resume from user %s", interaction.user.id)
            await interaction.response.send_message(
                "You are not authorized to resume trading.", ephemeral=True
            )
            return

        actor = f"discord:{interaction.user.id}"
        await asyncio.to_thread(
            kill_switch.resume, self.config.db_path, actor, reason
        )
        await interaction.response.send_message(
            f"Trading RESUMED by <@{interaction.user.id}> ({reason})."
        )

    async def _deliver_ops_alert(self, alert_id: int, message: str) -> bool:
        """Attempt delivery of one already-persisted alert. True if it landed.

        Failure is recorded against the row instead of being swallowed, so the
        alert stays in the outbox for drain_ops_alerts to retry.
        """
        try:
            channel = await self._resolve_channel()
            await channel.send(f"[OPS ALERT] {message}")
        except Exception as exc:
            logger.error("Failed to send ops alert: %s", exc)
            await asyncio.to_thread(
                _record_alert_failure,
                self.config.db_path,
                alert_id,
                f"{type(exc).__name__}: {exc}",
            )
            return False
        await asyncio.to_thread(
            _mark_alert_delivered, self.config.db_path, alert_id
        )
        return True

    async def send_ops_alert(self, message: str) -> None:
        """Persist an operational alert, then try to deliver it to Discord.

        The persist-then-send ordering is the safety property. Several states
        are only safe because an operator finds out about them — stuck orders,
        unresolved submissions, reconciliation failures — and this method used
        to catch every delivery error and merely log it, which made a Discord
        outage indistinguishable from a quiet system. Now a failed send leaves
        a durable row behind.

        Still never raises: run_scan posts alerts from inside its per-ticker
        loop, so a raising alert would abort the scan it was reporting on.
        """
        alert_id = await asyncio.to_thread(
            _enqueue_alert, self.config.db_path, message
        )
        await self._deliver_ops_alert(alert_id, message)

    async def drain_ops_alerts(self, limit: int = 20) -> int:
        """Retry undelivered alerts, oldest first. Returns how many landed.

        Stops at the first alert that still fails: Discord being unreachable is
        not a per-alert condition, so continuing would burn attempts on the
        whole backlog and deliver it out of order once it recovered.
        """
        pending = await asyncio.to_thread(
            _pending_alerts, self.config.db_path, limit
        )
        delivered = 0
        for row in pending:
            if not await self._deliver_ops_alert(row["id"], row["message"]):
                break
            delivered += 1
        return delivered
