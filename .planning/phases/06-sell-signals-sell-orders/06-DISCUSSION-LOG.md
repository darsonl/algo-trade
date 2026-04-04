# Phase 6 Discussion Log

**Date:** 2026-04-04
**Command:** /gsd:discuss-phase 6

---

## Area: Sell Trigger Gates

**Q: For a SELL recommendation to be posted, what must be true?**
Options: Both required / Either condition sufficient / Claude only (no RSI gate)
→ **User:** RSI > MAX_RSI first, then use the same analyzer and fallback analyzer from buy steps.
  Specifically called out: reuse Gemini free models, respect API rate limits, use same mechanism.

**Q: RSI threshold — same MAX_RSI or separate SELL_RSI_THRESHOLD?**
Options: Separate SELL_RSI_THRESHOLD (Recommended) / Reuse MAX_RSI
→ **User:** Separate SELL_RSI_THRESHOLD

---

## Area: Post-Rejection Behavior

**Q: After clicking Reject on a SELL, when can that position generate another SELL rec?**
Options: 24h cooldown / Never until RSI drops back below threshold / Immediately
→ **User:** Never until RSI drops back below threshold

**Q: Tracking 'RSI dropped back' requires storing state. Which approach?**
Options: sell_blocked flag on positions table (Recommended) / Query rejected recs
→ **User:** sell_blocked flag on positions table

---

## Area: Sell Prompt Richness

**Q: What context does the sell analyst prompt include beyond ticker + headlines?**
Options: Full position context (Recommended) / Minimal — news only
→ **User:** Full position context (entry price, current price, P&L%, hold duration, RSI)

---

## Area: Stop-Loss / Take-Profit

**Q: Include stop-loss / take-profit auto-triggers in Phase 6?**
Options: Defer to a later phase / Include in Phase 6
→ **User:** Defer to a later phase

---

*SELL-10, SELL-11, SELL-12 deferred. Phase 6 scope = SELL-01 to SELL-09.*
