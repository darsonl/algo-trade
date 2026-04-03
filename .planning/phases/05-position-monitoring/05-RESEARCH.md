# Phase 5: Position Monitoring - Research

**Researched:** 2026-04-03
**Domain:** SQLite schema extension, Discord slash commands, yfinance price fetch, exposure guard
**Confidence:** HIGH (all findings from direct codebase inspection)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| POS-01 | positions table added to DB schema (id, ticker, shares, avg_cost_usd, entry_date, status: open/closed) | Schema DDL and migration pattern established below |
| POS-02 | On trade approval, a position record is created or existing position is updated (avg cost) | Inject into ApproveRejectView.approve after create_trade call |
| POS-03 | get_open_positions query returns all open positions with current P&L (uses last known price) | last_price column + yfinance refresh in screener/positions.py |
| POS-04 | /positions Discord slash command displays open holdings table | setup_hook pattern and tree.sync() already established in bot.py |
| POS-05 | Before approving a buy, check if position size + existing exposure exceeds MAX_POSITION_SIZE_USD | Inject exposure check before place_order / create_trade in approve handler |
| POS-06 | Daily scan skips BUY analysis for tickers where a position already exists (prevents stacking) | Single-line guard in run_scan loop after ticker_recommended_today check |
</phase_requirements>

---

## Summary

Phase 5 builds entirely on top of patterns already established in the codebase. No new libraries or external dependencies are required. The work is three interconnected layers: (1) a new `positions` table in SQLite following the exact DDL and migration patterns from `database/models.py`, (2) four new CRUD functions in `database/queries.py` following the `get_connection` open/close pattern, and (3) three integration points — the approve button handler, the `run_scan` loop, and a new `/positions` slash command.

The most critical design decision for Phase 5 is the **weighted average cost update** on re-buy. The formula `new_avg = (old_shares * old_avg + new_shares * new_price) / (old_shares + new_shares)` must be implemented atomically in a single UPDATE statement to avoid race conditions. This is the only non-trivial business logic in the phase.

The `/positions` slash command follows the same `app_commands.Command` registration path already used by `/scan`. The P&L calculation is a one-liner using yfinance's `fast_info.last_price`. The exposure check is a simple comparison against `config.max_position_size_usd` computed before the trade is recorded.

**Primary recommendation:** Implement in four sequential tasks — (1) DB schema + queries, (2) approve handler integration + exposure check, (3) run_scan skip guard, (4) /positions command + embed.

---

## What Exists That Phase 5 Builds On

### DB Layer (HIGH confidence — direct file inspection)

`database/models.py` establishes these invariants:
- `get_connection(db_path)` opens a connection with `row_factory = sqlite3.Row` and WAL mode. Every query function opens and closes its own connection (no shared connection state). Phase 5 MUST follow this pattern.
- `initialize_db` uses a single `executescript()` call containing all `CREATE TABLE IF NOT EXISTS` DDL, followed by `ALTER TABLE ... ADD COLUMN` migrations wrapped in `try/except sqlite3.OperationalError`. Phase 5 adds the `positions` table inside that same `executescript()` block.
- Existing tables: `recommendations`, `trades`, `analyst_cache`.

`database/queries.py` establishes these invariants:
- Every function: `conn = get_connection(db_path)` → execute → `conn.commit()` (writes) → `conn.close()`.
- SELECT functions return `sqlite3.Row` or `list[sqlite3.Row]` (never raw tuples) because `row_factory = sqlite3.Row` is set at connection level. Dict-style access (`row["column"]`) works on all returned rows.
- `INSERT` functions return `cursor.lastrowid`.
- Upsert pattern established: `INSERT OR REPLACE INTO analyst_cache` (used for cached analysis). Phase 5 can use `INSERT OR IGNORE` + conditional `UPDATE` or a single `INSERT OR REPLACE` for the upsert-position path.

### Discord Slash Command (HIGH confidence — direct file inspection)

`discord_bot/bot.py` registers slash commands in `setup_hook()`:
```python
async def setup_hook(self):
    self.tree.add_command(
        app_commands.Command(
            name="scan",
            description="Trigger an immediate stock scan",
            callback=self._scan_command,
        )
    )
    await self.tree.sync()
```

