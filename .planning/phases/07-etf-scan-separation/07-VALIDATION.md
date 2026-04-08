---
phase: 07
slug: etf-scan-separation
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-08
---

# Phase 07 — Validation Strategy

> Per-phase validation contract — reconstructed from SUMMARY artifacts (State B) and gap-filled on 2026-04-08.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + pytest-asyncio |
| **Config file** | none (inline markers) |
| **Quick run command** | `pytest tests/test_screener_universe.py tests/test_database.py tests/test_analyst_claude.py tests/test_discord_embeds.py tests/test_main.py tests/test_run_scan.py tests/test_discord_bot.py -v` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~14 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick run command
- **After every plan wave:** Run `pytest`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | ETF-01 | T-07-01 / T-07-02 | partition_watchlist classifies via yfinance quoteType, falls back to allowlist on exception | unit | `pytest tests/test_screener_universe.py::test_partition_watchlist_classifies_via_yfinance tests/test_screener_universe.py::test_partition_watchlist_falls_back_to_allowlist_on_exception tests/test_screener_universe.py::test_partition_watchlist_handles_mixed_availability tests/test_screener_universe.py::test_partition_watchlist_empty_input -v` | ✅ | ✅ green |
| 07-01-02 | 01 | 1 | ETF-01 | T-07-03 | etf_watchlist.txt readable via get_watchlist | unit | `pytest tests/test_screener_universe.py::test_get_watchlist_reads_etf_watchlist -v` | ✅ | ✅ green |
| 07-01-03 | 01 | 1 | ETF-04 / ETF-05 | T-07-04 / T-07-05 | asset_type column stored and retrievable; defaults to "stock" | unit | `pytest tests/test_database.py::test_create_recommendation_with_asset_type_etf tests/test_database.py::test_create_recommendation_default_asset_type -v` | ✅ | ✅ green |
| 07-02-01 | 02 | 2 | ETF-03 | T-07-06 | build_etf_prompt includes RSI/MACD/expense_ratio, excludes stock fundamentals, None-safe | unit | `pytest tests/test_analyst_claude.py::test_build_etf_prompt_includes_ticker_and_technical_data tests/test_analyst_claude.py::test_build_etf_prompt_excludes_stock_fundamentals tests/test_analyst_claude.py::test_build_etf_prompt_expense_ratio_none_shows_na tests/test_analyst_claude.py::test_build_etf_prompt_macd_none_shows_na tests/test_analyst_claude.py::test_build_etf_prompt_empty_headlines_shows_no_headlines_message tests/test_analyst_claude.py::test_build_etf_prompt_includes_signal_format_instruction -v` | ✅ | ✅ green |
| 07-02-02 | 02 | 2 | ETF-07 | T-07-06 | analyze_etf_ticker returns {signal, reasoning, provider_used}, uses build_etf_prompt not build_prompt | unit | `pytest tests/test_analyst_claude.py::test_analyze_etf_ticker_returns_correct_shape tests/test_analyst_claude.py::test_analyze_etf_ticker_calls_build_etf_prompt_not_build_prompt -v` | ✅ | ✅ green |
| 07-02-03 | 02 | 2 | ETF-07 | T-07-08 | analyze_etf_ticker falls back to fallback_client on primary API failure | unit | `pytest tests/test_analyst_claude.py::test_analyze_etf_ticker_uses_fallback_on_primary_failure -v` | ✅ | ✅ green |
| 07-02-04 | 02 | 2 | ETF-07 | T-07-07 | build_etf_recommendation_embed produces [ETF] title, correct colors, None-safe fields | unit | `pytest tests/test_discord_embeds.py::test_etf_embed_buy_signal_is_green tests/test_discord_embeds.py::test_etf_embed_has_required_fields tests/test_discord_embeds.py::test_etf_embed_expense_ratio_none_shows_na tests/test_discord_embeds.py::test_etf_embed_rsi_none_shows_na tests/test_discord_embeds.py::test_etf_embed_title_format tests/test_discord_embeds.py::test_etf_embed_hold_signal_is_yellow -v` | ✅ | ✅ green |
| 07-03-01 | 03 | 3 | ETF-02 | T-07-09 / T-07-10 | run_scan_etf posts BUY, skips non-BUY, skips already-recommended, sends ops alert on 0 recs | integration | `pytest tests/test_main.py::test_run_scan_etf_posts_buy_recommendation tests/test_main.py::test_run_scan_etf_skips_non_buy tests/test_main.py::test_run_scan_etf_skips_already_recommended tests/test_main.py::test_run_scan_etf_zero_recs_sends_ops_alert -v` | ✅ | ✅ green |
| 07-03-02 | 03 | 3 | ETF-02 | T-07-09 | /scan_etf Discord command triggers _scan_etf_callback; no crash when callback is None | unit | `pytest tests/test_discord_bot.py::test_scan_etf_command_creates_task_when_callback_set tests/test_discord_bot.py::test_scan_etf_command_does_nothing_when_no_callback -v` | ✅ | ✅ green |
| 07-03-03 | 03 | 3 | ETF-02 | T-07-09 | send_etf_recommendation posts ETF embed with ApproveRejectView and returns message id | unit | `pytest tests/test_discord_bot.py::test_send_etf_recommendation_posts_embed_and_returns_message_id -v` | ✅ | ✅ green |
| 07-03-04 | 03 | 3 | ETF-06 | T-07-11 | run_scan excludes ETF tickers from stock scan universe via partition_watchlist | integration | `pytest tests/test_run_scan.py::test_run_scan_excludes_etfs_from_stock_universe -v` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. pytest + pytest-asyncio were already installed prior to this phase.

---

## Manual-Only Verifications

All phase behaviors have automated verification.

---

## Validation Audit 2026-04-08

| Metric | Count |
|--------|-------|
| Gaps found | 4 |
| Resolved | 4 |
| Escalated | 0 |

**Gaps filled:**
- G-1: `_scan_etf_command` Discord handler (2 tests → `tests/test_discord_bot.py`)
- G-2: `send_etf_recommendation` bot method (1 test → `tests/test_discord_bot.py`)
- G-3: `run_scan` ETF-filtering via `partition_watchlist` (1 test → `tests/test_run_scan.py`)
- G-4: `analyze_etf_ticker` fallback provider path (1 test → `tests/test_analyst_claude.py`)

---

## Validation Sign-Off

- [x] All tasks have automated verify
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (N/A — no Wave 0 needed)
- [x] No watch-mode flags
- [x] Feedback latency < 20s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-04-08
