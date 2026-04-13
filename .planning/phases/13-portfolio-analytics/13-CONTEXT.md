# Phase 13: Portfolio Analytics — Context

**Gathered:** 2026-04-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Enhance `/positions` with an aggregate unrealized P&L footer (PORT-01), and add a new `/stats` slash command showing win rate, average gain %, and average loss % on closed trades (PORT-02).

Requirements in scope: PORT-01, PORT-02.
Out of scope: individual trade history `/history` command (deferred to v1.3), per-sector exposure caps, confidence-based filtering, any changes to scan logic.
</domain>

<decisions>
## Implementation Decisions

### PORT-01: /positions Footer

- **D-01:** Footer format: `"Total unrealized: +$142.30 (+8.2%)"` — dollar amount AND aggregate percentage. Aggregate % = total_pnl_usd / total_cost_basis. Both numbers computed from position summaries.
- **D-02:** `get_position_summary()` in `screener/positions.py` must return a new `pnl_usd` key per position (`(current_price - avg_cost_usd) * shares`). The embed builder sums `pnl_usd` and total cost basis across positions to derive the footer values.
- **D-03:** When some (but not all) positions have unavailable prices, footer shows the partial total with a `(partial)` suffix: `"Total unrealized: +$142.30 (+8.2%) (partial)"`. Positions with `current_price=None` are excluded from the sum. If ALL prices are unavailable, omit the footer entirely.
- **D-04:** Footer is set via `embed.set_footer(text="Total unrealized: ...")`. No new embed field.

### PORT-02: /stats Command

- **D-05:** A "closed trade" = a `trades` row with `side='sell'` and `cost_basis IS NOT NULL`. The `cost_basis` column is the P&L anchor — see D-06.
- **D-06:** Add `cost_basis REAL` column to the `trades` table via `ALTER TABLE` try/except migration (matching the established migration pattern in `initialize_db`). Default NULL for existing rows — backward-compatible.
- **D-07:** `cost_basis` is populated at sell-approval time in `SellApproveRejectView.approve` (`discord_bot/bot.py`). Before calling `create_trade(..., side='sell')`, fetch the position's `avg_cost_usd` from DB and pass it as `cost_basis`. The `create_trade` function gains a new optional `cost_basis: float | None = None` parameter.
- **D-08:** Per-trade gain % = `(sell_price - cost_basis) / cost_basis`. A trade is a **win** if `sell_price > cost_basis`, a **loss** if `sell_price < cost_basis`. Break-even (equal) counts as a win for simplicity.
- **D-09:** `/stats` query logic lives in a new `get_trade_stats(db_path) -> dict | None` function in `database/queries.py`. Returns `{total, wins, losses, win_rate, avg_gain_pct, avg_loss_pct}` or `None` when no qualifying rows exist. SQL: pure DB aggregation — no external API call (satisfies PORT-02 success criterion 4).
- **D-10:** Old sell trades (pre-migration, `cost_basis IS NULL`) are silently excluded from stats. No fallback reconstruction.

### PORT-02: /stats Embed

- **D-11:** `/stats` response is a Discord embed with:
  - Title: `"Trade Statistics"`
  - Color: `discord.Color.blurple()` (matching `/positions`)
  - Row 1 (3 inline fields): `Win Rate` / `Avg Gain` / `Avg Loss`
    - Win Rate: `"58.3%"` (percentage, 1 decimal)
    - Avg Gain: `"+4.2%"` (prefixed with `+`, 1 decimal)
    - Avg Loss: `"-2.1%"` (prefixed with `-`, 1 decimal)
  - Row 2 (single non-inline field or description): `"Closed Trades: 12   Wins: 7   Losses: 5"`
- **D-12:** Empty state: plain text message `"No closed trades yet — nothing to analyze."` (not an embed). Matches the `/positions` empty state pattern (`"No open positions."`).
- **D-13:** `/stats` is registered as a slash command in `TradingBot._register_commands()` alongside `/scan`, `/scan_etf`, `/positions`. Command description: `"Show win rate and P&L stats for closed trades"`.