`_scan_command` is a regular `async def` method on `TradingBot` taking `(self, interaction: discord.Interaction)`. The `/positions` command follows the exact same pattern — add to `setup_hook`, define callback as a method.

**Important:** `tree.sync()` is called once in `setup_hook`. Adding `/positions` to the same `setup_hook` call means no second sync is needed. Both commands will be registered in one sync.

### Approve Handler (HIGH confidence — direct file inspection)

`ApproveRejectView.approve` in `bot.py` lines 47-74:
```python
async def approve(self, interaction, button):
    shares = compute_share_quantity(self.price, self.config.max_position_size_usd)
    if shares == 0:            # early return guard
        ...return
    order_id = None
    if not self.config.dry_run:
        order_id = place_order(...)
    queries.create_trade(...)
    queries.update_recommendation_status(..., "approved")
    await interaction.response.send_message(...)
    self.stop()
```

**Injection points for Phase 5:**
1. **Exposure check** — after `shares = compute_share_quantity(...)` and before `place_order`. Calculate `exposure = shares * self.price`. Query current open positions total. If `existing_exposure + exposure > config.max_position_size_usd * N` (or some threshold), send ephemeral warning and return.
2. **Position upsert** — after `queries.create_trade(...)`, before `update_recommendation_status`. Call `queries.upsert_position(db_path, ticker, shares, price)`.

The `self.config` object is already available on the view, so no signature changes are needed.

### run_scan Loop (HIGH confidence — direct file inspection)

`main.py` line 72, the per-ticker loop guard pattern:
```python
for ticker in universe:
    if queries.ticker_recommended_today(config.db_path, ticker):
        continue
```

POS-06 adds a second guard immediately after:
```python
    if queries.has_open_position(config.db_path, ticker):
        continue
```

This is a pure boolean query (same shape as `ticker_recommended_today`) and fits the existing pattern perfectly.

### Config (HIGH confidence — direct file inspection)

`config.py` already has `max_position_size_usd: float` (default 500.0). No new config fields are required for Phase 5. The exposure check uses this existing field.

---

## DB Schema Design

### positions Table DDL

```sql
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL UNIQUE,
    shares       REAL NOT NULL,
    avg_cost_usd REAL NOT NULL,
    entry_date   TEXT NOT NULL DEFAULT (date('now')),
    status       TEXT NOT NULL DEFAULT 'open',
    last_price   REAL,
    last_updated TEXT
);
```

**Design decisions:**
- `ticker TEXT NOT NULL UNIQUE`: One open position per ticker. The UNIQUE constraint enforces POS-06's intent at the DB level. Re-buys UPDATE the existing row rather than inserting a new one.
- `status TEXT`: values are `'open'` and `'closed'`. Phase 6 (sell signals) will set status to `'closed'` via `close_position()`.
- `last_price REAL`: nullable — populated by `get_position_summary` in `screener/positions.py` when `/positions` is called; not populated at trade time. This avoids requiring a yfinance call during the approve flow.
- `last_updated TEXT`: ISO datetime string, set when `last_price` is refreshed.
- `entry_date TEXT`: records the date of first buy. On re-buy, this is NOT updated (it reflects original entry).

**Migration approach** — follows the Phase 1 pattern exactly:

In `initialize_db`, add to the `executescript()` block:
```sql
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL UNIQUE,
    shares       REAL NOT NULL,
    avg_cost_usd REAL NOT NULL,
    entry_date   TEXT NOT NULL DEFAULT (date('now')),
    status       TEXT NOT NULL DEFAULT 'open',
    last_price   REAL,
    last_updated TEXT
);
```

No `ALTER TABLE` migration needed because this is a new table, not a column addition to an existing table. `CREATE TABLE IF NOT EXISTS` is idempotent — safe to run on both new and existing DBs.

---

## Integration Points

### 1. database/models.py — initialize_db

Add `CREATE TABLE IF NOT EXISTS positions (...)` inside the existing `executescript()` string, after the `analyst_cache` block. No other change needed.

### 2. database/queries.py — four new functions

