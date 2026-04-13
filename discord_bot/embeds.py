import discord

_SIGNAL_COLORS = {
    "BUY": discord.Color.green(),
    "HOLD": discord.Color.yellow(),
    "SKIP": discord.Color.red(),
    "SELL": discord.Color.red(),
}


def build_recommendation_embed(
    ticker: str,
    signal: str,
    reasoning: str,
    price: float,
    dividend_yield: float | None,
    pe_ratio: float | None,
    confidence: str | None = None,
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
    if confidence is not None:
        embed.add_field(name="Confidence", value=confidence.capitalize(), inline=True)
    return embed


def build_etf_recommendation_embed(
    ticker: str,
    signal: str,
    reasoning: str,
    price: float | None,
    rsi: float | None,
    ma50: float | None,
    expense_ratio: float | None,
    etf_max_expense_ratio: float | None = None,
    confidence: str | None = None,
) -> discord.Embed:
    """Build a Discord embed for an ETF BUY/HOLD/SKIP recommendation with technical and expense ratio fields (per ETF-04), with optional expense ratio flag per Phase 12 D-06/D-07."""
    if signal not in _SIGNAL_COLORS:
        raise ValueError(f"Invalid signal '{signal}': must be BUY, HOLD, or SKIP")

    embed = discord.Embed(
        title=f"{ticker} — {signal} [ETF]",
        description=reasoning,
        color=_SIGNAL_COLORS[signal],
    )
    embed.add_field(
        name="Price",
        value=f"${price:.2f}" if price is not None else "N/A",
        inline=True,
    )
    embed.add_field(
        name="RSI",
        value=f"{rsi:.1f}" if rsi is not None else "N/A",
        inline=True,
    )
    if price is not None and ma50 is not None:
        trend = "Above MA50" if price >= ma50 else "Below MA50"
    else:
        trend = "N/A"
    embed.add_field(name="MA50 Trend", value=trend, inline=True)
    if expense_ratio is None:
        expense_value = "N/A"
    elif etf_max_expense_ratio is not None and expense_ratio > etf_max_expense_ratio:
        expense_value = f"⚠️ {expense_ratio:.4f} (High)"
    else:
        expense_value = f"{expense_ratio:.4f}"
    embed.add_field(name="Expense Ratio", value=expense_value, inline=True)
    if confidence is not None:
        embed.add_field(name="Confidence", value=confidence.capitalize(), inline=True)
    return embed


def build_sell_embed(
    ticker: str,
    reasoning: str,
    entry_price: float,
    current_price: float,
    pnl_pct: float,
    shares: float,
    rsi: float,
    confidence: str | None = None,
) -> discord.Embed:
    """Build a Discord embed for a SELL recommendation with position P&L context."""
    embed = discord.Embed(
        title=f"{ticker} — SELL",
        description=reasoning,
        color=discord.Color.red(),
    )
    embed.add_field(name="Entry Price", value=f"${entry_price:.2f}", inline=True)
    embed.add_field(name="Current Price", value=f"${current_price:.2f}", inline=True)
    embed.add_field(name="P&L", value=f"{pnl_pct:+.1%}", inline=True)
    embed.add_field(name="Shares", value=f"{shares}", inline=True)
    embed.add_field(name="RSI", value=f"{rsi:.1f}", inline=True)
    if confidence is not None:
        embed.add_field(name="Confidence", value=confidence.capitalize(), inline=True)
    return embed


def build_positions_embed(summaries: list[dict]) -> discord.Embed:
    """Build a Discord embed showing open positions with P&L.

    Each position gets an inline field with ticker, shares, avg cost, current price, and P&L%.
    Shows 'No open positions.' if summaries is empty. Truncates at 25 fields (Discord limit).
    Footer aggregates total unrealized P&L when pnl_usd values are available (PORT-01).
    """
    embed = discord.Embed(title="Open Positions", color=discord.Color.blurple())
    for s in summaries[:25]:
        pnl_str = f"{s['pnl_pct']:.1%}" if s['pnl_pct'] is not None else "N/A"
        price_str = f"${s['current_price']:.2f}" if s['current_price'] else "N/A"
        embed.add_field(
            name=s["ticker"],
            value=(
                f"Shares: {s['shares']}\n"
                f"Avg Cost: ${s['avg_cost_usd']:.2f}\n"
                f"Current: {price_str}\n"
                f"P&L: {pnl_str}"
            ),
            inline=True,
        )
    if not summaries:
        embed.description = "No open positions."
    else:
        # Footer: aggregate P&L from positions that have pnl_usd
        valid = [s for s in summaries if s.get("pnl_usd") is not None]
        if valid:
            total_pnl_usd = sum(s["pnl_usd"] for s in valid)
            total_cost = sum(s["avg_cost_usd"] * s["shares"] for s in valid)
            agg_pct = total_pnl_usd / total_cost if total_cost else 0.0
            sign = "+" if total_pnl_usd >= 0 else ""
            footer_text = f"Total unrealized: {sign}${total_pnl_usd:.2f} ({agg_pct:+.1%})"
            if len(valid) < len(summaries):
                footer_text += " (partial)"
            embed.set_footer(text=footer_text)
        # If no valid positions, omit footer entirely
    return embed


def build_stats_embed(stats: dict) -> discord.Embed:
    """Build a Trade Statistics embed from get_trade_stats() result dict (PORT-02).

    Args:
        stats: dict with keys total, wins, losses, win_rate, avg_gain_pct, avg_loss_pct
    """
    embed = discord.Embed(title="Trade Statistics", color=discord.Color.blurple())
    embed.description = (
        f"Closed Trades: {stats['total']}   "
        f"Wins: {stats['wins']}   "
        f"Losses: {stats['losses']}"
    )
    embed.add_field(name="Win Rate", value=f"{stats['win_rate']:.1%}", inline=True)
    gain_pct = stats["avg_gain_pct"]
    loss_pct = stats["avg_loss_pct"]
    embed.add_field(
        name="Avg Gain",
        value=f"+{gain_pct:.1%}" if gain_pct is not None else "N/A",
        inline=True,
    )
    embed.add_field(
        name="Avg Loss",
        value=f"{loss_pct:.1%}" if loss_pct is not None else "N/A",
        inline=True,
    )
    return embed
