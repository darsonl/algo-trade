# Design: SPY MA Signal Enrichment + DXY Context

**Date:** 2026-04-14
**Status:** Approved
**Milestone:** v1.3 (candidate)

---

## Summary

Extend the macro context layer with two new signals injected into all Claude prompts (BUY, SELL, ETF):

1. **SPY 50-day MA** — replaces the existing "SPY trend" 1-month return. Shows price deviation from the 50-day moving average as `Bullish (+3.2%)` or `Bearish (-1.4%)`.
2. **SPY 200-day MA** — new signal in the same format as SPY 50-day MA. Provides long-term trend context.
3. **DXY** — US Dollar Index raw value (e.g., `104.23`). Purely context; no filtering or gating.

---

## Scope

- `screener/macro.py` — data fetching and formatting
- `analyst/claude_analyst.py` — prompt rendering (3 builders)
- `tests/test_macro.py` — updated and new tests

No schema changes. No new config fields. No changes to Discord embeds or the DB.

---

## Architecture

### `screener/macro.py`

**Rename `format_spy_trend` → `format_spy_ma_deviation`**

Same Bullish/Bearish + percentage logic, but now receives a price-vs-MA deviation rather than a 1-month return. Used for both 50-day and 200-day.

```python
def format_spy_ma_deviation(deviation: float) -> str:
    """Format price-vs-MA deviation as a Bullish/Bearish label with percentage.

    Args:
        deviation: (last_close - ma) / ma  (e.g., 0.032 for +3.2% above MA)

    Returns:
        "Bullish (+3.2%)" or "Bearish (-1.4%)"
    """
    if deviation >= 0:
        return f"Bullish (+{deviation * 100:.1f}%)"
    return f"Bearish ({deviation * 100:.1f}%)"
```

**Update `fetch_macro_context`**

- SPY history fetch: `period="1mo"` → `period="1y"` (≥200 trading days needed)
- Compute `ma50` and `ma200` via `rolling(50).mean()` and `rolling(200).mean()` on the Close series
- Both deviations formatted with `format_spy_ma_deviation`
- DXY fetched via `yf.Ticker("DX-Y.NYB")` with `fast_info["lastPrice"]` → history fallback (same pattern as VIX)
- DXY has its own inner `try/except` so a DXY failure does not discard SPY/VIX context

**Return dict (4 keys):**

```python
{
    "spy_50ma":  str | None,   # was "spy_trend"
    "vix_level": str | None,
    "spy_200ma": str | None,
    "dxy":       float | None,
}
```

**Failure contract:**

- Outer `except Exception` catches SPY/VIX failures → all 4 keys return `None`
- Inner `except Exception` around DXY fetch → `dxy=None` only; other keys unaffected

---

### `analyst/claude_analyst.py`

All three prompt builders (`build_prompt`, `build_sell_prompt`, `build_etf_prompt`) updated:

- `macro_context.get("spy_trend")` → `macro_context.get("spy_50ma")`
- Label `"- SPY trend:"` → `"- SPY 50-day MA:"`
- New lines added after SPY 50-day MA, before VIX:

```python
if macro_context.get("spy_50ma"):
    market_lines.append(f"- SPY 50-day MA: {macro_context['spy_50ma']}")
if macro_context.get("spy_200ma"):
    market_lines.append(f"- SPY 200-day MA: {macro_context['spy_200ma']}")
if macro_context.get("vix_level"):
    market_lines.append(f"- VIX: {macro_context['vix_level']}")
if macro_context.get("dxy") is not None:
    market_lines.append(f"- DXY: {macro_context['dxy']:.2f}")
```

Note: `dxy` uses `is not None` guard (not falsy) because `0.0` is a valid float value.

**Resulting Market Context block (BUY/SELL prompts):**
```
Market Context:
- Sector: Technology
- SPY 50-day MA: Bullish (+3.2%)
- SPY 200-day MA: Bullish (+1.1%)
- VIX: 18.4 (Low volatility)
- DXY: 104.23
- 52-week range: Near high (87%)
```

**ETF prompts** include all four macro lines (DXY is particularly relevant for bond/commodity ETFs like GLD, BND). No Sector or 52-week range in ETF prompts (existing constraint D-10 preserved).

---

## Data Flow

```
fetch_macro_context()
  → yf.Ticker("SPY").history(period="1y")
      → rolling(50).mean()  → spy_50ma
      → rolling(200).mean() → spy_200ma
  → yf.Ticker("^VIX").fast_info / history → vix_level
  → yf.Ticker("DX-Y.NYB").fast_info / history → dxy  [inner try/except]

macro_context dict
  → build_prompt()        (BUY path)
  → build_sell_prompt()   (SELL path)
  → build_etf_prompt()    (ETF path)
```

`fetch_macro_context` is already called once per scan in `main.py` and passed down — no callsite changes needed.

---

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| SPY history fetch fails | Outer except → all 4 keys `None` |
| Fewer than 200 SPY rows | `rolling(200).mean().iloc[-1]` returns `NaN` → `spy_200ma=None` |
| VIX fetch fails | Outer except → all 4 keys `None` |
| DXY fetch fails | Inner except → `dxy=None`; SPY + VIX keys unaffected |
| Any key is `None` | Prompt builder skips that line silently (existing guard pattern) |

---

## Tests (`tests/test_macro.py`)

### Updated

| Test | Change |
|------|--------|
| `test_format_spy_trend_*` (×5) | Renamed to `test_format_spy_ma_deviation_*`; import updated |
| `test_fetch_macro_context_returns_correct_shape_on_success` | Mock SPY → 210 rows; assert `spy_50ma`, `spy_200ma`, `dxy` keys; add DXY mock |
| `test_fetch_macro_context_returns_none_values_on_exception` | Expected dict updated to 4-key all-None |
| `test_fetch_macro_context_bearish_spy` | Key names updated + DXY mock |
| `test_fetch_macro_context_vix_fallback_to_history` | Key names updated + DXY mock |

### New

| Test | Asserts |
|------|---------|
| `test_fetch_macro_context_dxy_none_on_dxy_failure` | DXY ticker raises; `dxy=None` but `spy_50ma` and `vix_level` still populated |
| `test_fetch_macro_context_spy_50ma_bullish` | Price above 50-day MA → `"Bullish"` in `spy_50ma` |
| `test_fetch_macro_context_spy_200ma_bearish` | Price below 200-day MA → `"Bearish"` in `spy_200ma` |

**Mock sizing note:** SPY mocks use ≥210 rows so `rolling(200).mean()` produces non-NaN values. Smaller mocks would silently produce `NaN`, making assertions vacuous.

---

## What This Is Not

- No new config fields — DXY and both MAs are always fetched when macro context is enabled
- No Discord embed changes — macro signals stay in Claude prompts only
- No DB schema changes
- No filter/gate logic — all three signals are context only