**create_position** — insert a new position row:
```python
def create_position(db_path: str, ticker: str, shares: float, avg_cost_usd: float) -> int:
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO positions (ticker, shares, avg_cost_usd)
           VALUES (?, ?, ?)""",
        (ticker, shares, avg_cost_usd),
    )
    conn.commit()
    pos_id = cursor.lastrowid
    conn.close()
    return pos_id
```

**update_position** — weighted average cost on re-buy:
```python
def update_position(db_path: str, ticker: str, new_shares: float, buy_price: float) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """UPDATE positions
           SET shares       = shares + ?,
               avg_cost_usd = (shares * avg_cost_usd + ? * ?) / (shares + ?)
           WHERE ticker = ? AND status = 'open'""",
        (new_shares, new_shares, buy_price, new_shares, ticker),
    )
    conn.commit()
    conn.close()
```

**get_open_positions** — returns all open rows:
```python
def get_open_positions(db_path: str) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_date ASC"
    ).fetchall()
    conn.close()
    return rows
```

**has_open_position** — boolean guard for run_scan (POS-06):
```python
def has_open_position(db_path: str, ticker: str) -> bool:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT id FROM positions WHERE ticker = ? AND status = 'open'",
        (ticker,),
    ).fetchone()
    conn.close()
    return row is not None
```

**close_position** — for Phase 6 sell approval (stub it now so Phase 6 has a clean hook):
```python
def close_position(db_path: str, ticker: str) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE positions SET status = 'closed' WHERE ticker = ? AND status = 'open'",
        (ticker,),
    )
    conn.commit()
    conn.close()
```

**upsert_position** — called from approve handler; dispatches to create or update:
```python
def upsert_position(db_path: str, ticker: str, shares: float, price: float) -> None:
    if has_open_position(db_path, ticker):
        update_position(db_path, ticker, shares, price)
    else:
        create_position(db_path, ticker, shares, price)
```

### 3. discord_bot/bot.py — ApproveRejectView.approve

**Exposure check injection** (after shares computed, before trade):
```python
# POS-05: exposure guard
new_exposure = shares * self.price
existing = queries.get_open_positions(self.config.db_path)
existing_total = sum(p["shares"] * (p["last_price"] or p["avg_cost_usd"]) for p in existing)
if existing_total + new_exposure > self.config.max_position_size_usd:
    await interaction.response.send_message(
        f"Warning: buying {shares} share(s) of {self.ticker} at ${self.price:.2f} "
        f"(${new_exposure:.0f}) would exceed MAX_POSITION_SIZE_USD "
        f"(current exposure: ${existing_total:.0f}). Rejected.",
        ephemeral=True,
    )
    return
```

**Position upsert injection** (after create_trade, before update_recommendation_status):
```python
queries.upsert_position(self.config.db_path, self.ticker, shares, self.price)
```

**Import addition** at top of bot.py — no new imports needed; `queries` already imported.

### 4. main.py — run_scan loop

Add after the `ticker_recommended_today` check (line 72):
```python
if queries.has_open_position(config.db_path, ticker):
    logger.debug("Skipping %s: open position exists", ticker)
    continue
```

No imports needed; `queries` already imported.

### 5. discord_bot/bot.py — setup_hook + /positions command

```python
async def setup_hook(self):
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

async def _positions_command(self, interaction: discord.Interaction):
    from screener.positions import get_position_summary
    summaries = await asyncio.to_thread(
        get_position_summary, self.config.db_path
    )
    embed = build_positions_embed(summaries)
    await interaction.response.send_message(embed=embed)
```

---

## Discord Slash Command Pattern

### How /scan Is Registered (canonical reference)

`setup_hook` is called once by discord.py before `on_ready`. It:
1. Calls `self.tree.add_command(app_commands.Command(name=..., description=..., callback=...))` for each command.
2. Calls `await self.tree.sync()` once to push all commands to Discord's API.

The callback must be an `async def` taking `(self, interaction: discord.Interaction)`. No `@app_commands.command()` decorator is used here — commands are registered procedurally via `add_command`.

### /positions Specifics

