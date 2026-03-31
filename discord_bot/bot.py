from __future__ import annotations
import asyncio
import math
import logging

import discord
from discord import app_commands
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import queries
from discord_bot.embeds import build_recommendation_embed
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
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self._scan_callback = None  # Set by main.py after construction

    async def setup_hook(self):
        self.tree.add_command(
            app_commands.Command(
                name="scan",
                description="Trigger an immediate stock scan",
                callback=self._scan_command,
            )
        )
        await self.tree.sync()

    async def _scan_command(self, interaction: discord.Interaction):
        await interaction.response.send_message("Scan triggered — results incoming...")
        if self._scan_callback is not None:
            asyncio.create_task(self._scan_callback())

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
