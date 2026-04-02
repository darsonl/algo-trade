import discord

_SIGNAL_COLORS = {
    "BUY": discord.Color.green(),
    "HOLD": discord.Color.yellow(),
    "SKIP": discord.Color.red(),
}


def build_recommendation_embed(
    ticker: str,
    signal: str,
    reasoning: str,
    price: float,
    dividend_yield: float | None,
    pe_ratio: float | None,
) -> discord.Embed:
    """Build a Discord embed for a BUY/HOLD/SKIP recommendation with price and fundamental fields."""
    if signal not in _SIGNAL_COLORS:
        raise ValueError(f"Invalid signal '{signal}': must be BUY, HOLD, or SKIP")

    embed = discord.Embed(
        title=f"{ticker} — {signal}",
        description=reasoning,
        color=_SIGNAL_COLORS[signal],
    )
    embed.add_field(name="Price", value=f"${price:.2f}", inline=True)
    embed.add_field(
        name="Dividend Yield",
        value=f"{dividend_yield:.2%}" if dividend_yield is not None else "N/A",
        inline=True,
    )
    embed.add_field(
        name="P/E Ratio",
        value=f"{pe_ratio:.1f}" if pe_ratio is not None else "N/A",
        inline=True,
    )
    return embed