- Command name: `"positions"`
- Response: `await interaction.response.send_message(embed=embed)` — non-ephemeral so all channel members can see current holdings.
- If no open positions: send a plain message `"No open positions."` instead of an empty embed.
- The yfinance price fetch happens inside `get_position_summary` which is called via `asyncio.to_thread` to avoid blocking the Discord event loop (same pattern as `run_scan` uses for `analyze_ticker`).

---

## P&L Calculation

### screener/positions.py — new module

```python
import yfinance as yf
from database.queries import get_open_positions


def get_position_summary(db_path: str) -> list[dict]:
    """
    Fetch current price for each open position via yfinance and compute P&L%.
    Returns list of dicts: ticker, shares, avg_cost_usd, current_price, pnl_pct.
    """
    positions = get_open_positions(db_path)
    results = []
    for pos in positions:
        ticker = pos["ticker"]
        try:
            current_price = yf.Ticker(ticker).fast_info.last_price
        except Exception:
            current_price = pos["last_price"]  # fall back to last known price
        pnl_pct = None
        if current_price and pos["avg_cost_usd"]:
            pnl_pct = (current_price - pos["avg_cost_usd"]) / pos["avg_cost_usd"]
        results.append({
            "ticker": ticker,
            "shares": pos["shares"],
            "avg_cost_usd": pos["avg_cost_usd"],
            "current_price": current_price,
            "pnl_pct": pnl_pct,
        })
    return results
```

### yfinance fast_info

`yf.Ticker(ticker).fast_info.last_price` is the correct lightweight attribute for a single real-time price. It does not require downloading full history. This is consistent with how `fetch_fundamental_info` and `fetch_technical_data` use `yf.Ticker` objects in the existing screener modules.

P&L formula:
```python
pnl_pct = (current_price - avg_cost_usd) / avg_cost_usd  # e.g., 0.05 = +5%
```

### build_positions_embed — discord_bot/embeds.py

```python
def build_positions_embed(summaries: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="Open Positions", color=discord.Color.blurple())
    for s in summaries:
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
    return embed
```

Discord embeds support up to 25 fields. For a single-operator bot with at most 10-20 tickers in scope this is not a concern.

---

## Exposure Check

### Where to Inject (POS-05)

The check must run **after** `compute_share_quantity` returns `shares` (so we know the dollar value of the proposed buy) and **before** `place_order` or `create_trade` (so no trade is recorded if we reject).

In `ApproveRejectView.approve`, the insertion point is after the `shares == 0` guard at line 49, before `order_id = None` at line 56.

### Exposure Calculation

```python
new_exposure = shares * self.price
existing_positions = queries.get_open_positions(self.config.db_path)
existing_total_usd = sum(
    p["shares"] * (p["last_price"] or p["avg_cost_usd"])
    for p in existing_positions
)
```

Use `last_price` if available (most recently refreshed), fall back to `avg_cost_usd` (conservative). This avoids a live yfinance call in the approve handler's hot path.

**Threshold semantics:** The check compares `existing_total_usd + new_exposure > config.max_position_size_usd`. This treats `MAX_POSITION_SIZE_USD` as a **total portfolio exposure cap**, not a per-position size. This matches the roadmap description: "check combined position exposure vs MAX_POSITION_SIZE_USD."

**Warning behavior:** Send an ephemeral message (visible only to the user who clicked Approve) and return early. Do NOT block the view from being used again — do NOT call `self.stop()` so the user can still click Reject to dismiss.

**Dry-run behavior:** Exposure check runs regardless of `dry_run` flag — it is a safety guard that applies equally in both modes.

---

## Risks and Edge Cases

### 1. Re-buy Weighted Average Cost (Critical)

The SQL UPDATE for avg cost must reference the row's own `shares` column atomically:

```sql
UPDATE positions
SET shares       = shares + :new_shares,
    avg_cost_usd = (shares * avg_cost_usd + :new_shares * :price) / (shares + :new_shares)
WHERE ticker = :ticker AND status = 'open'
```

This works because SQLite evaluates the old column values (before the SET) when computing the new values within a single UPDATE statement. No read-then-write race condition.

**Test required:** upsert_position called twice on same ticker — verify final avg_cost_usd matches the weighted average formula.

