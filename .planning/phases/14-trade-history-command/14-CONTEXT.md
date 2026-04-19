# Phase 14: Trade History Command - Context

**Gathered:** 2026-04-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Add a `/history` Discord slash command that queries the last 20 closed trades and displays them as a
code block table in a Discord embed. Scope is limited to a new DB query function, a new embed builder
in `embeds.py`, and a new slash command handler in `bot.py`. No changes to the scan pipeline, trade
execution, or existing commands.

</domain>

<decisions>
## Implementation Decisions

### Embed Layout
- **D-01:** Display the 20 rows as a monospaced code block table in the embed `description` field (not as Discord inline fields). Compact, scannable, stays within Discord's 4096-char description limit for 20 rows.
- **D-02:** Column order: `Ticker  Entry    Exit     P&L%    Date`
- **D-03:** Date format: ISO `YYYY-MM-DD` (e.g., `2026-04-01`). Unambiguous and sortable.
- **D-04:** Empty state: respond with the plain string `"No closed trades yet."` (not an embed). Matches SC-3 exactly.

### Query Filter
- **D-05:** Query filter: `WHERE side = 'sell' AND cost_basis IS NOT NULL ORDER BY executed_at DESC LIMIT 20`. Excludes pre-v1.2 rows with `NULL cost_basis` — consistent with `/stats` behavior and keeps the table clean (no partial rows).
- **D-06:** No JOIN — sell-side-only SELECT per SC-4. P&L% computed in Python from `(price - cost_basis) / cost_basis`.

### Claude's Discretion
- Embed title and color (suggest "Trade History" title; blurple matches the existing `/stats` and `/positions` color convention)
- Footer content (e.g., "Last 20 of N total" using a COUNT query, or omit footer)
- Exact column width padding in the code block table
- Whether to add a `get_closed_trades` helper in `queries.py` (consistent with existing pattern) or inline the query

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Files to add or modify
- `discord_bot/bot.py` — add `_history_command` handler; register in `setup_hook` (see existing `/stats` handler at line 231 as the template)
- `discord_bot/embeds.py` — add `build_history_embed(trades: list[dict]) -> discord.Embed`
- `database/queries.py` — add `get_closed_trades(db_path, limit=20) -> list[dict]` returning rows with `ticker`, `price` (exit), `cost_basis` (entry), `executed_at`

### Reference implementations (read for patterns)
- `discord_bot/bot.py` lines 231–241 — `/stats` command: asyncio.to_thread + embed builder + send_message pattern
- `discord_bot/embeds.py` lines 157–180 — `build_stats_embed`: embed with description + fields pattern
- `database/queries.py` lines 86–107 — `get_trade_stats`: `WHERE side = 'sell' AND cost_basis IS NOT NULL` filter (same filter applies to `/history`)

### Requirements
- `OPS-02` in `.planning/REQUIREMENTS.md` — defines the `/history` requirement

### Success criteria (locked — do not renegotiate)
- SC-1: `/history` shows up to last 20 closed trades
- SC-2: each row shows ticker, entry price, exit price, P&L%, sell date
- SC-3: empty state responds with "No closed trades yet." (plain string, not embed)
- SC-4: sell-side-only SELECT, no JOIN

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `asyncio.to_thread(fn, db_path)` — every slash command DB call uses this pattern; `/history` follows the same
- `discord.Embed(title=..., color=discord.Color.blurple())` — established color for informational commands
- `build_stats_embed` / `build_positions_embed` in `embeds.py` — embed builder pattern to follow

### Established Patterns
- DB query functions in `queries.py` return plain dicts (not ORM objects); embed builders accept `list[dict]`
- Slash command handlers import DB functions locally (inside the method) to avoid circular imports
- `interaction.response.send_message(embed=embed)` for embed responses; plain string for empty state

### Integration Points
- `bot.py:setup_hook` — registers all slash commands; `/history` needs to be added here alongside `/scan`, `/positions`, `/stats`
- `database/queries.py` — new `get_closed_trades` function drops in alongside `get_trade_stats`

</code_context>

<specifics>
## Specific Ideas

- The code block table should use fixed-width columns with space padding so columns align in Discord's monospace font
- P&L% sign: prefix `+` for gains, `-` for losses (same convention as `/stats` avg gain display)

</specifics>

<deferred>
## Deferred Ideas

- `/history` filtering by ticker or date range — noted in REQUIREMENTS.md Future Requirements
- `/history` pagination for large trade histories — noted in REQUIREMENTS.md Future Requirements
- Show pre-v1.2 rows with "N/A" — excluded; NULL cost_basis rows silently skipped consistent with `/stats`

</deferred>

---

*Phase: 14-trade-history-command*
*Context gathered: 2026-04-19*
