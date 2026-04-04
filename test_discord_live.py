"""
One-shot live Discord test.

Connects to Discord, posts a fake BUY recommendation embed with Approve/Reject
buttons, prints the message URL, then exits cleanly.

Usage:
    python test_discord_live.py
"""
import asyncio
import discord
from config import Config
from discord_bot.bot import TradingBot
from discord_bot.embeds import build_recommendation_embed


async def main():
    config = Config()

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"[live test] Logged in as {client.user}")

        try:
            channel = await client.fetch_channel(config.discord_channel_id)
        except discord.NotFound:
            print(f"[live test] ERROR: channel {config.discord_channel_id} not found — "
                  "check DISCORD_CHANNEL_ID and that the bot has access.")
            await client.close()
            return

        # Build a fake recommendation
        embed = build_recommendation_embed(
            ticker="AAPL",
            signal="BUY",
            reasoning="[LIVE TEST] Strong fundamentals, positive momentum, oversold RSI.",
            price=175.50,
            dividend_yield=0.006,
            pe_ratio=24.5,
        )

        # Import the view directly so buttons render
        from discord_bot.bot import ApproveRejectView
        view = ApproveRejectView(rec_id=0, ticker="AAPL", price=175.50, config=config)

        msg = await channel.send(content="**[LIVE TEST — ignore this message]**", embed=embed, view=view)
        print(f"[live test] Posted successfully → message ID {msg.id}")
        print(f"[live test] URL: https://discord.com/channels/{msg.guild.id}/{channel.id}/{msg.id}")

        await client.close()

    await client.start(config.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
