from __future__ import annotations
import asyncio
import math
import logging

import discord
from discord import app_commands
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import queries
from discord_bot.embeds import build_recommendation_embed, build_positions_embed
from schwab_client.orders import place_order

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

    def __init__(self, rec_id: int, ticker: str, price: float, config: Config):
        super().__init__(timeout=None)
        self.rec_id = rec_id
        self.ticker = ticker
        self.price = price
        self.config = config

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
        existing_positions = queries.get_open_positions(self.config.db_path)
        existing_total = sum(
            p["shares"] * (p["last_price"] if p["last_price"] is not None else p["avg_cost_usd"])
            for p in existing_positions
        )
        if existing_total + new_exposure > self.config.max_position_size_usd:
            await interaction.response.send_message(
                f"Blocked: buying {shares} share(s) of {self.ticker} at ${self.price:.2f} "
                f"(${new_exposure:.0f}) would exceed MAX_POSITION_SIZE_USD "
                f"(${self.config.max_position_size_usd:.0f}, current exposure: ${existing_total:.0f}).",
                ephemeral=True,
            )
            return

        order_id = None
        if not self.config.dry_run:
            order_id = place_order(self.ticker, shares, self.config)

        queries.create_trade(
            db_path=self.config.db_path,
            recommendation_id=self.rec_id,
            ticker=self.ticker,
            shares=shares,
            price=self.price,
            order_id=order_id,
        )
        # POS-02: track position
        queries.upsert_position(self.config.db_path, self.ticker, shares, self.price)
        queries.update_recommendation_status(self.config.db_path, self.rec_id, "approved")

        label = f"[DRY RUN] " if self.config.dry_run else ""
        await interaction.response.send_message(
            f"{label}Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}.",
        )
        self.stop()

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        queries.update_recommendation_status(self.config.db_path, self.rec_id, "rejected")
        await interaction.response.send_message(f"Rejected {self.ticker}.")
        self.stop()


class TradingBot(discord.Client):
    """Discord client that posts stock recommendations and handles Approve/Reject button interactions."""

    def __init__(self, config: Config):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self._scan_callback = None  # Set by main.py after construction

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
        await self.tree.sync()

    async def _scan_command(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message("Scan triggered — results incoming...")
        except Exception:
            pass  # Interaction may have expired; still run the scan
        if self._scan_callback is not None:
            asyncio.create_task(self._scan_callback())

    async def _positions_command(self, interaction: discord.Interaction):
        """Handle /positions slash command: show open holdings with P&L."""
        from screener.positions import get_position_summary
        summaries = await asyncio.to_thread(get_position_summary, self.config.db_path)
        if not summaries:
            await interaction.response.send_message("No open positions.")
            return
        embed = build_positions_embed(summaries)
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
    ) -> str:
        """Fetch the configured channel, post a recommendation embed with Approve/Reject buttons, and return the message id as a string."""
        channel = await self.fetch_channel(self.config.discord_channel_id)
        embed = build_recommendation_embed(ticker, signal, reasoning, price, dividend_yield, pe_ratio)
        view = ApproveRejectView(rec_id, ticker, price, self.config)
        msg = await _send_message(channel, embed, view)
        return str(msg.id)

    async def send_ops_alert(self, message: str) -> None:
        """Send a plain-text operational alert to the configured Discord channel."""
        try:
            channel = await self.fetch_channel(self.config.discord_channel_id)
            await channel.send(f"[OPS ALERT] {message}")
        except Exception as exc:
            logger.error("Failed to send ops alert: %s", exc)