### 2. ticker UNIQUE Constraint

The `ticker UNIQUE` constraint means `create_position` will raise `sqlite3.IntegrityError` if called for a ticker that already has an open position. `upsert_position` calls `has_open_position` first to route to `update_position` instead. Always go through `upsert_position` from the approve handler — never call `create_position` directly.

### 3. Closed Position Re-buy

If a position for ticker X was previously closed (status='closed'), and the bot recommends X again (POS-06's skip applies only to status='open'), the user can approve a new buy. `has_open_position` checks `status = 'open'`, so it correctly returns False for closed positions. `create_position` will fail due to UNIQUE constraint because the old row still exists.

**Resolution:** Change `create_position` to use `INSERT OR REPLACE` or add a query `get_position_by_ticker(db_path, ticker)` and branch: if exists but closed → UPDATE status='open', reset shares/avg_cost/entry_date; if not exists → INSERT. The UNIQUE constraint on ticker means we can use:

```sql
INSERT INTO positions (ticker, shares, avg_cost_usd, entry_date, status)
VALUES (?, ?, ?, date('now'), 'open')
ON CONFLICT(ticker) DO UPDATE SET
    shares       = excluded.shares,
    avg_cost_usd = excluded.avg_cost_usd,
    entry_date   = date('now'),
    status       = 'open',
    last_price   = NULL,
    last_updated = NULL
```

This handles both new tickers and re-entry on previously closed positions.

**Note on ON CONFLICT syntax:** SQLite supports `ON CONFLICT(column) DO UPDATE` (upsert) since version 3.24.0 (2018). Python's bundled SQLite is well above this threshold.

### 4. Dry-Run Position Creation

In dry-run mode, `place_order` is not called but `create_trade` is still called (with `order_id=None`). Phase 5 must replicate this: `upsert_position` runs regardless of `dry_run`. This is correct — tracking positions in dry-run is essential for testing the `/positions` command without live trading.

### 5. Exposure Check With No Last Price

If `last_price` is NULL (position just created, `/positions` never called), fall back to `avg_cost_usd` for the exposure calc. This gives a conservative estimate (cost basis, not current market value). Document this in the function docstring.

### 6. Discord embed field limit

Discord embeds allow max 25 fields. For a single-operator bot with a universe of ~20 tickers, the maximum possible open positions is ~20 (can't hold more than the universe). Safe in practice. Add a note to `build_positions_embed` docstring that >25 positions will silently truncate.

### 7. run_scan POS-06 and ticker_recommended_today interaction

If a ticker already has an open position AND a recommendation from today, both guards fire. The `has_open_position` check is added second. This is fine — the earlier `ticker_recommended_today` guard catches today's duplicates; `has_open_position` catches the "already own it" case for tickers that were not recommended today. Order matters: `ticker_recommended_today` first (cheap check), `has_open_position` second (also cheap — single indexed lookup).

---

## Validation Architecture

`workflow.nyquist_validation` is not set in `.planning/config.json` (key absent), so validation is enabled.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing, see CLAUDE.md) |
| Config file | none (pytest auto-discovery) |
| Quick run command | `pytest tests/test_positions.py -v` |
| Full suite command | `pytest` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| POS-01 | positions table created by initialize_db | unit | `pytest tests/test_positions.py::test_initialize_db_creates_positions_table -v` | Wave 0 |
| POS-02 | upsert_position creates on first buy | unit | `pytest tests/test_positions.py::test_upsert_position_creates_new -v` | Wave 0 |
| POS-02 | upsert_position updates avg cost on re-buy | unit | `pytest tests/test_positions.py::test_upsert_position_updates_avg_cost -v` | Wave 0 |
| POS-02 | approve handler calls upsert_position | unit | `pytest tests/test_positions.py::test_approve_upserts_position -v` | Wave 0 |
| POS-02 | re-entry after close creates fresh position | unit | `pytest tests/test_positions.py::test_create_position_after_close -v` | Wave 0 |
| POS-03 | get_open_positions returns only open rows | unit | `pytest tests/test_positions.py::test_get_open_positions -v` | Wave 0 |
| POS-03 | get_position_summary returns correct P&L% | unit | `pytest tests/test_positions.py::test_get_position_summary_pnl -v` | Wave 0 |
| POS-04 | /positions command returns embed | unit | `pytest tests/test_positions.py::test_positions_command_sends_embed -v` | Wave 0 |
| POS-04 | /positions returns "no positions" on empty | unit | `pytest tests/test_positions.py::test_positions_command_empty -v` | Wave 0 |
| POS-05 | exposure check blocks over-limit approval | unit | `pytest tests/test_positions.py::test_approve_exposure_guard_blocks -v` | Wave 0 |
| POS-05 | exposure check allows under-limit approval | unit | `pytest tests/test_positions.py::test_approve_exposure_guard_allows -v` | Wave 0 |
| POS-06 | run_scan skips ticker with open position | unit | `pytest tests/test_positions.py::test_run_scan_skips_open_position -v` | Wave 0 |

### What to Mock

Following the pattern established in `test_discord_buttons.py`:
- `queries` is patched as a module-level mock in button handler tests (`@patch("discord_bot.bot.queries")`)
- `place_order` is patched where needed
- DB tests use a real temp file DB (like `test_database.py` with `fresh_db` fixture using a named temp file and `os.remove` teardown)
- yfinance calls in `get_position_summary` tests: patch `yf.Ticker` with a MagicMock where `fast_info.last_price` returns a known float

### Key Assertions

1. `test_upsert_position_updates_avg_cost`: buy 5 shares at $100, then buy 5 more at $120 → `avg_cost_usd == 110.0`
2. `test_approve_exposure_guard_blocks`: set up existing position totaling $490, attempt new buy of $100 → interaction.response.send_message called with ephemeral=True, create_trade NOT called
3. `test_approve_upserts_position`: mock `queries.upsert_position`, approve a buy → assert called with correct (db_path, ticker, shares, price)
4. `test_run_scan_skips_open_position`: mock `queries.has_open_position` to return True for one ticker → assert that ticker never reaches `fetch_fundamental_info`

### Wave 0 Gaps

- [ ] `tests/test_positions.py` — all 12 test cases above
- [ ] `screener/positions.py` — new module (needed for /positions command and its test)

No framework gaps — pytest already installed, conftest.py and fixtures established.

---

## Architecture Patterns

### Recommended New Module Structure

```
screener/
└── positions.py         # get_position_summary(db_path) -> list[dict]

database/
├── models.py            # add positions DDL to executescript
└── queries.py           # add 6 new functions

discord_bot/
├── bot.py               # inject exposure check + upsert, add /positions command
└── embeds.py            # add build_positions_embed

tests/
└── test_positions.py    # all POS-01 through POS-06 tests
```

### Anti-Patterns to Avoid

- **Opening the DB connection in screener/positions.py directly**: all DB access must go through `database/queries.py` using `get_connection`. `screener/positions.py` calls `get_open_positions(db_path)` from queries, not sqlite3 directly.
- **Calling `tree.sync()` twice**: adding the new command in `setup_hook` alongside `/scan` and syncing once is correct. Do not create a separate `sync()` call elsewhere.
- **Blocking the event loop with yfinance in /positions handler**: always use `asyncio.to_thread(get_position_summary, db_path)` as established in `run_scan` for `analyze_ticker`.
- **Using `entry_price` as a column name in the DDL**: The ROADMAP mentions `entry_price` in the schema but it is redundant with `avg_cost_usd` for a single-lot buy. `avg_cost_usd` IS the entry price for a single buy. The REQUIREMENTS table only specifies `avg_cost_usd` as the meaningful column. Omit `entry_price` to avoid confusion.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Current stock price | Custom HTTP to Yahoo Finance | `yf.Ticker(t).fast_info.last_price` | Already used project-wide; handles rate limiting |
| Weighted avg cost | Manual read-modify-write | Single SQL UPDATE with inline arithmetic | Atomic, no race condition |
| Upsert on UNIQUE conflict | `SELECT` then `INSERT` or `UPDATE` | `INSERT ... ON CONFLICT(ticker) DO UPDATE` | One round trip, no TOCTOU |
| Discord command registration | Custom event dispatch | `app_commands.Command` + `tree.sync()` | Already established in setup_hook |

---

## Common Pitfalls

### Pitfall 1: Calling create_position on a closed ticker
**What goes wrong:** `sqlite3.IntegrityError: UNIQUE constraint failed: positions.ticker`
**Why it happens:** The `ticker UNIQUE` constraint is not scoped to `status='open'`. A closed position row still occupies the unique slot.
**How to avoid:** Use the `ON CONFLICT(ticker) DO UPDATE` upsert DDL in `create_position`. Never INSERT directly from outside `upsert_position`.
**Warning signs:** `IntegrityError` in the approve handler logs.

### Pitfall 2: Blocking the event loop in /positions handler
**What goes wrong:** The bot freezes during `/positions` if yfinance takes 2-5 seconds per ticker.
**Why it happens:** `interaction.response` has a 3-second acknowledgement deadline. Synchronous yfinance calls inside an async handler block the event loop.
**How to avoid:** Wrap `get_position_summary` in `asyncio.to_thread(...)` before sending the response. Or use `interaction.response.defer()` then `interaction.followup.send(embed=embed)`.
**Warning signs:** Discord shows "The application did not respond" on the slash command.

### Pitfall 3: Exposure check using stale market values
**What goes wrong:** Existing positions have `last_price=NULL` (never refreshed), so exposure is underestimated.
**Why it happens:** `last_price` is only populated when `/positions` is called.
**How to avoid:** Fall back to `avg_cost_usd` when `last_price` is NULL. Document that the check is conservative (cost basis), not mark-to-market.
**Warning signs:** New buy approved even though total exposure visually exceeds limit.

### Pitfall 4: tree.sync() after setup_hook
**What goes wrong:** `/positions` command not visible in Discord; users see "Unknown command."
**Why it happens:** If the command is added after `setup_hook` completes (e.g., in `on_ready`), the sync already happened without it.
**How to avoid:** Add `/positions` command registration inside `setup_hook` alongside `/scan`, before the single `await self.tree.sync()` call.
**Warning signs:** Command not in Discord's autocomplete list.

---

## Environment Availability

Step 2.6: SKIPPED — Phase 5 is purely code/config changes using libraries already installed (yfinance, discord.py, sqlite3). No new external dependencies introduced.

---

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection: `database/models.py` — SQLite schema, migration, get_connection pattern
- Direct codebase inspection: `database/queries.py` — all CRUD functions, Row factory pattern
- Direct codebase inspection: `discord_bot/bot.py` — ApproveRejectView, setup_hook, tree.sync pattern
- Direct codebase inspection: `discord_bot/embeds.py` — embed building pattern
- Direct codebase inspection: `schwab_client/orders.py` — place_order signature and dry-run behavior
- Direct codebase inspection: `main.py` — run_scan loop, guard pattern, asyncio.to_thread usage
- Direct codebase inspection: `config.py` — max_position_size_usd field location
- Direct codebase inspection: `tests/test_discord_buttons.py` — button handler test patterns
- Direct codebase inspection: `tests/test_database.py` — DB test fixture pattern

### Secondary (MEDIUM confidence)
- SQLite documentation: ON CONFLICT upsert syntax available since SQLite 3.24.0 (2018-06-04) — Python's bundled SQLite exceeds this on all supported Python versions

---

## Metadata

**Confidence breakdown:**
- DB schema: HIGH — exact DDL modeled on existing tables in models.py
- Integration points: HIGH — line numbers and function signatures verified from source
- Slash command pattern: HIGH — exact pattern extracted from working /scan implementation
- P&L calculation: HIGH — yfinance fast_info.last_price is the correct lightweight attribute
- Weighted avg cost SQL: HIGH — standard SQL UPDATE self-referencing pattern, atomic in SQLite
- Exposure check logic: HIGH — straightforward arithmetic against existing config field
- Re-entry edge case: HIGH — analyzed UNIQUE constraint behavior, ON CONFLICT syntax documented

**Research date:** 2026-04-03
**Valid until:** 2026-05-03 (stable codebase, no external API changes affect this)
