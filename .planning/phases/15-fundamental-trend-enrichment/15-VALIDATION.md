---
phase: 15
slug: fundamental-trend-enrichment
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-21
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | none (pytest auto-discovery) |
| **Quick run command** | `pytest tests/test_screener_fundamentals.py tests/test_analyst_claude.py -x -q` |
| **Full suite command** | `pytest -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_screener_fundamentals.py tests/test_analyst_claude.py -x -q`
- **After every plan wave:** Run `pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 15-01-01 | 01 | 1 | SIG-08 | — | N/A | unit | `pytest tests/test_screener_fundamentals.py -k "fetch_eps" -x -q` | Wave 0 (new tests) | ⬜ pending |
| 15-01-02 | 01 | 1 | SIG-07 | — | N/A | unit | `pytest tests/test_analyst_claude.py -k "fundamental_trend or pe_direction or eps" -x -q` | Wave 0 (new tests) | ⬜ pending |
| 15-01-03 | 01 | 1 | SIG-07, SIG-08 | — | N/A | unit | `pytest tests/test_analyst_claude.py -k "backward_compat" -x -q` | ✅ existing pattern | ⬜ pending |
| 15-01-04 | 01 | 2 | SIG-07, SIG-08 | — | N/A | integration | `pytest -q` | ✅ existing | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_screener_fundamentals.py` — add `fetch_eps_data` tests: None stmt, missing "Diluted EPS" row, NaN filtering, valid 4-quarter return, chronological order verification
- [ ] `tests/test_analyst_claude.py` — add `fundamental_trend` fixture variants: present with full trend, pe_direction only (eps None), backward compat (no arg)

*(Existing test infrastructure is in place — only new test functions needed, no new files or framework setup)*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Cache hit skips fundamental_trend re-build | SIG-07, SIG-08 | Cache path bypasses analyze_ticker; unit tests can't verify prompt content for cache hits | Trigger a duplicate scan for the same ticker within cache window; confirm no crash and prior recommendation is reused |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