### Claude's Discretion

- `get_position_summary()` extension: add `pnl_usd` key. If `current_price` or `avg_cost_usd` is None, set `pnl_usd=None` (same guard as existing `pnl_pct`).
- Footer computation: sum `pnl_usd` and `avg_cost_usd * shares` only for positions where `pnl_usd is not None`. Track whether any positions were excluded to append `(partial)`.
- Test coverage: unit tests for `get_trade_stats()` (win/loss/mixed/empty cases), `build_positions_embed()` footer (normal/partial/all-None), and `/stats` embed builder.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` §PORT-01 — unrealized P&L footer acceptance criteria
- `.planning/REQUIREMENTS.md` §PORT-02 — /stats command acceptance criteria

### Key source files
- `screener/positions.py` — `get_position_summary()` (add `pnl_usd` key)
- `discord_bot/embeds.py` — `build_positions_embed()` (add footer), new `build_stats_embed()` function
- `discord_bot/bot.py` — `SellApproveRejectView.approve` (populate `cost_basis`), `_register_commands()` (add /stats), new `_stats_command` handler
- `database/queries.py` — `create_trade()` (add `cost_basis` param), new `get_trade_stats()` function
- `database/models.py` — `initialize_db()` migration (add `cost_basis` ALTER TABLE block)

### Prior context
- `.planning/phases/11-confidence-scoring/11-CONTEXT.md` — D-04/D-05 show the ALTER TABLE try/except migration pattern used here
- `.planning/phases/12-etf-polish/12-CONTEXT.md` — D-07 shows how embed parameters are extended with optional kwargs; same pattern for `build_positions_embed` footer

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `get_position_summary()` at `screener/positions.py:7` — extend with `pnl_usd` key; no structural change
- `build_positions_embed()` at `discord_bot/embeds.py:118` — add `embed.set_footer(text=...)` call after the field loop
- `create_trade()` at `database/queries.py:62` — add `cost_basis: float | None = None` kwarg, extend INSERT
- `SellApproveRejectView.approve` at `discord_bot/bot.py:113` — fetch `avg_cost_usd` from `get_open_positions()` before calling `create_trade`

### Established Patterns
- Slash command registration: `_register_commands()` in `bot.py` — follow the `/positions` pattern exactly
- Empty state: plain `interaction.response.send_message("No ...")` string (not embed) — matches `/positions` empty path
- DB migration: try/except ALTER TABLE block in `initialize_db()` — mandatory pattern
- Embed color: `discord.Color.blurple()` for informational commands

### Integration Points
- `bot.py:SellApproveRejectView.approve` — the only place sell trades are recorded; `cost_basis` must be fetched here
- `discord_bot/bot.py:_register_commands` — add `/stats` command registration
- `database/models.py:initialize_db` — add `cost_basis` migration block after existing migrations

</code_context>

<specifics>
## Specific Details

- Footer text: `"Total unrealized: +$142.30 (+8.2%)"` or with partial: `"Total unrealized: +$142.30 (+8.2%) (partial)"`
- Stats embed Row 2 separator: use `"Closed Trades: 12   Wins: 7   Losses: 5"` as embed description or a non-inline field
- Win rate definition: wins / total (break-even = sell_price >= cost_basis counts as win)
- Avg Gain shows `+` prefix; Avg Loss shows `-` prefix naturally from negative value formatted as `f"{val:.1%}"`

</specifics>

<deferred>
## Deferred Ideas

- `/history` command (individual closed trades with entry/exit/hold duration) — deferred to v1.3 per REQUIREMENTS.md
- Per-sector exposure stats — deferred
- Fallback reconstruction for pre-migration sell trades (NULL cost_basis) — excluded from stats with known limitation

</deferred>

---

*Phase: 13-portfolio-analytics*
*Context gathered: 2026-04-13*
