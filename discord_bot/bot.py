from __future__ import annotations
import asyncio
import math
import logging

import discord
from discord import app_commands
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import queries
from discord_bot.embeds import build_recommendation_embed, build_positions_embed, build_sell_embed, build_etf_recommendation_embed, build_stats_embed, build_history_embed
from schwab_client.orders import place_limit_order, place_order, place_sell_order

logger = logging.getLogger(__name__)

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


@_retry
async def _send_message(channel, embed, view):
    return await channel.send(embed=embed, view=view)


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
                order_id = await asyncio.to_thread(
                    place_sell_order, self.ticker, self.shares, self.config
                )
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
        channel = await self.fetch_channel(self.config.discord_channel_id)
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
        channel = await self.fetch_channel(self.config.discord_channel_id)
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
        channel = await self.fetch_channel(self.config.discord_channel_id)
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

    async def send_ops_alert(self, message: str) -> None:
        """Send a plain-text operational alert to the configured Discord channel."""
        try:
            channel = await self.fetch_channel(self.config.discord_channel_id)
            await channel.send(f"[OPS ALERT] {message}")
        except Exception as exc:
            logger.error("Failed to send ops alert: %s", exc)
