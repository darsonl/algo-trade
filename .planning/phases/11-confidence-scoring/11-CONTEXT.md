# Phase 11: Confidence Scoring — Context

**Gathered:** 2026-04-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Add a `high/medium/low` confidence level to every Claude BUY/SELL/HOLD signal. Confidence is instructed in the prompt, parsed from the response, persisted to the database, and displayed as an optional badge in Discord embeds. A missing or unparseable confidence value must not break the embed or the scan.

Requirements in scope: SIG-04.
Out of scope: behavioral changes based on confidence (e.g. auto-rejecting low-confidence signals), confidence filtering in scan logic, confidence in `/positions` embed.
</domain>

<decisions>
## Implementation Decisions

### Prompt Format

- **D-01:** All three prompt builders (`build_prompt`, `build_sell_prompt`, `build_etf_prompt`) add `CONFIDENCE: <high|medium|low>` as a **required** 3rd line in the response format instruction.
  - Rationale: LLMs follow strict format rules consistently; making it required maximizes actual output frequency.
  - Parse failure handling: `parse_claude_response()` checks for the `CONFIDENCE:` line. If absent or the value is not `high`/`medium`/`low`, `confidence` key is set to `None` — no `ValueError` raised. This satisfies success criterion 2 (missing confidence never breaks the scan).

Prompt format instruction (all signal types):
```
Respond with exactly this format (no extra text):
SIGNAL: <BUY|HOLD|SKIP>
REASONING: <2-3 sentences explaining your decision>
CONFIDENCE: <high|medium|low>
```

### Badge Placement in Embeds

- **D-02:** Confidence appears as an **inline embed field** — consistent with the existing Price/P-E/Dividend Yield field pattern.
  - Called only when confidence is not `None`: `embed.add_field(name="Confidence", value=confidence.capitalize(), inline=True)`
  - Applies to all three embed builders: `build_recommendation_embed`, `build_sell_embed`, `build_etf_recommendation_embed`.
  - When confidence is `None`, the field is simply not added — no "N/A" placeholder.

### Signal Scope

- **D-03:** All signal paths get confidence: BUY/HOLD/SKIP (stock), SELL, and ETF. All three prompt builders and all three embed builders are updated consistently.

### Database — recommendations table

- **D-04:** Add a nullable `confidence TEXT` column to the `recommendations` table via `ALTER TABLE` try/except migration (matching the existing migration pattern in `initialize_db`).
  - `save_recommendation()` in `queries.py` accepts an optional `confidence` parameter.
  - `confidence` values stored as lowercase: `"high"`, `"medium"`, `"low"`, or `None`.

### Database — analyst_cache table

- **D-05:** Add a nullable `confidence TEXT` column to `analyst_cache` via `ALTER TABLE` try/except migration.
  - `get_cached_analysis()` returns `{signal, reasoning, confidence}` — `confidence` may be `None` for rows written before this migration.
  - `cache_analyst_result()` accepts an optional `confidence` parameter; stores `None` if not provided.
  - Rationale: Cache hits must produce the same badge behavior as live API calls. Without this, repeated tickers within the same day would silently lose their confidence badge.

### Return Value of analyze_ticker / analyze_etf_ticker

- **D-06:** Both `analyze_ticker()` and `analyze_etf_ticker()` return `{signal, reasoning, provider_used, confidence}` — `confidence` may be `None`. No breaking change to callers that ignore the new key.

### No behavioral changes

- **D-07:** Confidence is display-only. No scan logic, filtering, or ordering changes based on confidence value. The badge informs the human approving in Discord — it does not affect whether a recommendation is posted or approved.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` §SIG-04 — acceptance criteria for confidence scoring

### Key source files
- `analyst/claude_analyst.py` — `build_prompt`, `build_sell_prompt`, `build_etf_prompt`, `parse_claude_response`, `analyze_ticker`, `analyze_etf_ticker`
- `discord_bot/embeds.py` — `build_recommendation_embed`, `build_sell_embed`, `build_etf_recommendation_embed`
- `database/models.py` — `initialize_db`, migration pattern (try/except ALTER TABLE)
- `database/queries.py` — `get_cached_analysis`, `cache_analyst_result`, `save_recommendation`

### Prior context
- `.planning/phases/10-prompt-signal-enrichment/10-CONTEXT.md` — D-06/D-07/D-08 establish the "None = omit" pattern this phase continues; D-09 shows the prompt structure being extended
</canonical_refs>

<deferred>
## Deferred Ideas

None surfaced during discussion.
</deferred>
